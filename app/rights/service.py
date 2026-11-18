"""配股认购发行核对的服务编排：入库后确定性重算，输出复核视图。"""
from datetime import datetime, timezone
from decimal import Decimal

from app.rights import engine, reader, store


def _now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def create_offering(offering_id, security, rights_price, cutoff):
    return store.upsert_offering(offering_id, security, rights_price, cutoff)


def ingest_files(offering_id, declarations_text=None, payments_text=None):
    declarations, q1 = reader.parse_declarations(declarations_text) if declarations_text else ([], [])
    payments, q2 = reader.parse_payments(payments_text) if payments_text else ([], [])
    counters = store.ingest(offering_id, declarations, payments, q1 + q2)
    return counters


def reconcile(offering_id, as_of=None):
    """从已持久化的消息重建台账；发布后回显发布时快照，结果不再漂移。"""
    offering = store.get_offering(offering_id)
    as_of = as_of or _now_iso()
    if offering['lock_status'] == '已发布' and offering['snapshot']:
        import json
        snapshot = json.loads(offering['snapshot'])
        snapshot['offering'] = {k: offering[k] for k in
                                ('offering_id', 'security', 'rights_price', 'cutoff', 'rule_version',
                                 'lock_status', 'published_at', 'revision', 'decision', 'created_at')}
        snapshot['gate'] = engine.evaluate_gate(offering, snapshot['totals'],
                                                len(snapshot['pending']), as_of, '已发布')
        return snapshot

    declarations, payments, quarantine = store.load_messages(offering_id)
    accounts, totals, pending, orphan = engine.build_ledger(
        [(d['account'], d['shares']) for d in declarations],
        [(p['account'], Decimal(p['amount']), bool(p['verified'])) for p in payments],
        offering['rights_price'])
    # 无申报缴款（孤儿缴款）并入隔离面板，保留证据但绝不计入发行。
    quarantine_view = quarantine + [
        {'line_no': None, 'kind': 'payment', 'ref_id': o['账户'], 'account': o['账户'],
         'code': o['原因码'], 'reason': o['原因'], 'raw': o['实缴_CNY']} for o in orphan]
    gate = engine.evaluate_gate(offering, totals, len(pending), as_of, offering['lock_status'])
    return {
        'offering': {k: offering[k] for k in
                     ('offering_id', 'security', 'rights_price', 'cutoff', 'rule_version',
                      'lock_status', 'published_at', 'revision', 'decision', 'created_at')},
        'accounts': accounts,
        'totals': totals,
        'pending': pending,
        'quarantine': quarantine_view,
        'gate': gate,
        'counts': {'declaration_messages': len(declarations), 'payment_messages': len(payments),
                   'accounts': len(accounts), 'quarantine': len(quarantine_view),
                   'pending_payments': len(pending)},
        'as_of': as_of,
    }


def publish_offering(offering_id, revision, as_of=None):
    view = reconcile(offering_id, as_of)
    if view['offering']['lock_status'] == '已发布':
        from app.errors import DomainError
        raise DomainError('LOCKED', '发行结果已确认锁定，不能重复发布', 409)
    if not view['gate']['can_publish']:
        from app.errors import DomainError
        raise DomainError('GATE', '发布闸门未通过：' + view['gate']['blocker_text'], 409)
    new_revision = store.publish(offering_id, revision, view)
    return reconcile(offering_id, as_of) | {'new_revision': new_revision}


def review_offering(offering_id, revision, decision):
    new_revision = store.review(offering_id, revision, decision)
    return reconcile(offering_id) | {'new_revision': new_revision}
