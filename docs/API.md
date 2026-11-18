# 接口

`POST /api/run`：JSON中的csv文本，省略使用样例；或multipart字段file。最大5MB。

`POST /api/m1`至`POST /api/m7`：独立运行到指定模块，JSON结构同运行接口。

`GET /api/history`：历史批次。`POST /api/review`接收id、revision和decision（通过或退回）。`GET /api/evidence.csv`提供单一证据导出。

结构错误422，版本变化409，文件过大413；错误返回code与message。所有页面可从左侧七个模块入口访问。

`GET /api/runs/<id>`读取指定批次；页面与证据CSV通过run_id查询参数绑定批次，模块导航保留该参数。

JSON请求体必须是对象，数组、标量、null、损坏JSON及错误上传字段返回400，且不运行默认样例。

# 配股认购发行核对子系统

状态化、可重跑的配股发行核对，入口页面 `/rights`，默认发行编号 `RIGHTS-0001`。申报与缴款以消息主键（`msg_id`/`pay_id`）幂等，截止时点在发行创建时固定，发布后整账锁定。

- `POST /api/rights/offering`：创建/复用发行。JSON 字段 `offering_id`、`security`、`rights_price`、`cutoff`（ISO 8601）。同编号再次创建但参数不一致返回 409，参数一致则幂等复用。
- `POST /api/rights/ingest`：申报/缴款入库。JSON 字段 `declarations_csv`、`payments_csv`（可只给其一）、`offering_id`、可选 `as_of`；也支持 multipart，文件字段名为 `declarations`、`payments`。返回各类入库计数与最新核对视图。已发布发行返回 409。
- `GET /api/rights/reconcile?offering_id=&as_of=`：逐户台账、合计守恒、未核实缴款、隔离证据与发布闸门。`as_of` 省略取当前 UTC。
- `POST /api/rights/publish`：字段 `offering_id`、`revision`、可选 `as_of`。闸门未通过返回 409（code `GATE`），重复发布返回 409（code `LOCKED`），成功后写入不可变快照。
- `POST /api/rights/review`：字段 `offering_id`、`revision`、`decision`（通过/退回），乐观锁，陈旧版本 409。
- `GET /api/rights/offerings`：发行清单。
- `GET /api/rights/evidence.csv?offering_id=&as_of=`：账户台账、待核实缴款与隔离记录的证据导出，含截止时点与复核时点。

申报 CSV 表头：`msg_id,account,shares,received_at`；缴款 CSV 表头：`pay_id,account,amount,verified,received_at`。`verified` 取 1/0、true/false、是/否、已核实/未核实。结构性错误 422；单行业务错误进入隔离并保留行号与原因码。
