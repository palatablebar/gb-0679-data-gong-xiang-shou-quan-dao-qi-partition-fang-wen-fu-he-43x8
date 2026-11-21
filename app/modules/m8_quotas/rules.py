"""M8 共享额度占用定版。

复用流水线入口预建的唯一台账（app.quota.get_bundle），把每条采纳行关联到
其 request_id 对应的唯一额度结论；授权（M3）、对账（M6）、证据导出（M7/归档）
引用的是同一个 bundle，不存在第二份记账。

参数 parameters.quotas.limits 登记额度池限额（缺省 shared-default=8）；
parameters.quotas.events 可显式给出 arrive/retry/withdraw/settle/settle_failed；
未显式提供事件时，每条采纳行合成一次到达事件（amount=1），旧样例无需改造。
M8 不丢弃上游行、不新增隔离，非法额度事件进入 metrics.invalid 留痕。
"""
from app.quota import get_bundle, conclusion_for


def evaluate(rows, context):
    bundle = get_bundle(rows, context)
    ledger = bundle["ledger"]

    out = [{**row, **conclusion_for(bundle, row.get("event_id"))} for row in rows]

    metrics = dict(ledger["metrics"])
    metrics.update({
        "pools": len(ledger["pools"]),
        "pool_limits": {p: v["limit"] for p, v in ledger["pools"].items()},
        "pool_available": {p: v["available"] for p, v in ledger["pools"].items()},
        "conclusions": len(bundle["conclusions"]),
        "invalid_events": ledger["metrics"]["invalid_events"],
    })
    return {"rows": out, "quarantine": [], "ledger": ledger, "metrics": metrics}
