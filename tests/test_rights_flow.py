"""配股认购发行核对：入库幂等、迟到/冲突隔离、发布锁定与 HTTP 端到端。"""
import pytest

from app.config import RIGHTS_SAMPLE
from app.errors import DomainError
from app.rights import service, store

OID = 'RIGHTS-TEST'
OFFERING = {'offering_id': OID, 'security': '700001',
            'rights_price': '5.00', 'cutoff': '2026-11-18T15:00:00Z'}
DECL_HEAD = 'msg_id,account,shares,received_at\n'
PAY_HEAD = 'pay_id,account,amount,verified,received_at\n'


@pytest.fixture
def rights_client(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_DB', str(tmp_path / 'rights.sqlite3'))
    from app.main import app
    app.config['TESTING'] = True
    client = app.test_client()
    client.post('/api/rights/offering', json=OFFERING)
    return client


def _ingest(client, decl=None, pay=None, as_of='2026-11-18T16:00:00Z'):
    body = {'offering_id': OID, 'as_of': as_of}
    if decl is not None:
        body['declarations_csv'] = decl
    if pay is not None:
        body['payments_csv'] = pay
    return client.post('/api/rights/ingest', json=body)


def _view(client, as_of='2026-11-18T16:00:00Z'):
    return client.get(f'/api/rights/reconcile?offering_id={OID}&as_of={as_of}').get_json()


def test_per_account_reconcilable_ledger(rights_client):
    decl = DECL_HEAD + 'D1,A,1000,2026-11-18T09:00:00Z\nD2,B,800,2026-11-18T09:05:00Z\n'
    pay = PAY_HEAD + 'P1,A,5000.00,1,2026-11-18T10:00:00Z\nP2,B,2000.00,1,2026-11-18T10:05:00Z\n'
    assert _ingest(rights_client, decl, pay).status_code == 200
    view = _view(rights_client)
    by = {r['账户']: r for r in view['accounts']}
    assert by['A']['获配股数'] == '1000' and by['A']['未获配股数'] == '0'
    assert by['B']['获配股数'] == '400' and by['B']['未获配股数'] == '400'
    assert all(r['差异_CNY'] == '0.00' for r in view['accounts'])
    assert view['totals']['balanced'] and view['totals']['股份守恒']


def test_unverified_payment_never_counted_as_issued(rights_client):
    decl = DECL_HEAD + 'D1,A,600,2026-11-18T09:00:00Z\n'
    pay = PAY_HEAD + 'P1,A,1000.00,0,2026-11-18T10:00:00Z\n'
    _ingest(rights_client, decl, pay)
    view = _view(rights_client)
    row = view['accounts'][0]
    assert row['实缴_CNY'] == '0.00' and row['获配股数'] == '0' and row['未获配股数'] == '600'
    assert len(view['pending']) == 1
    assert not view['gate']['can_publish'] and 'PENDING_PAYMENT' in view['gate']['blockers']


def test_verification_upgrade_unblocks_gate(rights_client):
    decl = DECL_HEAD + 'D1,A,600,2026-11-18T09:00:00Z\n'
    _ingest(rights_client, decl, PAY_HEAD + 'P1,A,1000.00,0,2026-11-18T10:00:00Z\n')
    assert not _view(rights_client)['gate']['can_publish']
    # 同金额同账户，仅核实标志 0->1：视为核实完成，不算冲突
    resp = _ingest(rights_client, pay=PAY_HEAD + 'P1,A,1000.00,1,2026-11-18T14:50:00Z\n')
    assert resp.get_json()['counters']['accepted_payment'] == 1
    view = _view(rights_client)
    assert view['gate']['can_publish']
    assert view['accounts'][0]['获配股数'] == '200'


def test_duplicate_rerun_does_not_double_issue(rights_client):
    decl = DECL_HEAD + 'D1,A,1000,2026-11-18T09:00:00Z\n'
    pay = PAY_HEAD + 'P1,A,5000.00,1,2026-11-18T10:00:00Z\n'
    first = _ingest(rights_client, decl, pay).get_json()['counters']
    assert first['accepted_declaration'] == 1 and first['accepted_payment'] == 1
    second = _ingest(rights_client, decl, pay).get_json()['counters']
    assert second['accepted_declaration'] == 0 and second['accepted_payment'] == 0
    assert second['duplicate_declaration'] == 1 and second['duplicate_payment'] == 1
    view = _view(rights_client)
    assert len(view['accounts']) == 1 and view['accounts'][0]['获配股数'] == '1000'
    assert view['totals']['获配合计_股'] == '1000'


def test_conflicting_same_id_keeps_first_and_quarantines(rights_client):
    d1 = DECL_HEAD + 'D1,A,500,2026-11-18T09:00:00Z\n'
    d2 = DECL_HEAD + 'D1,A,999,2026-11-18T09:30:00Z\n'
    _ingest(rights_client, d1, PAY_HEAD + 'P1,A,2500.00,1,2026-11-18T10:00:00Z\n')
    counters = _ingest(rights_client, d2).get_json()['counters']
    assert counters['conflict_declaration'] == 1
    view = _view(rights_client)
    # 首次确认结果不被迟到的同编号申报覆盖
    assert view['accounts'][0]['申报股数'] == '500'
    assert any(q['code'] == 'CONFLICT' for q in view['quarantine'])


def test_late_declaration_after_cutoff_is_quarantined(rights_client):
    late = DECL_HEAD + 'D9,A,100,2026-11-18T15:00:01Z\n'
    _ingest(rights_client, late)
    view = _view(rights_client)
    assert any(q['code'] == 'LATE' for q in view['quarantine'])
    assert view['counts']['accounts'] == 0


def test_payment_without_declaration_is_orphan_not_issued(rights_client):
    _ingest(rights_client, pay=PAY_HEAD + 'PX,Z,700.00,1,2026-11-18T12:00:00Z\n')
    view = _view(rights_client)
    assert view['accounts'] == []
    assert any(q['code'] == 'ORPHAN' and q['account'] == 'Z' for q in view['quarantine'])


def test_publish_freezes_results_and_rejects_later_messages(rights_client):
    decl = DECL_HEAD + 'D1,A,1000,2026-11-18T09:00:00Z\n'
    _ingest(rights_client, decl, PAY_HEAD + 'P1,A,5000.00,1,2026-11-18T10:00:00Z\n')
    rev = _view(rights_client)['offering']['revision']
    published = rights_client.post('/api/rights/publish',
                                   json={'offering_id': OID, 'revision': rev,
                                         'as_of': '2026-11-18T16:00:00Z'})
    assert published.status_code == 200
    assert published.get_json()['offering']['lock_status'] == '已发布'
    # 重复发布 -> 409
    again = rights_client.post('/api/rights/publish',
                               json={'offering_id': OID, 'revision': rev + 1,
                                     'as_of': '2026-11-18T16:00:00Z'})
    assert again.status_code == 409 and again.get_json()['code'] == 'LOCKED'
    # 锁定后任何入库 -> 409，结果不变
    locked = _ingest(rights_client, DECL_HEAD + 'D2,B,10,2026-11-19T08:00:00Z\n')
    assert locked.status_code == 409 and locked.get_json()['code'] == 'LOCKED'
    frozen = _view(rights_client, '2026-11-20T00:00:00Z')
    assert frozen['totals']['获配合计_股'] == '1000'
    assert frozen['gate']['blockers'] == ['LOCKED']


def test_publish_refused_before_cutoff(rights_client):
    decl = DECL_HEAD + 'D1,A,1000,2026-11-18T09:00:00Z\n'
    _ingest(rights_client, decl, PAY_HEAD + 'P1,A,5000.00,1,2026-11-18T10:00:00Z\n')
    rev = _view(rights_client)['offering']['revision']
    resp = rights_client.post('/api/rights/publish',
                              json={'offering_id': OID, 'revision': rev,
                                    'as_of': '2026-11-18T14:59:59Z'})
    assert resp.status_code == 409 and resp.get_json()['code'] == 'GATE'
    assert '截止' in resp.get_json()['message']


def test_stale_revision_conflicts_and_bad_decision_rejected(rights_client):
    rev = _view(rights_client)['offering']['revision']
    ok = rights_client.post('/api/rights/review',
                            json={'offering_id': OID, 'revision': rev, 'decision': '退回'})
    assert ok.status_code == 200
    stale = rights_client.post('/api/rights/review',
                               json={'offering_id': OID, 'revision': rev, 'decision': '通过'})
    assert stale.status_code == 409
    bad = rights_client.post('/api/rights/review',
                             json={'offering_id': OID, 'revision': rev + 1, 'decision': '其他'})
    assert bad.status_code == 422


def test_sample_pipeline_balances_and_page_renders(rights_client):
    resp = _ingest(rights_client, RIGHTS_SAMPLE['declarations_csv'],
                   RIGHTS_SAMPLE['payments_csv'])
    counters = resp.get_json()['counters']
    assert counters['duplicate_declaration'] == 1
    assert counters['late_declaration'] == 1
    assert counters['conflict_payment'] == 1
    # 样例含一笔未核实缴款，初始不可发布；补核实后可发布
    assert not _view(rights_client)['gate']['can_publish']
    page = rights_client.get(f'/rights?offering_id={OID}').data.decode()
    assert '配股认购发行核对' in page and '发布闸门' in page and '逐户台账' in page
    evidence = rights_client.get(f'/api/rights/evidence.csv?offering_id={OID}').data.decode('utf-8-sig')
    assert '截止时点' in evidence and 'ORPHAN' in evidence


def test_structural_errors_are_422_and_bad_requests_400(rights_client):
    assert _ingest(rights_client, decl='a,b\n1,2').status_code == 422
    assert rights_client.post('/api/rights/ingest', json=[1]).status_code == 400
    assert rights_client.post('/api/rights/ingest', json={'offering_id': OID}).status_code == 400
    assert rights_client.get('/api/rights/reconcile?offering_id=MISSING').status_code == 404


def test_multipart_accepts_either_file_and_rejects_wrong_field(rights_client):
    import io
    decl = (DECL_HEAD + 'D1,A,1000,2026-11-18T09:00:00Z\n').encode()
    pay = (PAY_HEAD + 'P1,A,5000.00,1,2026-11-18T10:00:00Z\n').encode()
    only_pay = rights_client.post(
        '/api/rights/ingest',
        data={'offering_id': OID, 'payments': (io.BytesIO(pay), 'p.csv')},
        content_type='multipart/form-data')
    assert only_pay.status_code == 200
    both = rights_client.post(
        '/api/rights/ingest',
        data={'offering_id': OID,
              'declarations': (io.BytesIO(decl), 'd.csv'),
              'payments': (io.BytesIO(pay), 'p.csv')},
        content_type='multipart/form-data')
    assert both.status_code == 200 and both.get_json()['counters']['accepted_declaration'] == 1
    wrong = rights_client.post(
        '/api/rights/ingest',
        data={'offering_id': OID, 'file': (io.BytesIO(decl), 'x.csv')},
        content_type='multipart/form-data')
    assert wrong.status_code == 400
    # /api/run 仍只接受 file 字段
    wrong_run = rights_client.post(
        '/api/run', data={'declarations': (io.BytesIO(decl), 'x.csv')},
        content_type='multipart/form-data')
    assert wrong_run.status_code == 400


def test_offering_parameters_are_immutable(rights_client):
    changed = dict(OFFERING, rights_price='9.99')
    resp = rights_client.post('/api/rights/offering', json=changed)
    assert resp.status_code == 409 and resp.get_json()['code'] == 'CONFLICT'


def test_service_layer_idempotent_without_http(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_DB', str(tmp_path / 's.sqlite3'))
    service.create_offering(OID, '700001', '5.00', '2026-11-18T15:00:00Z')
    decl = DECL_HEAD + 'D1,A,1000,2026-11-18T09:00:00Z\n'
    pay = PAY_HEAD + 'P1,A,5000.00,1,2026-11-18T10:00:00Z\n'
    service.ingest_files(OID, decl, pay)
    service.ingest_files(OID, decl, pay)  # 重跑
    declarations, payments, quarantine = store.load_messages(OID)
    assert len(declarations) == 1 and len(payments) == 1
    with pytest.raises(DomainError):
        service.create_offering(OID, '700001', '6.00', '2026-11-18T15:00:00Z')
