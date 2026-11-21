import csv,io,json
def build_archive(run):
    stream=io.StringIO(newline="");writer=csv.writer(stream)
    writer.writerow(["run_id","input_sha256","rule_version","kind","source_lines","reason","evidence"])
    meta=run["manifest"]
    for row in run["stages"][-1]["rows"]:
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"accepted",json.dumps(row.get("_sources",[])),row.get("status",""),json.dumps(row,ensure_ascii=False)])
    for row in run["stages"][-1].get("cumulative_quarantine",[]):
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"quarantine",json.dumps(row.get("_sources",[])),row.get("reason",""),json.dumps(row,ensure_ascii=False)])
    # \u5171\u4eab\u989d\u5ea6\u552f\u4e00\u5360\u7528\u7ed3\u8bba\uff1a\u6388\u6743\u3001\u5bf9\u8d26\u5171\u7528\u540c\u4e00\u4efd\u53f0\u8d26\uff0c\u8bc1\u636e\u5bfc\u51fa\u9010\u8bf7\u6c42\u7559\u75d5\u3002
    quota=run["stages"][-1].get("ledger")
    if quota:
        for pool,book in quota["pools"].items():
            for record in book["requests"]:
                writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"quota_"+record["state"],json.dumps([record["request_id"]]),"limit="+book["limit"]+" pool="+pool,json.dumps(record,ensure_ascii=False)])
        for bad in quota["invalid"]:
            writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"quota_rejected",json.dumps([str(bad.get("request_id",""))]),bad.get("reason",""),json.dumps(bad,ensure_ascii=False)])
    return "\ufeff"+stream.getvalue()
