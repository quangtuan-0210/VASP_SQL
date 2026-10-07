"""
Step 1 — Regression check on BIRD gold SQL.

Gold SQL is assumed correct, so the ideal outcome for every sample is UNCHANGED.
  * REPAIRED  -> the layer modified a correct query (check whether the result still matches gold)
  * ABSTAIN   -> a gate / diagnosis falsely flagged a correct query (no harm to EX, but a false alarm)

Usage (from project root):
    py scripts/eval_gold.py                       # all samples whose DB exists locally
    py scripts/eval_gold.py --limit 200 --db california_schools
Outputs: results/gold_eval/{per_sample.jsonl, summary.json, summary.md}
"""

import argparse
import json
import os
import time
from collections import Counter, defaultdict

from eval_common import GateProbe, available_samples, ex_match, run_sql_full

from vasp_sql.adapters.bird_loader import BirdDatasetLoader
from vasp_sql.pipeline import repair


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/bird_config.yaml")
    ap.add_argument("--db", nargs="*", default=None, help="restrict to these db_ids")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default="results/gold_eval")
    ap.add_argument("--llm", action="store_true",
                    help="bật Gate F bằng LLM (đọc cấu hình server từ .env); câu trả lời được lưu đệm ở results/llm_cache.jsonl")
    ap.add_argument("--resume", action="store_true", help="continue an interrupted run (skip samples already in per_sample.jsonl)")
    ap.add_argument("--max-seconds", type=float, default=None, help="stop after this many seconds (use with --resume to run in chunks)")
    args = ap.parse_args()

    loader = BirdDatasetLoader(args.config)
    llm = None
    if args.llm:
        from vasp_sql.llm import LLMClient  # chỉ cần khi bật --llm
        llm = LLMClient()
        print(f"Gate F dùng LLM: {llm.model} @ {llm.base_url}")
    samples = available_samples(loader, args.db)
    if args.limit:
        samples = samples[: args.limit]
    os.makedirs(args.out, exist_ok=True)
    probe = GateProbe()

    records = []
    out_path = os.path.join(args.out, "per_sample.jsonl")
    done_ids = set()
    if args.resume and os.path.exists(out_path):
        records = load_jsonl(out_path)
        done_ids = {r["question_id"] for r in records}
        # rewrite cleanly (drops a half-written last line from an interrupted run)
        with open(out_path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    t0 = time.time()
    finished = True
    with open(out_path, "a" if args.resume else "w", encoding="utf-8") as fout:
        for i, s in enumerate(samples, 1):
            if s.question_id in done_ids:
                continue
            if args.max_seconds and time.time() - t0 > args.max_seconds:
                finished = False
                break
            db_path = loader.get_db_path(s.db_id)
            schema = loader.get_schema(s.db_id)
            rec = {"question_id": s.question_id, "db_id": s.db_id, "difficulty": s.difficulty,
                   "gold_sql": s.golden_sql}

            ok, gold_rows, gold_err = run_sql_full(db_path, s.golden_sql)
            if not ok:
                rec.update(status="GOLD_EXEC_ERROR", error=gold_err)
                records.append(rec)
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                continue

            try:
                res = repair(question=s.question, sql=s.golden_sql, schema=schema,
                             db_path=db_path, evidence=s.evidence, llm=llm)
                gate, gate_msg = (None, None)
                if res.status.value != "UNCHANGED":
                    gate, gate_msg = probe.first_failing_gate(s.question, s.golden_sql, schema, db_path, s.evidence)
                still_correct = True if res.status.value != "REPAIRED" else ex_match(db_path, res.final_sql, gold_rows)
                rec.update(
                    status=res.status.value,
                    final_sql=res.final_sql if res.status.value == "REPAIRED" else None,
                    patches=[p.patch_type for p in res.patches_applied],
                    abstain_reason=res.abstain_reason,
                    first_failing_gate=gate,
                    gate_message=gate_msg,
                    ex_after=still_correct,
                    latency_ms=res.metrics.get("latency_ms"),
                )
            except Exception as e:  # noqa: BLE001
                rec.update(status="CRASH", error=f"{type(e).__name__}: {e}")
            records.append(rec)
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fout.flush()
            if i % 100 == 0:
                print(f"  {i}/{len(samples)}  ({time.time() - t0:.0f}s)", flush=True)

    if not finished:
        print(f"PARTIAL: {len(records)}/{len(samples)} done — run again with --resume to continue")
        return
    summarize(records, args.out, time.time() - t0)


def load_jsonl(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # half-written line from an interrupted run
    return out


def summarize(records, out_dir, elapsed):
    valid = [r for r in records if r["status"] not in ("GOLD_EXEC_ERROR",)]
    n = len(valid)
    st = Counter(r["status"] for r in valid)
    regressions = [r for r in valid if r["status"] == "REPAIRED" and not r.get("ex_after")]
    gates = Counter(r.get("first_failing_gate") or "none" for r in valid if r["status"] != "UNCHANGED")

    # group the false-alarm messages into coarse buckets
    def bucket(msg):
        if not msg:
            return "(no gate failed — diagnosis/planner)"
        m = msg.lower()
        for key in ("could not be resolved", "does not exist in table", "does not exist in schema",
                    "unjoined", "timed out", "aggregation", "syntax"):
            if key in m:
                return key
        return msg[:70]
    buckets = Counter(bucket(r.get("gate_message")) for r in valid if r["status"] != "UNCHANGED")

    per_db = defaultdict(Counter)
    for r in valid:
        per_db[r["db_id"]][r["status"]] += 1
    per_diff = defaultdict(Counter)
    for r in valid:
        per_diff[r["difficulty"]][r["status"]] += 1

    pct = lambda k: round(100.0 * k / n, 2) if n else 0.0  # noqa: E731
    summary = {
        "evaluated": n,
        "gold_exec_errors": len(records) - n,
        "status_counts": dict(st),
        "unchanged_rate_pct": pct(st.get("UNCHANGED", 0)),
        "false_alarm_rate_pct": pct(n - st.get("UNCHANGED", 0)),
        "regression_count": len(regressions),
        "regression_rate_pct": pct(len(regressions)),
        "first_failing_gate": dict(gates),
        "false_alarm_buckets": dict(buckets.most_common()),
        "per_db": {k: dict(v) for k, v in sorted(per_db.items())},
        "per_difficulty": {k: dict(v) for k, v in per_diff.items()},
        "elapsed_s": round(elapsed, 1),
    }
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    lines = ["# Gold-SQL regression check (Step 1)", "",
             f"Evaluated: **{n}** samples (gold exec errors skipped: {len(records) - n})", "",
             "| Status | Count | % |", "|---|---:|---:|"]
    for k in ("UNCHANGED", "REPAIRED", "ABSTAIN", "CRASH"):
        lines.append(f"| {k} | {st.get(k, 0)} | {pct(st.get(k, 0))} |")
    lines += ["", f"Regression (REPAIRED and result no longer matches gold): **{len(regressions)}** ({pct(len(regressions))}%)",
              "", "## False alarms by first failing gate", "", "| Gate | Count |", "|---|---:|"]
    lines += [f"| {g} | {c} |" for g, c in gates.most_common()]
    lines += ["", "## False alarms by cause", "", "| Cause | Count |", "|---|---:|"]
    lines += [f"| {b} | {c} |" for b, c in buckets.most_common()]
    lines += ["", "## Per database", "", "| DB | UNCHANGED | REPAIRED | ABSTAIN | CRASH |", "|---|---:|---:|---:|---:|"]
    for db, c in sorted(per_db.items()):
        lines.append(f"| {db} | {c.get('UNCHANGED', 0)} | {c.get('REPAIRED', 0)} | {c.get('ABSTAIN', 0)} | {c.get('CRASH', 0)} |")
    with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
