"""M8 共享额度占用：到达预留、幂等重试、撤回重算、结算失败回退。"""
import pytest
from decimal import Decimal
from app.modules.m8_quotas.ledger import (
    replay, ARRIVE, RETRY, WITHDRAW, SETTLE, SETTLE_FAILED,
    HELD, WAITING, OCCUPIED, WITHDRAWN, REJECTED,
)
from app.modules.m8_quotas.rules import evaluate
from app.quota import DEFAULT_POOL
from app.errors import DomainError

LIMITS = {DEFAULT_POOL: "3"}


def ev(kind, rid, amount="1", pool=DEFAULT_POOL, **extra):
    return {"type": kind, "request_id": rid, "pool": pool, "amount": amount, **extra}


def state_of(ledger, rid, pool=DEFAULT_POOL):
    return next(r for r in ledger["pools"][pool]["requests"] if r["request_id"] == rid)["state"]


def record_of(ledger, rid, pool=DEFAULT_POOL):
    return next(r for r in ledger["pools"][pool]["requests"] if r["request_id"] == rid)


def test_reserve_on_arrival_holds_within_limit_and_queues_beyond():
    "先到先占：限额内到达即预留，超出的进入排队且不超用"
    ledger = replay([ev(ARRIVE, "R1"), ev(ARRIVE, "R2"), ev(ARRIVE, "R3"), ev(ARRIVE, "R4")], LIMITS)
    book = ledger["pools"][DEFAULT_POOL]
    assert [state_of(ledger, r) for r in ("R1", "R2", "R3")] == [HELD, HELD, HELD]
    assert state_of(ledger, "R4") == WAITING
    assert book["used"] == "3" and book["available"] == "0"
    assert book["oversubscribed"] is False


def test_retry_is_idempotent_and_never_adds_occupation():
    "同一请求重试不能新增占用：结论与额度合计保持不变"
    events = [ev(ARRIVE, "R1"), ev(ARRIVE, "R2"), ev(ARRIVE, "R3"),
              ev(RETRY, "R1"), ev(RETRY, "R1"), ev(RETRY, "R2")]
    ledger = replay(events, LIMITS)
    book = ledger["pools"][DEFAULT_POOL]
    assert book["used"] == "3" and book["available"] == "0"
    r1 = record_of(ledger, "R1")
    assert r1["state"] == HELD and r1["retry_count"] == 2
    assert all(t["type"] == "retry" and t["decision"].startswith("idempotent") for t in r1["trace"] if t["type"] == "retry")
    assert ledger["metrics"]["requests"] == 3  # 重试不产生新请求结论


def test_withdraw_releases_and_recalculates_waiting_queue():
    "撤回后额度失效并重算：排队最早者按到达顺序提升为预留"
    events = [ev(ARRIVE, "R1"), ev(ARRIVE, "R2"), ev(ARRIVE, "R3"), ev(ARRIVE, "R4"),
              ev(ARRIVE, "R5"), ev(WITHDRAW, "R2")]
    ledger = replay(events, LIMITS)
    assert state_of(ledger, "R2") == WITHDRAWN
    assert state_of(ledger, "R4") == HELD          # 先到的 R4 被提升
    assert state_of(ledger, "R5") == WAITING       # R5 仍排队
    book = ledger["pools"][DEFAULT_POOL]
    assert book["used"] == "3" and book["available"] == "0"
    promoted = record_of(ledger, "R4")["trace"][-1]
    assert promoted["type"] == "recalc" and promoted["decision"] == "promoted_after_release"


def test_settle_converts_reservation_to_occupation():
    ledger = replay([ev(ARRIVE, "R1"), ev(SETTLE, "R1")], LIMITS)
    assert state_of(ledger, "R1") == OCCUPIED
    assert ledger["pools"][DEFAULT_POOL]["occupied"] == "1"


def test_settle_failure_keeps_previous_conclusion():
    "结算切换失败时保留旧版结论：held 不被改写，失败仅留痕"
    ledger = replay([ev(ARRIVE, "R1"), ev(SETTLE_FAILED, "R1")], LIMITS)
    record = record_of(ledger, "R1")
    assert record["state"] == HELD and record["settle_failures"] == 1
    assert record["trace"][-1]["decision"] == "rollback_keep_held"
    # 占用阶段结算失败同样保留 occupied
    ledger2 = replay([ev(ARRIVE, "R1"), ev(SETTLE, "R1"), ev(SETTLE_FAILED, "R1")], LIMITS)
    assert state_of(ledger2, "R1") == OCCUPIED


def test_settle_while_waiting_is_rejected_without_touching_quota():
    ledger = replay([ev(ARRIVE, "R1"), ev(ARRIVE, "R2"), ev(ARRIVE, "R3"),
                     ev(ARRIVE, "R4"), ev(SETTLE, "R4")], LIMITS)
    assert state_of(ledger, "R4") == WAITING
    assert ledger["invalid"][0]["reason"] == "INVALID_TRANSITION"
    assert ledger["pools"][DEFAULT_POOL]["used"] == "3"


def test_retry_with_different_amount_is_conflict_not_new_hold():
    ledger = replay([ev(ARRIVE, "R1", "2"), ev(RETRY, "R1", "5")], {DEFAULT_POOL: "10"})
    r1 = record_of(ledger, "R1")
    assert r1["state"] == HELD
    assert r1["amount"] == "2"
    assert ledger["invalid"][0]["reason"] == "QUOTA_AMOUNT_CONFLICT"


