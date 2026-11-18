"""配股认购发行核对的纯领域规则。

本模块不接触数据库与 Web，只做确定性算术，便于离线复算与单元测试。
所有金额使用 Decimal，输出量化到分；股数为非负整数。
"""
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from datetime import datetime

CENT = Decimal('0.01')
RIGHTS_RULE_VERSION = 'rights-2026.11.18.1'

# 复核闸门原因码 -> 中文说明
GATE_REASONS = {
    'CUTOFF': '未到发行截止时点',
    'PENDING_PAYMENT': '存在未核实缴款，暂不能计入发行',
    'CASH_DIFF': '缴款与发行现金对账不平',
    'SHARE_DIFF': '申报股数与获配/未获配合计不守恒',
    'LOCKED': '发行结果已确认并锁定',
}


def parse_moment(text):
    """解析 ISO 8601 时间；尾部 Z 按 UTC 处理，统一返回 naive datetime 比较。"""
    if text is None:
        raise ValueError('时间为空')
    s = str(text).strip()
    if not s:
        raise ValueError('时间为空')
    if s.endswith(('Z', 'z')):
        s = s[:-1]
    return datetime.fromisoformat(s)


def money(value):
    """金额入模：非负、有限、量化到分。"""
    from app.errors import DomainError
    from decimal import InvalidOperation
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value).strip().replace(',', ''))
    except InvalidOperation:
        raise DomainError('NUMBER', '金额格式无效或为空')
    if not d.is_finite() or d < 0:
        raise DomainError('NUMBER', '缴款金额必须为非负有限数')
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def whole_shares(value):
    """股数入模：非负整数。"""
    from app.errors import DomainError
    from decimal import InvalidOperation
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value).strip().replace(',', ''))
    except InvalidOperation:
        raise DomainError('NUMBER', '股数格式无效或为空')
    if not d.is_finite() or d < 0 or d != d.to_integral_value():
        raise DomainError('NUMBER', '股数必须为非负整数')
    return int(d)


def allocate(declared, paid, rights_price):
    """单户发行裁决。

    已核实缴款能覆盖的配股数向下取整，并以本户申报数为上限；
    缴款超过获配所需的部分留作应退，任何未核实/未到账部分都不垫付。
    返回 (获配, 未获配, 发行款, 应退, 可购股数)。
    """
    declared = whole_shares(declared)
    paid = money(paid)
    price = money(rights_price)
    if price <= 0:
        from app.errors import DomainError
        raise DomainError('NUMBER', '配股价必须为正')
    covered = int((paid / price).to_integral_value(rounding=ROUND_FLOOR))
    allocated = min(declared, max(covered, 0))
    unallocated = declared - allocated
    cash_required = (allocated * price).quantize(CENT)
    refund = (paid - cash_required).quantize(CENT)
    return allocated, unallocated, cash_required, refund, covered


def account_status(declared, allocated, refund):
    if declared == 0:
        return '无申报缴款隔离'
    if allocated == 0:
        return '未获配（未到账）'
    if allocated < declared:
        return '部分获配'
    if refund > 0:
        return '足额获配（超额待退）'
    return '足额获配'


def build_ledger(declarations, payments, rights_price):
    """按账户汇总申报与已核实缴款，生成可复核的逐户台账。

    declarations: [(account, shares), ...] 仅含已受理申报，可同户多笔累加
    payments:     [(account, amount, verified), ...] 仅含已受理缴款
    返回 accounts 行、totals、pending、orphan。
    """
    declared_map = {}
    for account, shares in declarations:
        declared_map[account] = declared_map.get(account, 0) + whole_shares(shares)

    paid_map, pending = {}, []
    for account, amount, verified in payments:
        if not verified:
            pending.append({'账户': account, '金额_CNY': str(money(amount))})
            continue
        paid_map[account] = paid_map.get(account, Decimal('0.00')) + money(amount)

    accounts, orphan = [], []
    for account in sorted(set(declared_map) | set(paid_map)):
        declared = declared_map.get(account, 0)
        paid = paid_map.get(account, Decimal('0.00')).quantize(CENT)
        if declared == 0:
            orphan.append({'账户': account, '实缴_CNY': str(paid), '原因码': 'ORPHAN',
                           '原因': '该账户无有效申报，缴款不计入发行，转待核查/应退'})
            continue
        allocated, unallocated, required, refund, covered = allocate(declared, paid, rights_price)
        difference = (paid - required - refund).quantize(CENT)
        accounts.append({
            '账户': account,
            '申报股数': str(declared),
            '实缴_CNY': str(paid),
            '可购股数': str(covered),
            '获配股数': str(allocated),
            '未获配股数': str(unallocated),
            '发行款_CNY': str(required),
            '应退_CNY': str(refund),
            '差异_CNY': str(difference),
            '状态': account_status(declared, allocated, refund),
        })

    total_declared = sum(whole_shares(r['申报股数']) for r in accounts)
    total_allocated = sum(whole_shares(r['获配股数']) for r in accounts)
    total_unallocated = sum(whole_shares(r['未获配股数']) for r in accounts)
    total_paid = sum((Decimal(r['实缴_CNY']) for r in accounts), Decimal('0.00')).quantize(CENT)
    total_required = sum((Decimal(r['发行款_CNY']) for r in accounts), Decimal('0.00')).quantize(CENT)
    total_refund = sum((Decimal(r['应退_CNY']) for r in accounts), Decimal('0.00')).quantize(CENT)
    share_conserved = total_declared == total_allocated + total_unallocated
    cash_conserved = total_paid == (total_required + total_refund).quantize(CENT)
    totals = {
        '申报合计_股': str(total_declared),
        '获配合计_股': str(total_allocated),
        '未获配合计_股': str(total_unallocated),
        '实缴合计_CNY': str(total_paid),
        '发行款合计_CNY': str(total_required),
        '应退合计_CNY': str(total_refund),
        '股份守恒': share_conserved,
        '现金守恒': cash_conserved,
        'balanced': share_conserved and cash_conserved,
    }
    return accounts, totals, pending, orphan


def evaluate_gate(offering, totals, pending_count, as_of, lock_status):
    """发布闸门：截止时点、未核实缴款、两项守恒、锁定状态。"""
    blockers = []
    if lock_status == '已发布':
        blockers.append('LOCKED')
    if parse_moment(as_of) < parse_moment(offering['cutoff']):
        blockers.append('CUTOFF')
    if pending_count:
        blockers.append('PENDING_PAYMENT')
    if not totals['现金守恒']:
        blockers.append('CASH_DIFF')
    if not totals['股份守恒']:
        blockers.append('SHARE_DIFF')
    can_publish = not blockers
    return {
        'can_publish': can_publish,
        'blockers': blockers,
        'blocker_text': '；'.join(GATE_REASONS[b] for b in blockers) or '满足发布条件',
        'as_of': str(as_of),
        'cutoff': offering['cutoff'],
        'reached_cutoff': parse_moment(as_of) >= parse_moment(offering['cutoff']),
    }
