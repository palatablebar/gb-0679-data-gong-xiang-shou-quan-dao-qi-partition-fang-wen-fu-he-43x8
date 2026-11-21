from decimal import Decimal
from app.reasons import sources
from app.errors import DomainError
from app.quota import get_bundle, conclusion_for
def evaluate(rows,context):
    intake=context.get("intake")
    bad=context.get("quarantine",[])
    accepted=set(x for row in rows for x in sources(row))
    rejected=set(x for row in bad for x in sources(row))
    if accepted & rejected: raise DomainError("LINEAGE_OVERLAP","同一来源行被重复裁决")
    m={"accepted_inputs":len(accepted),"quarantine_inputs":len(rejected),"output_records":len(rows)}
    if intake:
        excluded=intake["duplicate"]+intake["blank"]+intake["repeated_headers"]
        m.update(input=intake["input"],excluded=excluded,balanced=intake["input"]==len(accepted)+len(rejected)+excluded)
    else:m.update(input=len(accepted)+len(rejected),excluded=0,balanced=True)
    quota=get_bundle(rows,context)
    output=[]
    for row in rows:
        expected=Decimal(str(row.get("expected","0")));reported=Decimal(str(row.get("reported","0")))
        # 对账引用同一额度结论，并把占用额度纳入核对，不另设账。
        output.append({**row,"difference":str(expected-reported),"status":"通过" if expected==reported else "差异",**conclusion_for(quota,row.get("event_id"))})
    m.update(expected_total=str(sum((Decimal(str(r.get("expected","0"))) for r in rows),Decimal(0))),reported_total=str(sum((Decimal(str(r.get("reported","0"))) for r in rows),Decimal(0))),differences=sum(r["status"]=="差异" for r in output))
    ledger=quota["ledger"]["metrics"]
    m.update(quota_reserved=ledger["reserved_amount"],quota_occupied=ledger["occupied_amount"],
             quota_held=ledger["held"],quota_waiting=ledger["waiting"],
             quota_withdrawn=ledger["withdrawn"],quota_invalid=ledger["invalid_events"])
    return {"rows":output,"quarantine":[],"metrics":m}
