"""共享额度占用台账（确定性事件溯源）。

裁决：到达时预留（reserve-on-arrival），而非执行后占用再回退。
理由：区域团队会重试与撤回，执行后记账在并发下必然超用；到达预留用先到先占
守住总额度，撤回释放后由台账重算把额度让给排队请求，重试按请求号幂等不新增占用。

所有结论只由 (额度池限额, 事件序列) 重放得到，不保留可变计数器：
同一输入永远得到同一份每请求唯一结论，授权、对账、证据导出共用它。
"""
from decimal import Decimal
from app.errors import DomainError
from app.values import number

ARRIVE = "arrive"          # 请求到达：尝试预留
RETRY = "retry"            # 同一请求重试：幂等，不新增占用
WITHDRAW = "withdraw"      # 区域团队撤回：预留/占用失效
SETTLE = "settle"          # 执行结算：预留转为实际占用
SETTLE_FAILED = "settle_failed"  # 结算切换失败：保留旧版结论

EVENTS = {ARRIVE, RETRY, WITHDRAW, SETTLE, SETTLE_FAILED}
# 结论状态机
HELD = "held"              # 已预留（到达且额度足够）
WAITING = "waiting"        # 排队（到达时额度不足，撤回后可能被重算提升）
OCCUPIED = "occupied"      # 已结算实际占用
WITHDRAWN = "withdrawn"    # 已撤回，额度失效
REJECTED = "rejected"      # 非法事件，不进入台账结论
STATES = (HELD, WAITING, OCCUPIED, WITHDRAWN, REJECTED)

# 每种事件允许作用的前置状态；retry 对到达类状态幂等。
ALLOWED = {
    ARRIVE: (None,),
    RETRY: (HELD, WAITING, OCCUPIED),
    WITHDRAW: (HELD, WAITING, OCCUPIED),
    SETTLE: (HELD,),
    SETTLE_FAILED: (HELD, OCCUPIED),
}


def validate_event(event):
    """校验事件形状，返回归一化后的 (pool, request_id, kind, amount)。"""
    if not isinstance(event, dict):
        raise DomainError("BAD_QUOTA_EVENT", "额度事件需要对象")
    kind = str(event.get("type", ""))
    if kind not in EVENTS:
        raise DomainError("BAD_QUOTA_EVENT", f"未知额度事件类型: {kind!r}")
    request_id = str(event.get("request_id", "")).strip()
    if not request_id:
        raise DomainError("BAD_QUOTA_EVENT", "额度事件缺少 request_id")
    pool = str(event.get("pool", "shared-default")).strip() or "shared-default"
    amount = number(event.get("amount", "1"))
    if amount <= 0:
        raise DomainError("BAD_QUOTA_EVENT", "占用额度必须为正数")
    return pool, request_id, kind, amount


def _reject(event, code, detail):
    return {**event, "reason": code, "detail": detail, "state": REJECTED}


