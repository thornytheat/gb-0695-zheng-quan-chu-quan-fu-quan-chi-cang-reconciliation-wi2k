from pathlib import Path
RULE_VERSION = "2026.11.18.2"
TITLE = '证券除权复权与持仓对账'
SHORT = 'equity-adjust'
MODULES = ['行动接收', '证券映射', '权益登记', '除权因子', '持仓重算', '现金对账', '修订回放']
HEADERS = ['record', 'kind', 'security', 'alias', 'event_date', 'record_date', 'ex_date', 'price', 'cash', 'bonus', 'rights', 'rights_price', 'shares', 'version', 'reported_cash']
SAMPLE = Path(__file__).resolve().parent.parent / "sample_data" / "operations.csv"
LIMIT = 10000
# 配股认购发行核对子系统的样例发行参数与样例 CSV 文本
_RIGHTS_DIR = Path(__file__).resolve().parent.parent / "sample_data"
RIGHTS_SAMPLE = {
    'offering': {'offering_id': 'RIGHTS-0001', 'security': '700001',
                 'rights_price': '5.00', 'cutoff': '2026-11-18T15:00:00Z'},
    'declarations_csv': (_RIGHTS_DIR / 'rights_declarations.csv').read_text(encoding='utf-8-sig'),
    'payments_csv': (_RIGHTS_DIR / 'rights_payments.csv').read_text(encoding='utf-8-sig'),
}
