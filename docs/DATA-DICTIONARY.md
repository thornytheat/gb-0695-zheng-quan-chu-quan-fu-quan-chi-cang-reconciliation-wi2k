# 数据定义

除权参考价=(前收盘-每股现金+配股价×配股比例)/(1+送转比例+配股比例)，按登记日判定权益，零碎股单列。

输入字段：record, kind, security, alias, event_date, record_date, ex_date, price, cash, bonus, rights, rights_price, shares, version, reported_cash。所有ID按字符串保留；空字符串、NULL、N/A、未知与“-”视为缺失，不能当作数值零。数值使用Decimal，输出为十进制字符串。重复业务键相同内容计重复，内容冲突隔离或按明确修订号裁决，不按文件顺序盲目覆盖。

每条证据有原始行号，隔离记录有原因码及原值。时间区间使用半开边界；日期定义在各模块规则内显式声明。输入表头可调序，不得缺列或重复；中文旧编码可上传。规则版本存入运行清单，重跑使用原始文件内容摘要与规则版本共同确定幂等键。复核使用revision乐观锁，陈旧提交返回409。

每行position是投资者在event_date结束时的登记快照；本示例同一证券同一除权日只有一项合并公司行动，不将多个日终快照相加。登记日当天有权益，次日新增持仓无权益。送转与配股产生的新增股份向下取整，零碎股保留。现金按每项行动总额四舍五入到分，再按最大余额分配，记录ID作稳定顺序。无税费及真实交易所连接。

# 配股认购发行核对数据定义

发行参数 `offering_id/security/rights_price/cutoff` 在创建时确定且不可变；截止时点 `cutoff` 固定，不随后续消息延后。申报消息键 `msg_id,account,shares,received_at`，缴款消息键 `pay_id,account,amount,verified,received_at`，ID 按字符串保留。金额 Decimal 量化到分，股数为非负整数，时间为 ISO 8601（尾缀 Z 按 UTC）。

逐户裁决（`app/rights/engine.py::allocate`）：可购股数=已核实实缴÷配股价，向下取整；获配股数=min(申报股数, 可购股数)；未获配=申报−获配；发行款=获配×配股价；应退=实缴−发行款；恒等关系 实缴=发行款+应退，申报=获配+未获配。未核实缴款（verified=0）不计入实缴，列入“未核实缴款”，对应账户在核实前不得获配。无申报的缴款不进账户台账，转 `ORPHAN` 隔离（待核查/应退），绝不计入发行。

隔离原因码：`COLUMNS` 列数不符、`MISSING` 必填为空、`NUMBER` 数值无效、`DATETIME` 时间格式无效、`FLAG` 核实标志无效、`DUPLICATE` 同编号完全重复（幂等跳过）、`CONFLICT` 同编号内容冲突（保留首次结果不覆盖）、`LATE` 申报晚于截止时点、`ORPHAN` 无申报缴款。发布闸门 blockers：`CUTOFF` 未到截止、`PENDING_PAYMENT` 有未核实缴款、`CASH_DIFF` 现金不守恒、`SHARE_DIFF` 股份不守恒、`LOCKED` 已发布锁定。发布时写入快照，之后重算只读快照，迟到消息在入库层即被 409 拒收。