def test_event_before_arrival_and_unknown_pool_are_rejected():
    ledger = replay([ev(RETRY, "X"), ev(ARRIVE, "Y", pool="nope")], LIMITS)
    assert {x["reason"] for x in ledger["invalid"]} == {"REQUEST_NOT_ARRIVED", "UNKNOWN_POOL"}
    assert ledger["metrics"]["requests"] == 0


def test_invalid_event_shape_is_quarantined_or_raises():
    # 形状非法在 replay 内隔离为 rejected，不污染额度；validate_event 直接抛 DomainError。
    ledger = replay([{"type": "frobnicate", "request_id": "Z"}], LIMITS)
    assert ledger["invalid"][0]["state"] == REJECTED
    from app.modules.m8_quotas.ledger import validate_event
    with pytest.raises(DomainError):
        validate_event(ev(ARRIVE, "Z", "0"))


def test_retry_after_withdraw_is_rejected_not_revived():
    ledger = replay([ev(ARRIVE, "R1"), ev(WITHDRAW, "R1"), ev(RETRY, "R1")], LIMITS)
    assert state_of(ledger, "R1") == WITHDRAWN
    assert ledger["invalid"][0]["reason"] == "INVALID_TRANSITION"


def test_duplicate_arrival_same_request_keeps_first_conclusion():
    "重复到达（非 retry）按非法迁移拒绝，首条结论与额度不变"
    ledger = replay([ev(ARRIVE, "R1"), ev(ARRIVE, "R1")], LIMITS)
    assert state_of(ledger, "R1") == HELD
    assert ledger["invalid"][0]["reason"] == "INVALID_TRANSITION"
    assert ledger["pools"][DEFAULT_POOL]["used"] == "1"


def test_deterministic_same_input_same_conclusion():
    events = [ev(ARRIVE, "R1"), ev(ARRIVE, "R2"), ev(RETRY, "R1"), ev(WITHDRAW, "R2")]
    a = replay(events, LIMITS)
    b = replay(events, LIMITS)
    assert a["pools"] == b["pools"]


def test_multi_amount_decimal_capacity_guard():
    "Decimal 记账：部分额度请求不会把总额度拆穿"
    ledger = replay([ev(ARRIVE, "R1", "1.5"), ev(ARRIVE, "R2", "2"), ev(ARRIVE, "R3", "1")],
                    {DEFAULT_POOL: "4"})
    assert state_of(ledger, "R1") == HELD and state_of(ledger, "R2") == HELD
    assert state_of(ledger, "R3") == WAITING  # 仅剩 0.5，1 不够
    assert Decimal(ledger["pools"][DEFAULT_POOL]["used"]) == Decimal("3.5")


def test_stage_enriches_rows_with_single_conclusion():
    rows = [{"event_id": f"{i:05d}", "_sources": [i], "_line": i} for i in range(1, 6)]
    result = evaluate(rows, {"parameters": {"quotas": {"limits": {DEFAULT_POOL: "2"}}}})
    assert len(result["rows"]) == 5  # 不丢上游行
    by_id = {r["event_id"]: r for r in result["rows"]}
    assert by_id["00001"]["quota_state"] == HELD
    assert by_id["00003"]["quota_state"] == WAITING
    assert result["metrics"]["held"] == 2 and result["metrics"]["waiting"] == 3
    assert result["metrics"]["conclusions"] == 5


def test_stage_explicit_events_drive_full_lifecycle():
    params = {"parameters": {"quotas": {"limits": {DEFAULT_POOL: "2"}, "events": [
        ev(ARRIVE, "R1"), ev(ARRIVE, "R2"), ev(ARRIVE, "R3"),
        ev(RETRY, "R1"), ev(WITHDRAW, "R1"), ev(SETTLE_FAILED, "R2"),
    ]}}}
    result = evaluate([], params)
    ledger = result["ledger"]
    assert state_of(ledger, "R1") == WITHDRAWN
    assert state_of(ledger, "R2") == HELD          # 结算失败保留旧结论
    assert state_of(ledger, "R3") == HELD          # 撤回释放后提升
    assert result["metrics"]["retries"] == 1
    assert result["metrics"]["settle_failures"] == 1


def test_stage_default_limit_synthesizes_arrivals_from_rows(result):
    "真实样例：无需改造输入，采纳行自动合成到达事件，结论按请求号唯一"
    stage = result["stages"][7]
    ledger = stage["ledger"]
    request_ids = [r["request_id"] for book in ledger["pools"].values() for r in book["requests"]]
    assert len(request_ids) == len(set(request_ids))          # 每请求唯一结论
    assert ledger["pools"][DEFAULT_POOL]["limit"] == "8"
    assert all(r.get("quota_conclusion") for r in stage["rows"])
    # 授权（M3）、对账（M6）、占用页（M8）引用同一结论对象
    m3 = next(r for r in result["stages"][2]["rows"] if r["event_id"] == stage["rows"][0]["event_id"])
    m6 = next(r for r in result["stages"][5]["rows"] if r["event_id"] == m3["event_id"])
    m8 = next(r for r in stage["rows"] if r["event_id"] == m3["event_id"])
    assert m3["quota_state"] == m6["quota_state"] == m8["quota_state"]
    assert m3["quota_trace"] == m8["quota_trace"]
