"""配股申报/缴款 CSV 读取与单消息裁决。

表头字段固定，列可换序；结构性错误（缺列/重复列/空文件）直接 422，
单行业务问题进入隔离并保留原因码与原始行号，不影响其它行。
"""
import csv
import io
from decimal import Decimal

DECLARATION_HEADERS = ['msg_id', 'account', 'shares', 'received_at']
PAYMENT_HEADERS = ['pay_id', 'account', 'amount', 'verified', 'received_at']
TRUE_TOKENS = {'1', 'true', 'yes', 'y', '是', '已核实'}
FALSE_TOKENS = {'0', 'false', 'no', 'n', '否', '未核实'}


def decode_csv(raw):
    """兼容 UTF-8 BOM 与 GB18030 旧编码。"""
    from app.errors import DomainError
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise DomainError('ENCODING', '无法识别文件编码')


def _open_csv(text):
    if not isinstance(text, str) or not text.lstrip('﻿'):
        from app.errors import DomainError
        raise DomainError('EMPTY', '输入为空')
    reader = csv.reader(io.StringIO(text.lstrip('﻿'), newline=''), strict=True)
    try:
        head = next(reader, None)
    except csv.Error:
        from app.errors import DomainError
        raise DomainError('STRUCTURE', '表头损坏')
    if head is None:
        from app.errors import DomainError
        raise DomainError('BLANK', '文件仅有空行')
    head = [x.strip() for x in head]
    return reader, head


def _require_headers(head, expected):
    from app.errors import DomainError
    if set(head) != set(expected) or len(head) != len(expected):
        raise DomainError('HEADER', '字段缺失或重复')


def parse_declarations(text):
    """返回 (有效消息列表, 隔离行列表)。每条消息标准化字段。"""
    reader, head = _open_csv(text)
    _require_headers(head, DECLARATION_HEADERS)
    good, quarantine = [], []
    try:
        for cells in reader:
            if not cells or all(not x.strip() for x in cells):
                continue
            if cells == head:
                continue
            line = reader.line_num
            if len(cells) != len(head):
                quarantine.append({'line_no': line, 'kind': 'declaration', 'raw': '|'.join(cells),
                                   'code': 'COLUMNS', 'reason': '列数不符'})
                continue
            r = {k: v.strip() for k, v in zip(head, cells)}
            err = _check_message(r, 'declaration')
            if err:
                quarantine.append({'line_no': line, 'kind': 'declaration', 'msg_id': r.get('msg_id', ''),
                                   'account': r.get('account', ''), 'code': err[0], 'reason': err[1],
                                   'raw': '|'.join(cells)})
                continue
            from app.rights.engine import whole_shares, parse_moment
            good.append({'msg_id': r['msg_id'], 'account': r['account'],
                         'shares': whole_shares(r['shares']),
                         'received_at': parse_moment(r['received_at']),
                         '行号': line})
    except csv.Error:
        from app.errors import DomainError
        raise DomainError('STRUCTURE', '引号未闭合或记录截断')
    return good, quarantine


def parse_payments(text):
    reader, head = _open_csv(text)
    _require_headers(head, PAYMENT_HEADERS)
    good, quarantine = [], []
    try:
        for cells in reader:
            if not cells or all(not x.strip() for x in cells):
                continue
            if cells == head:
                continue
            line = reader.line_num
            if len(cells) != len(head):
                quarantine.append({'line_no': line, 'kind': 'payment', 'raw': '|'.join(cells),
                                   'code': 'COLUMNS', 'reason': '列数不符'})
                continue
            r = {k: v.strip() for k, v in zip(head, cells)}
            err = _check_message(r, 'payment')
            if err:
                quarantine.append({'line_no': line, 'kind': 'payment', 'pay_id': r.get('pay_id', ''),
                                   'account': r.get('account', ''), 'code': err[0], 'reason': err[1],
                                   'raw': '|'.join(cells)})
                continue
            token = r['verified'].lower()
            verified = token in TRUE_TOKENS
            from app.rights.engine import money, parse_moment
            good.append({'pay_id': r['pay_id'], 'account': r['account'],
                         'amount': money(r['amount']), 'verified': verified,
                         'received_at': parse_moment(r['received_at']),
                         '行号': line})
    except csv.Error:
        from app.errors import DomainError
        raise DomainError('STRUCTURE', '引号未闭合或记录截断')
    return good, quarantine


def _check_message(r, kind):
    from app.errors import DomainError
    from app.rights.engine import parse_moment, whole_shares, money
    id_field = 'msg_id' if kind == 'declaration' else 'pay_id'
    if not r.get(id_field):
        return 'MISSING', '消息编号为空'
    if not r.get('account'):
        return 'MISSING', '账户为空'
    if not r.get('received_at'):
        return 'MISSING', '到达时间为空'
    try:
        parse_moment(r['received_at'])
    except (ValueError, TypeError):
        return 'DATETIME', '时间格式无效（需 ISO 8601）'
    try:
        if kind == 'declaration':
            whole_shares(r['shares'])
        else:
            money(r['amount'])
            if r['verified'].lower() not in TRUE_TOKENS | FALSE_TOKENS:
                return 'FLAG', '核实标志无效'
    except DomainError as e:
        return e.code, e.message
    return None
