"""配股认购发行核对的 HTTP 接口。"""
import csv
import io

from flask import Blueprint, Response, jsonify, render_template, request

from app.errors import DomainError
from app.rights import service, store
from app.rights.reader import decode_csv

bp = Blueprint('rights', __name__)

DEFAULT_OFFERING = 'RIGHTS-0001'


def _id(payload):
    return (payload.get('offering_id') or DEFAULT_OFFERING).strip()


def _as_of(payload):
    value = payload.get('as_of')
    if value is None and request.args.get('as_of'):
        value = request.args['as_of']
    return value


@bp.route('/api/rights/offering', methods=['POST'])
def create():
    payload = request.get_json(silent=True) or {}
    offering = service.create_offering(
        _id(payload), str(payload.get('security', '')).strip(),
        payload.get('rights_price'), str(payload.get('cutoff', '')).strip())
    return jsonify(offering)


@bp.route('/api/rights/offerings')
def offerings():
    return jsonify(store.list_offerings())


@bp.route('/api/rights/ingest', methods=['POST'])
def ingest():
    if request.files:
        offering_id = (request.form.get('offering_id') or DEFAULT_OFFERING).strip()
        decl_text = decode_csv(request.files['declarations'].read()) if 'declarations' in request.files else None
        pay_text = decode_csv(request.files['payments'].read()) if 'payments' in request.files else None
    else:
        payload = request.get_json(silent=True) or {}
        offering_id = _id(payload)
        decl_text = payload.get('declarations_csv')
        pay_text = payload.get('payments_csv')
    if decl_text is None and pay_text is None:
        raise DomainError('REQUEST', '需提供申报或缴款 CSV 文本/文件', 400)
    counters = service.ingest_files(offering_id, decl_text, pay_text)
    return jsonify({'offering_id': offering_id, 'counters': counters,
                    'view': service.reconcile(offering_id, _as_of(request.get_json(silent=True) or {}))})


@bp.route('/api/rights/reconcile')
def reconcile():
    offering_id = (request.args.get('offering_id') or DEFAULT_OFFERING).strip()
    return jsonify(service.reconcile(offering_id, request.args.get('as_of')))


@bp.route('/api/rights/publish', methods=['POST'])
def publish():
    payload = request.get_json(silent=True) or {}
    offering_id = _id(payload)
    try:
        revision = int(payload.get('revision', 0))
    except (ValueError, TypeError):
        raise DomainError('VERSION', '版本必须为整数')
    return jsonify(service.publish_offering(offering_id, revision, payload.get('as_of')))


@bp.route('/api/rights/review', methods=['POST'])
def review():
    payload = request.get_json(silent=True) or {}
    try:
        revision = int(payload.get('revision', 0))
    except (ValueError, TypeError):
        raise DomainError('VERSION', '版本必须为整数')
    return jsonify(service.review_offering(_id(payload), revision, payload.get('decision')))


@bp.route('/rights')
def page():
    offering_id = (request.args.get('offering_id') or DEFAULT_OFFERING).strip()
    existing = store.list_offerings()
    if not any(o['offering_id'] == offering_id for o in existing):
        # 页面首次进入：用样例参数初始化一个空发行，不产生任何申报/缴款。
        from app.config import RIGHTS_SAMPLE
        params = RIGHTS_SAMPLE['offering']
        service.create_offering(offering_id, params['security'],
                                params['rights_price'], params['cutoff'])
    view = service.reconcile(offering_id, request.args.get('as_of'))
    from app.config import RIGHTS_SAMPLE
    return render_template('rights.html', view=view, sample=RIGHTS_SAMPLE)


@bp.route('/api/rights/evidence.csv')
def evidence():
    offering_id = (request.args.get('offering_id') or DEFAULT_OFFERING).strip()
    view = service.reconcile(offering_id, request.args.get('as_of'))
    out = io.StringIO(newline='')
    rows = [{'板块': '账户台账', **r} for r in view['accounts']]
    rows += [{'板块': '待核实缴款', '账户': r['账户'], '金额_CNY': r['金额_CNY']} for r in view['pending']]
    rows += [{'板块': '隔离', '账户': r['account'], '原因码': r['code'], '原因': r['reason']}
             for r in view['quarantine']]
    fields = list(dict.fromkeys(['板块', 'offering_id', '规则版本', '截止时点', '复核时点']
                                + [k for r in rows for k in r]))
    writer = csv.DictWriter(out, fieldnames=fields)
    writer.writeheader()
    meta = {'offering_id': view['offering']['offering_id'],
            '规则版本': view['offering']['rule_version'],
            '截止时点': view['offering']['cutoff'], '复核时点': view['as_of']}
    for r in rows:
        writer.writerow({**meta, **r})
    return Response('﻿' + out.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=rights_evidence.csv'})
