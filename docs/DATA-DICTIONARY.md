# 授权定义

资源orders与订单等价，customers与客户等价。principal为主体文本，不支持模糊主体。partition为YYYY-MM；分区授权[start,end)不含end月。授权时间也为[start,end)，到期时刻立即失效。

每条授权包含主体、资源、生效窗、分区窗和allow/deny。全部条件必须同时匹配。拒绝优先，多个允许不能覆盖任何显式拒绝；无匹配默认拒绝。匹配规则ID按字典序稳定排序，冲突显示全部允许与拒绝证据。

observed为真实访问执行结果allowed或blocked；reported为上游0/1判定，留作证据。expected为实际授权复算，分区模块重建reported为执行结果便于二值对账。越权放行和误拦截分别计数，禁止用相互抵消的总数代替逐条核查。

业务ID和超大整数按文本保形；Decimal参与数量金额计算，对外序列化为字符串。哨兵集合定义在app/values.py，缺失值不能当零。所有时间带UTC偏移，先转UTC比较，业务日按指定地区划分。区间统一[start,end)。重复业务键保留第一条、完全相同行计重复；值冲突行隔离且保留原值。空文件、全空白、仅表头分开拒绝。

运行清单包括输入sha256、规则版本、参数、生成时刻与耗时。源行编号以CSV解析器的物理结束行号表示，引号内换行不增加记录数。输入超过4MB返回413，非法表头和类型返回422，失效版本复核返回409。

## 共享额度占用（M8）

额度结论按请求号唯一，采用到达时预留：arrive 在限额内转为 held（已预留），超额进入 waiting（排队）且总额度绝不超用；retry 按 request_id 幂等，只累加 retry_count 并保留原结论，不二次预留；withdraw 使 held/waiting/occupied 转为 withdrawn（额度失效），随后按到达顺序重算排队队列，能满足的 waiting 提升为 held；settle 仅允许 held → occupied；settle_failed 不改写 held/occupied，仅累加 settle_failures 并在 trace 记录 rollback_keep_*。

amount 为正 Decimal，缺失按 1；未登记额度池、未到达先作用、重试额度与首达不一致、非法状态迁移均隔离为 rejected 事件，不进入额度合计。状态与轨迹（trace）随每行 quota_* 字段输出，并在归档 CSV 以 quota_<state>/quota_rejected 留痕。