def replay(events, limits):
    """重放事件序列，返回每个额度池的唯一占用结论。

    events: 已校验或待校验事件列表（按到达顺序，可带 seq/_line 作为溯源）。
    limits: {pool: Decimal 限额}，未登记池拒绝写入。
    返回 {"pools": {pool: {"limit","used","reserved","available","requests": [...]}},
          "invalid": [...], "metrics": {...}}
    """
    limits = {str(k): number(v) for k, v in limits.items()}
    # pool -> request_id -> 台账记录
    pools = {}
    invalid = []

    for index, raw in enumerate(events):
        event = dict(raw)
        try:
            pool, request_id, kind, amount = validate_event(event)
        except DomainError as exc:
            invalid.append(_reject(event, exc.code, exc.message))
            continue
        if pool not in limits:
            invalid.append(_reject(event, "UNKNOWN_POOL", f"额度池未登记: {pool}"))
            continue

        bucket = pools.setdefault(pool, {})
        record = bucket.get(request_id)
        line = event.get("_line", event.get("seq", index + 1))

        if record is None:
            if kind != ARRIVE:
                # 未到达就重试/撤回/结算：没有可幂等的旧结论，拒绝但不污染额度。
                invalid.append(_reject(event, "REQUEST_NOT_ARRIVED", "请求尚未到达，无占用结论可作用"))
                continue
            record = {
                "request_id": request_id, "pool": pool, "amount": str(amount),
                "state": WAITING, "arrival_seq": index, "trace": [],
                "retry_count": 0, "settle_failures": 0,
            }
            bucket[request_id] = record
            current = WAITING
        else:
            current = record["state"]
            if amount != number(record["amount"]):
                # 同一请求重试携带不同额度：幂等键冲突，拒绝改写。
                invalid.append(_reject(event, "QUOTA_AMOUNT_CONFLICT",
                                       f"请求 {request_id} 重试额度与首次到达不一致"))
                continue
            if current not in ALLOWED[kind]:
                invalid.append(_reject(event, "INVALID_TRANSITION",
                                       f"状态 {current} 不接受事件 {kind}"))
                continue

        entry = {"seq": index, "line": line, "type": kind}

        if kind == ARRIVE:
            # 理论上 record 已新建；先按先到先占尝试预留。
            held = _held_total(bucket)
            if held + amount <= limits[pool]:
                record["state"] = HELD
                entry["decision"] = "reserved"
            else:
                record["state"] = WAITING
                entry["decision"] = "queued"
        elif kind == RETRY:
            # 幂等：维持原结论，仅登记重试，绝不二次预留。
            record["retry_count"] += 1
            entry["decision"] = "idempotent_keep_" + record["state"]
        elif kind == WITHDRAW:
            record["state"] = WITHDRAWN
            entry["decision"] = "released"
            _promote_waiting(bucket, limits[pool])  # 撤回释放后重算排队队列
        elif kind == SETTLE:
            record["state"] = OCCUPIED
            entry["decision"] = "occupied"
        elif kind == SETTLE_FAILED:
            # 结算切换失败：保留旧版结论（held/occupied 不动），只留失败证据。
            record["settle_failures"] += 1
            entry["decision"] = "rollback_keep_" + record["state"]

        record["trace"].append(entry)

    return _assemble(pools, limits, invalid)


def _state_label(state):
    return {HELD: "已预留", WAITING: "排队等待",
            OCCUPIED: "已占用", WITHDRAWN: "已撤回失效"}.get(state, "无结论")


def _held_total(bucket):
    return sum((number(r["amount"]) for r in bucket.values()
                if r["state"] in (HELD, OCCUPIED)), Decimal(0))


def _promote_waiting(bucket, limit):
    """撤回释放额度后按到达顺序重算：能满足的排队请求提升为已预留。"""
    used = _held_total(bucket)
    for record in sorted((r for r in bucket.values() if r["state"] == WAITING),
                         key=lambda r: r["arrival_seq"]):
        amount = number(record["amount"])
        if used + amount <= limit:
            record["state"] = HELD
            record["trace"].append({
                "seq": record["trace"][-1]["seq"] if record["trace"] else -1,
                "line": "recalc", "type": "recalc",
                "decision": "promoted_after_release",
            })
            used += amount


def _assemble(pools, limits, invalid):
    out = {}
    totals = {"requests": 0, "held": 0, "waiting": 0, "occupied": 0,
              "withdrawn": 0, "retries": 0, "settle_failures": 0,
              "reserved_amount": Decimal(0), "occupied_amount": Decimal(0)}
    for pool, bucket in sorted(pools.items()):
        requests = sorted(bucket.values(), key=lambda r: r["arrival_seq"])
        for record in requests:
            record.setdefault("quota_conclusion", _state_label(record["state"]))
        reserved = sum((number(r["amount"]) for r in requests if r["state"] == HELD), Decimal(0))
        occupied = sum((number(r["amount"]) for r in requests if r["state"] == OCCUPIED), Decimal(0))
        used = reserved + occupied
        limit = limits[pool]
        out[pool] = {
            "limit": str(limit),
            "reserved": str(reserved),
            "occupied": str(occupied),
            "used": str(used),
            "available": str(limit - used),
            "oversubscribed": used > limit,
            "requests": requests,
        }
        for r in requests:
            totals["requests"] += 1
            totals[r["state"]] = totals.get(r["state"], 0) + 1
            totals["retries"] += r["retry_count"]
            totals["settle_failures"] += r["settle_failures"]
        totals["reserved_amount"] += reserved
        totals["occupied_amount"] += occupied
    totals["invalid_events"] = len(invalid)
    totals["reserved_amount"] = str(totals["reserved_amount"])
    totals["occupied_amount"] = str(totals["occupied_amount"])
    return {"pools": out, "invalid": invalid, "metrics": totals}
