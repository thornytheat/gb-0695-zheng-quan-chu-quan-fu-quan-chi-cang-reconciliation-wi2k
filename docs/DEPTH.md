# 深度登记 证券除权复权与持仓对账

- 规模：中型
- 使用者与工作流：券商数据校验员，归一公司行动与成交持仓文件，复算除权因子和股数现金。
- 核心规则：除权参考价=(前收盘-每股现金+配股价×配股比例)/(1+送转比例+配股比例)，按登记日判定权益，零碎股单列。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 行动接收 | 运行行动接收并复核证据 | `POST /api/m1`；`/work/1` | `app/modules/m1_receiving/rules.py` | `tests/test_m1.py` |
| M2 证券映射 | 运行证券映射并复核证据 | `POST /api/m2`；`/work/2` | `app/modules/m2_mapping/rules.py` | `tests/test_m2.py` |
| M3 权益登记 | 运行权益登记并复核证据 | `POST /api/m3`；`/work/3` | `app/modules/m3_registry/rules.py` | `tests/test_m3.py` |
| M4 除权因子 | 运行除权因子并复核证据 | `POST /api/m4`；`/work/4` | `app/modules/m4_factors/rules.py` | `tests/test_m4.py` |
| M5 持仓重算 | 运行持仓重算并复核证据 | `POST /api/m5`；`/work/5` | `app/modules/m5_holdings/rules.py` | `tests/test_m5.py` |
| M6 现金对账 | 运行现金对账并复核证据 | `POST /api/m6`；`/work/6` | `app/modules/m6_cash/rules.py` | `tests/test_m6.py` |
| M7 修订回放 | 运行修订回放并复核证据 | `POST /api/m7`；`/work/7` | `app/modules/m7_revisions/rules.py` | `tests/test_m7.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-02 | BOM | 静默覆盖或误计 | 显式裁决并保留证据 | `app/modules/m1_receiving/rules.py` | `tests/test_m1.py::test_bom_header` |
| TRAP-06 | 全角千分位 | 静默覆盖或误计 | 显式裁决并保留证据 | `app/modules/m2_mapping/rules.py` | `tests/test_m2.py::test_fullwidth_decimal` |
| TRAP-08 | 证券前导零 | 静默覆盖或误计 | 显式裁决并保留证据 | `app/modules/m2_mapping/rules.py` | `tests/test_m2.py::test_security_leading_zero` |
| TRAP-09 | 大整数保形 | 静默覆盖或误计 | 显式裁决并保留证据 | `app/modules/m2_mapping/rules.py` | `tests/test_m2.py::test_large_id_unchanged` |
| TRAP-10 | Decimal除权因子 | 静默覆盖或误计 | 显式裁决并保留证据 | `app/modules/m4_factors/rules.py` | `tests/test_m4.py::test_factor_precise` |
| TRAP-14 | 行动定义冲突 | 静默覆盖或误计 | 显式裁决并保留证据 | `app/modules/m2_mapping/rules.py` | `tests/test_m2.py::test_conflicting_action_removed` |
| TRAP-24 | 现金尾差 | 静默覆盖或误计 | 显式裁决并保留证据 | `app/modules/m5_holdings/rules.py` | `tests/test_m5.py::test_largest_remainder_conservation` |
| TRAP-25 | 行动版本选择 | 静默覆盖或误计 | 显式裁决并保留证据 | `app/modules/m2_mapping/rules.py` | `tests/test_m2.py::test_latest_action_chosen` |

## 对账规则

输入记录 = 采信记录 + 隔离记录 + 相同行重复。重复表头与空行单独记数。业务合计按阶段中的守恒或差值列复核。运行清单有原文SHA256、规则版本与耗时；相同规则版本下同文重复提交复用批次。

## 已知不覆盖

部署为本地单用户工具，不连接外部上游系统。CSV单文件5MB及一万条业务记录上限；不推断未提供的未知数据。

## 配股认购发行核对子系统（app/rights/）

| 层 | 文件 | 职责 |
|---|---|---|
| 领域引擎 | `app/rights/engine.py` | 纯函数：单户 allocate 裁决、build_ledger 逐户台账与守恒、evaluate_gate 发布闸门 |
| 读取裁决 | `app/rights/reader.py` | 申报/缴款 CSV 表头校验、单行隔离（行号+原因码）、编码探测 |
| 持久层 | `app/rights/store.py` | SQLite 四表；消息主键幂等、冲突/迟到隔离、发布快照与乐观锁 |
| 服务编排 | `app/rights/service.py` | 入库后确定性重算，发布后回显快照 |
| HTTP/页面 | `app/rights/handler.py`、`templates/rights.html`、`static/rights.js` | 接口与复核台 |

| 陷阱 | 现象 | 本项目处理 | 测试 |
|---|---|---|---|
| 重跑重复发行 | 同批申报/缴款再次提交导致翻倍 | 主键去重，重复计 DUPLICATE 且不新增发行 | `test_duplicate_rerun_does_not_double_issue` |
| 同编号覆盖 | 后到的同 msg_id/pay_id 改写已确认结果 | 内容冲突 CONFLICT 隔离，保留首次 | `test_conflicting_same_id_keeps_first_and_quarantines` |
| 截止日不变 | 截止后申报挤入发行 | received_at>cutoff 判 LATE 隔离 | `test_late_declaration_after_cutoff_is_quarantined` |
| 未核实先入账 | 未到账资金被计入发行、未缴款拿股 | verified=0 不计实缴，闸门 PENDING_PAYMENT 拦截 | `test_unverified_payment_never_counted_as_issued` |
| 现金对账不平 | 实缴与发行款对不上 | 恒等 实缴=发行款+应退，孤儿缴款 ORPHAN 隔离不入发行 | `test_payment_without_declaration_is_orphan_not_issued` |
| 发布后漂移 | 发布后新消息改变结果 | 发布写快照并锁定，入库 409 | `test_publish_freezes_results_and_rejects_later_messages` |
