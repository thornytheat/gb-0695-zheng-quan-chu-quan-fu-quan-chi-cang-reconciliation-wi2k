"""配股发行纯领域引擎：获配裁决、守恒、发布闸门。"""
from decimal import Decimal

import pytest

from app.errors import DomainError
from app.rights.engine import (allocate, build_ledger, evaluate_gate, money,
                               parse_moment, whole_shares)

OFFERING = {'cutoff': '2026-11-18T15:00:00Z'}


def test_allocate_full_payment_covers_declaration():
    allocated, unallocated, required, refund, covered = allocate(1000, '5000', '5.00')
    assert (allocated, unallocated, covered) == (1000, 0, 1000)
    assert required == Decimal('5000.00') and refund == Decimal('0.00')


def test_allocate_underpaid_floors_and_never_advances_unreceived_cash():
    # 缴款 2000，配股价 5：可购 400 股，未获配部分不垫付
    allocated, unallocated, required, refund, covered = allocate(800, '2000', '5.00')
    assert (allocated, unallocated, covered) == (400, 400, 400)
    assert required == Decimal('2000.00') and refund == Decimal('0.00')


def test_allocate_overpaid_caps_by_declaration_and_refunds_residual():
    allocated, unallocated, required, refund, covered = allocate(500, '3000', '5.00')
    assert (allocated, unallocated, covered) == (500, 0, 600)
    assert required == Decimal('2500.00') and refund == Decimal('500.00')


def test_allocate_fractional_share_floors():
    # 99 元 / 5 元 = 19.8 -> 19 股，余款 4 元应退
    allocated, unallocated, required, refund, _ = allocate(100, '99.00', '5.00')
    assert allocated == 19 and unallocated == 81
    assert required == Decimal('95.00') and refund == Decimal('4.00')


def test_zero_payment_gets_no_shares():
    allocated, unallocated, required, refund, _ = allocate(300, '0', '5.00')
    assert (allocated, unallocated, required, refund) == (0, 300, Decimal('0.00'), Decimal('0.00'))


def test_money_quantizes_to_cent_and_rejects_negative():
    assert money('100.005') == Decimal('100.01')
    with pytest.raises(DomainError):
        money('-1')
    with pytest.raises(DomainError):
        money('')


def test_whole_shares_rejects_fraction_and_text():
    assert whole_shares('1,200') == 1200
    with pytest.raises(DomainError):
        whole_shares('1.5')
    with pytest.raises(DomainError):
        whole_shares('abc')


def test_parse_moment_accepts_z_suffix():
    assert parse_moment('2026-11-18T15:00:00Z').isoformat() == '2026-11-18T15:00:00'


def test_build_ledger_conserves_shares_and_cash():
    declarations = [('A', 1000), ('A', 200), ('B', 800), ('C', 500)]
    payments = [('A', '6000.00', True), ('B', '2000.00', True),
                ('C', '1000.00', False), ('D', '700.00', True)]
    accounts, totals, pending, orphan = build_ledger(declarations, payments, '5.00')
    by = {r['账户']: r for r in accounts}
    assert by['A']['申报股数'] == '1200' and by['A']['获配股数'] == '1200'
    assert by['B']['获配股数'] == '400' and by['B']['未获配股数'] == '400'
    # C 的未核实缴款不计入：实缴为 0，整户未获配
    assert by['C']['实缴_CNY'] == '0.00' and by['C']['获配股数'] == '0'
    assert len(pending) == 1 and pending[0]['账户'] == 'C'
    # D 无申报：不进账户台账，进孤儿清单且不计入发行
    assert 'D' not in by and orphan[0]['账户'] == 'D'
    assert totals['股份守恒'] and totals['现金守恒'] and totals['balanced']
    assert totals['申报合计_股'] == '2500'
    assert Decimal(totals['实缴合计_CNY']) == Decimal(totals['发行款合计_CNY']) + Decimal(totals['应退合计_CNY'])


def test_gate_blocks_before_cutoff_and_with_pending():
    totals = {'现金守恒': True, '股份守恒': True}
    gate = evaluate_gate(OFFERING, totals, 1, '2026-11-18T14:59:59Z', '待发布')
    assert not gate['can_publish']
    assert set(gate['blockers']) == {'CUTOFF', 'PENDING_PAYMENT'}
    assert gate['reached_cutoff'] is False


def test_gate_blocks_on_cash_and_share_break():
    totals = {'现金守恒': False, '股份守恒': False}
    gate = evaluate_gate(OFFERING, totals, 0, '2026-11-18T16:00:00Z', '待发布')
    assert set(gate['blockers']) == {'CASH_DIFF', 'SHARE_DIFF'}


def test_gate_passes_after_cutoff_when_clean_and_locks():
    totals = {'现金守恒': True, '股份守恒': True}
    gate = evaluate_gate(OFFERING, totals, 0, '2026-11-18T15:00:00Z', '待发布')
    assert gate['can_publish'] and gate['reached_cutoff']
    locked = evaluate_gate(OFFERING, totals, 0, '2026-11-18T16:00:00Z', '已发布')
    assert locked['blockers'] == ['LOCKED']
