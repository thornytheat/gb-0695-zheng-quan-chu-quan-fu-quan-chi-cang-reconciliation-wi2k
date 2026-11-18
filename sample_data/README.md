样例包含重复、同键冲突、空值和单位/格式变体。operations.csv为UTF-8 BOM，legacy-gb18030.csv为同内容旧编码。

rights_declarations.csv / rights_payments.csv 为配股认购样例（发行 RIGHTS-0001，配股价5.00，截止2026-11-18T15:00:00Z）。覆盖：足额获配、部分获配、超额应退、未到账零获配；完全重复（D001/P001，幂等跳过）、同编号冲突（D003/P003，保留首次）、迟到申报（D008，LATE隔离）、空股数（D009，NUMBER隔离）、未核实缴款（P005，不计入发行）、无申报孤儿缴款（P007/P008，ORPHAN隔离）。
