# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV。

## 共享额度（M8 `/api/quotas`）

POST `parameters.quotas.limits` 为 `{额度池: 限额}`，缺省登记 `shared-default=8`；`parameters.quotas.events` 为只追加事件序列，字段 `type`（arrive/retry/withdraw/settle/settle_failed）、`request_id`、`pool`、`amount`（Decimal正数）。未给事件时按上游采纳行合成单位额到达事件。返回 `ledger.pools`（每池 limit/reserved/occupied/used/available/oversubscribed 与每请求 state/trace）、`invalid`（隔离的非法事件）和 metrics。台账在流水线入口只构建一次，M3 授权、M6 对账、M8 占用页与归档导出共用同一结论。证据 CSV 中 `quota_<state>` 行为逐请求占用结论，`quota_rejected` 为非法事件留痕。
