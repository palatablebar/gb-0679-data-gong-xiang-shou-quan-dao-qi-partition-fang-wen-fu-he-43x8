"""共享额度结论的统一入口。

台账只在流水线入口计算一次并放入 context["quota_ledger"]，
授权定版（M3）、访问对账（M6）、额度占用页（M8）与证据导出共用同一份结论，
避免各阶段各自记账产生分歧。模块级预览缺少 context 时按需现算，结论仍由同一重放得到。
"""
from app.modules.m8_quotas.ledger import replay

DEFAULT_POOL = "shared-default"
DEFAULT_LIMIT = "8"


def _normalize(params):
    quotas = (params or {}).get("quotas", {}) or {}
    limits = dict(quotas.get("limits") or {})
    limits.setdefault(DEFAULT_POOL, DEFAULT_LIMIT)
    events = quotas.get("events")
    return limits, events


def _synthetic_arrivals(rows):
    events = []
    for row in rows:
        events.append({
            "type": "arrive",
            "request_id": str(row.get("event_id")),
            "pool": str(row.get("quota_pool") or DEFAULT_POOL),
            "amount": str(row.get("quota_amount") or "1"),
            "_line": (row.get("_sources") or [row.get("_line", 0)])[0],
        })
    return events


def _label(state):
    return {"held": "已预留", "waiting": "排队等待",
            "occupied": "已占用", "withdrawn": "已撤回失效"}.get(state, "无结论")


def build_ledger(rows, params):
    limits, explicit = _normalize(params)
    events = list(explicit) if explicit is not None else _synthetic_arrivals(rows)
    ledger = replay(events, limits)
    conclusions = {}
    for book in ledger["pools"].values():
        for record in book["requests"]:
            record = {**record, "quota_conclusion": _label(record["state"])}
            conclusions[record["request_id"]] = {
                "quota_pool": record["pool"],
                "quota_amount": record["amount"],
                "quota_state": record["state"],
                "quota_conclusion": record["quota_conclusion"],
                "quota_retry_count": record["retry_count"],
                "quota_settle_failures": record["settle_failures"],
                "quota_trace": record["trace"],
            }
    return {"ledger": ledger, "conclusions": conclusions, "limits": limits}


def get_bundle(rows, context):
    """取流水线预建台账；模块预览等无 context 场景按相同规则现算。"""
    bundle = (context or {}).get("quota_ledger")
    if bundle is None:
        bundle = build_ledger(rows, (context or {}).get("parameters", {}))
    return bundle


def conclusion_for(bundle, request_id):
    return bundle["conclusions"].get(str(request_id), {
        "quota_pool": DEFAULT_POOL, "quota_amount": "0",
        "quota_state": None, "quota_conclusion": "无额度请求",
        "quota_retry_count": 0, "quota_settle_failures": 0, "quota_trace": [],
    })
