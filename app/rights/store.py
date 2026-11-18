"""配股认购的 SQLite 持久层。

幂等以消息主键（msg_id / pay_id）为锚：重复提交不重复发行；
同键内容冲突只隔离不覆盖；已发布发行整账锁定，迟到消息一律拒收。
"""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.errors import DomainError
from app.rights.engine import RIGHTS_RULE_VERSION

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rights_offerings(
  offering_id TEXT PRIMARY KEY,
  security TEXT NOT NULL,
  rights_price TEXT NOT NULL,
  cutoff TEXT NOT NULL,
  rule_version TEXT NOT NULL,
  created_at TEXT NOT NULL,
  lock_status TEXT NOT NULL DEFAULT '待发布',
  published_at TEXT,
  snapshot TEXT,
  revision INTEGER NOT NULL DEFAULT 1,
  decision TEXT NOT NULL DEFAULT '待复核'
);
CREATE TABLE IF NOT EXISTS rights_declarations(
  offering_id TEXT NOT NULL,
  msg_id TEXT NOT NULL,
  account TEXT NOT NULL,
  shares INTEGER NOT NULL,
  received_at TEXT NOT NULL,
  line_no INTEGER NOT NULL,
  PRIMARY KEY(offering_id, msg_id)
);
CREATE TABLE IF NOT EXISTS rights_payments(
  offering_id TEXT NOT NULL,
  pay_id TEXT NOT NULL,
  account TEXT NOT NULL,
  amount TEXT NOT NULL,
  verified INTEGER NOT NULL,
  received_at TEXT NOT NULL,
  line_no INTEGER NOT NULL,
  PRIMARY KEY(offering_id, pay_id)
);
CREATE TABLE IF NOT EXISTS rights_quarantine(
  offering_id TEXT NOT NULL,
  line_no INTEGER,
  kind TEXT NOT NULL,
  ref_id TEXT,
  account TEXT,
  code TEXT NOT NULL,
  reason TEXT NOT NULL,
  raw TEXT NOT NULL,
  dedup TEXT NOT NULL,
  UNIQUE(offering_id, dedup)
);
"""


def connection():
    path = Path(os.environ.get('APP_DB', 'data/runs.sqlite3'))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(_SCHEMA)
    return db


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def upsert_offering(offering_id, security, rights_price, cutoff):
    from app.rights.engine import money, parse_moment
    price = str(money(rights_price))
    parse_moment(cutoff)  # 截止时点在创建时校验且不可变
    if not security:
        raise DomainError('MISSING', '证券代码不能为空')
    with connection() as db:
        row = db.execute('SELECT * FROM rights_offerings WHERE offering_id=?', (offering_id,)).fetchone()
        if row:
            if (row['security'], row['rights_price'], row['cutoff']) != (security, price, cutoff):
                raise DomainError('CONFLICT', '发行参数已存在且不可变，不能覆盖已确认结果', 409)
            return dict(row)
        db.execute(
            'INSERT INTO rights_offerings(offering_id,security,rights_price,cutoff,rule_version,created_at)'
            ' VALUES(?,?,?,?,?,?)',
            (offering_id, security, price, cutoff, RIGHTS_RULE_VERSION, _now()))
        return dict(db.execute('SELECT * FROM rights_offerings WHERE offering_id=?', (offering_id,)).fetchone())


def get_offering(offering_id):
    with connection() as db:
        row = db.execute('SELECT * FROM rights_offerings WHERE offering_id=?', (offering_id,)).fetchone()
    if row is None:
        raise DomainError('NOT_FOUND', '发行批次不存在', 404)
    return dict(row)


def list_offerings():
    with connection() as db:
        return [dict(r) for r in db.execute(
            'SELECT offering_id,security,rights_price,cutoff,lock_status,revision,decision,'
            'published_at,created_at FROM rights_offerings ORDER BY rowid DESC')]


def _insert_quarantine(db, offering_id, q):
    dedup = json.dumps([q.get('line_no'), q.get('kind'), q.get('code'), q.get('raw', '')],
                       ensure_ascii=False, sort_keys=True)
    db.execute(
        'INSERT OR IGNORE INTO rights_quarantine(offering_id,line_no,kind,ref_id,account,code,reason,raw,dedup)'
        ' VALUES(?,?,?,?,?,?,?,?,?)',
        (offering_id, q.get('line_no'), q.get('kind', ''), q.get('msg_id') or q.get('pay_id', ''),
         q.get('account', ''), q['code'], q['reason'], q.get('raw', ''), dedup))


def ingest(offering_id, declarations, payments, quarantine_rows):
    offering = get_offering(offering_id)
    if offering['lock_status'] == '已发布':
        raise DomainError('LOCKED', '发行结果已确认锁定，拒收新申报与缴款', 409)
    cutoff = offering['cutoff']
    counters = {'accepted_declaration': 0, 'duplicate_declaration': 0, 'late_declaration': 0,
                'conflict_declaration': 0, 'accepted_payment': 0, 'duplicate_payment': 0,
                'conflict_payment': 0, 'quarantine_new': 0}
    with connection() as db:
        for q in quarantine_rows:
            cur = db.execute('SELECT 1 FROM rights_quarantine WHERE offering_id=? AND dedup=?',
                             (offering_id, json.dumps([q.get('line_no'), q.get('kind'), q.get('code'),
                                                       q.get('raw', '')], ensure_ascii=False, sort_keys=True)))
            if cur.fetchone() is None:
                counters['quarantine_new'] += 1
            _insert_quarantine(db, offering_id, q)

        for m in declarations:
            existing = db.execute('SELECT * FROM rights_declarations WHERE offering_id=? AND msg_id=?',
                                  (offering_id, m['msg_id'])).fetchone()
            if existing:
                if (existing['account'], existing['shares'], existing['received_at']) == \
                        (m['account'], m['shares'], m['received_at'].isoformat()):
                    counters['duplicate_declaration'] += 1
                else:
                    counters['conflict_declaration'] += 1
                    _insert_quarantine(db, offering_id, {
                        'line_no': m['行号'], 'kind': 'declaration', 'msg_id': m['msg_id'],
                        'account': m['account'], 'code': 'CONFLICT',
                        'reason': '同编号申报内容冲突，保留首次确认结果，禁止覆盖',
                        'raw': f"{m['msg_id']}|{m['account']}|{m['shares']}|{m['received_at'].isoformat()}"})
                continue
            if m['received_at'].isoformat() > cutoff:
                counters['late_declaration'] += 1
                _insert_quarantine(db, offering_id, {
                    'line_no': m['行号'], 'kind': 'declaration', 'msg_id': m['msg_id'],
                    'account': m['account'], 'code': 'LATE',
                    'reason': f"到达时间 {m['received_at'].isoformat()} 晚于截止时点 {cutoff}",
                    'raw': f"{m['msg_id']}|{m['account']}|{m['shares']}|{m['received_at'].isoformat()}"})
                continue
            db.execute('INSERT INTO rights_declarations(offering_id,msg_id,account,shares,received_at,line_no)'
                       ' VALUES(?,?,?,?,?,?)',
                       (offering_id, m['msg_id'], m['account'], m['shares'],
                        m['received_at'].isoformat(), m['行号']))
            counters['accepted_declaration'] += 1

        for m in payments:
            existing = db.execute('SELECT * FROM rights_payments WHERE offering_id=? AND pay_id=?',
                                  (offering_id, m['pay_id'])).fetchone()
            if existing:
                same_core = (existing['account'], existing['amount']) == (m['account'], str(m['amount']))
                if same_core and bool(existing['verified']) == m['verified']:
                    counters['duplicate_payment'] += 1
                    continue
                if same_core and (not existing['verified']) and m['verified']:
                    db.execute('UPDATE rights_payments SET verified=1,received_at=?,line_no=? '
                               'WHERE offering_id=? AND pay_id=?',
                               (m['received_at'].isoformat(), m['行号'], offering_id, m['pay_id']))
                    counters['accepted_payment'] += 1
                    continue
                counters['conflict_payment'] += 1
                _insert_quarantine(db, offering_id, {
                    'line_no': m['行号'], 'kind': 'payment', 'pay_id': m['pay_id'],
                    'account': m['account'], 'code': 'CONFLICT',
                    'reason': '同编号缴款金额/账户冲突或核实状态回退，保留已确认结果',
                    'raw': f"{m['pay_id']}|{m['account']}|{m['amount']}|{int(m['verified'])}"})
                continue
            db.execute('INSERT INTO rights_payments(offering_id,pay_id,account,amount,verified,received_at,line_no)'
                       ' VALUES(?,?,?,?,?,?,?)',
                       (offering_id, m['pay_id'], m['account'], str(m['amount']),
                        int(m['verified']), m['received_at'].isoformat(), m['行号']))
            counters['accepted_payment'] += 1
    return counters


def load_messages(offering_id):
    with connection() as db:
        declarations = [dict(r) for r in db.execute(
            'SELECT msg_id,account,shares,received_at,line_no FROM rights_declarations '
            'WHERE offering_id=? ORDER BY line_no', (offering_id,))]
        payments = [dict(r) for r in db.execute(
            'SELECT pay_id,account,amount,verified,received_at,line_no FROM rights_payments '
            'WHERE offering_id=? ORDER BY line_no', (offering_id,))]
        quarantine = [dict(r) for r in db.execute(
            'SELECT line_no,kind,ref_id,account,code,reason,raw FROM rights_quarantine '
            'WHERE offering_id=? ORDER BY rowid', (offering_id,))]
    return declarations, payments, quarantine


def publish(offering_id, revision, snapshot):
    with connection() as db:
        row = db.execute('SELECT lock_status,revision FROM rights_offerings WHERE offering_id=?',
                         (offering_id,)).fetchone()
        if row is None:
            raise DomainError('NOT_FOUND', '发行批次不存在', 404)
        if row['lock_status'] == '已发布':
            raise DomainError('LOCKED', '发行结果已确认锁定，不能重复发布', 409)
        changed = db.execute(
            'UPDATE rights_offerings SET lock_status=?,published_at=?,snapshot=?,revision=revision+1,decision=? '
            'WHERE offering_id=? AND revision=?',
            ('已发布', _now(), json.dumps(snapshot, ensure_ascii=False), '通过',
             offering_id, revision)).rowcount
        if not changed:
            raise DomainError('VERSION', '版本已变化，请重新载入', 409)
    return revision + 1


def review(offering_id, revision, decision):
    if decision not in ('通过', '退回'):
        raise DomainError('DECISION', '复核动作无效')
    with connection() as db:
        changed = db.execute(
            'UPDATE rights_offerings SET revision=revision+1,decision=? WHERE offering_id=? AND revision=?',
            (decision, offering_id, revision)).rowcount
        if not changed:
            raise DomainError('VERSION', '版本已变化，请重新载入', 409)
    return revision + 1
