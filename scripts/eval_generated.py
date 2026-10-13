"""
Chấm điểm SQL do một AI sinh ra, TRƯỚC và SAU khi qua lớp sửa lỗi VASP-SQL (mục 5.4, 5.7 kế hoạch).

Với mỗi câu:
  - EX trước : SQL gốc của AI cho kết quả giống gold không (tiêu chí BIRD: so tập các dòng)
  - chạy repair() -> UNCHANGED / REPAIRED / ABSTAIN
  - EX sau   : câu cuối cùng (đã sửa, hoặc giữ nguyên) có giống gold không

Chỉ số:
  EX trước / sau, RSR (câu sai được sửa thành đúng), RR (câu đúng bị làm sai),
  tỉ lệ sửa sai, tỉ lệ từ chối, tỉ lệ chạy được, chi phí LLM.

Cách chạy (từ thư mục gốc project):
    py scripts/eval_generated.py --gen g1 --split tune            # không dùng LLM ở Gate F
    py scripts/eval_generated.py --gen g1 --split tune --llm      # Gate F bằng LLM
Đọc:  results/generators/<gen>/<split>/outputs.jsonl
Ghi:  results/generators/<gen>/<split>/eval[_llm]/{per_sample.jsonl, summary.json, summary.md}
"""

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_common import GateProbe, ex_match, run_sql_full  # noqa: E402

from vasp_sql.adapters.bird_loader import BirdDatasetLoader  # noqa: E402
from vasp_sql.pipeline import repair  # noqa: E402
from vasp_sql.core.policy import RepairPolicy  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402
import threading  # noqa: E402


def load_jsonl(path):
    out = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # dòng ghi dở
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", required=True)
    ap.add_argument("--split", default="tune")
    ap.add_argument("--config", default="configs/bird_config.yaml")
    ap.add_argument("--llm", action="store_true", help="bật Gate F bằng LLM (đọc .env)")
    ap.add_argument("--semantic", action="store_true",
                    help="bật hướng 2: LLM đề xuất sửa lỗi ý nghĩa cục bộ + LLM độc lập kiểm chứng (ngầm bật --llm)")
    ap.add_argument("--ast-only", action="store_true",
                    help="đối chứng AST-only (mục 5.6): không đồ thị schema, không sửa giá trị, không LLM")
    ap.add_argument("--no-value-repair", action="store_true", help="ablation: tắt sửa giá trị lọc")
    ap.add_argument("--no-schema-graph", action="store_true", help="ablation: tắt đồ thị schema (sửa thiếu JOIN)")
    ap.add_argument("--workers", type=int, default=1, help="số câu chấm song song")
    ap.add_argument("--judge-model", default=None,
                    help="mô hình giám khảo của hướng 2 (mặc định VT_MODEL_JUDGE trong .env, hoặc gpt-oss-120b)")
    ap.add_argument("--tag", default="", help="hậu tố tên thư mục kết quả, VD _v2")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--max-seconds", type=float, default=None)
    args = ap.parse_args()

    base = os.path.join("results", "generators", args.gen, args.split)
    outputs = load_jsonl(os.path.join(base, "outputs.jsonl"))
    if args.semantic:
        args.llm = True
    if args.ast_only and (args.llm or args.semantic):
        raise SystemExit("--ast-only không dùng cùng --llm/--semantic")
    if args.no_value_repair:
        args.tag = "_no_value" + args.tag
    if args.no_schema_graph:
        args.tag = "_no_graph" + args.tag
    out_dir = os.path.join(base, ("eval_ast_only" if args.ast_only else ("eval_semantic" if args.semantic else ("eval_llm" if args.llm else "eval"))) + args.tag)
    os.makedirs(out_dir, exist_ok=True)
    per_path = os.path.join(out_dir, "per_sample.jsonl")

    loader = BirdDatasetLoader(args.config)
    by_id = {s.question_id: s for s in loader.samples}
    llm = None
    if args.llm:
        from vasp_sql.llm import LLMClient
        llm = LLMClient()
    judge = None
    if args.semantic:
        from vasp_sql.llm import LLMClient
        judge = LLMClient(model=args.judge_model or os.environ.get("VT_MODEL_JUDGE", "gpt-oss-120b"))
        print(f"Hướng 2: đề xuất = {llm.model}, giám khảo = {judge.model}")

    policy = RepairPolicy(allow_semantic_repair=bool(args.semantic))
    if args.ast_only:
        policy = RepairPolicy(use_schema_graph=False, allow_value_repair=False)
    if args.no_value_repair or args.no_schema_graph:
        policy = policy.model_copy(update={"allow_value_repair": not args.no_value_repair and policy.allow_value_repair,
                                           "use_schema_graph": not args.no_schema_graph and policy.use_schema_graph})
    records = load_jsonl(per_path) if args.resume else []
    done = {r["sample_id"] for r in records}
    todo = [o for o in outputs if o["sample_id"] not in done]
    t0 = time.time()
    lock = threading.Lock()
    stop = threading.Event()

    def eval_one(o):
        if stop.is_set():
            return None
        if args.max_seconds and time.time() - t0 > args.max_seconds:
            stop.set()
            return None
        s = by_id[o["sample_id"]]
        db_path = loader.get_db_path(s.db_id)
        schema = loader.get_schema(s.db_id)
        ok, gold_rows, _ = run_sql_full(db_path, s.golden_sql)
        rec = {"sample_id": s.question_id, "db_id": s.db_id, "difficulty": s.difficulty,
               "generated_sql": o.get("cleaned_sql"), "gold_ok": ok}
        pred = o.get("cleaned_sql")
        if not pred:
            rec.update(status="NO_SQL", ex_before=False, ex_after=False, final_sql=None)
        else:
            ok_b, _, err_b = run_sql_full(db_path, pred)
            rec["exec_ok_before"] = ok_b
            rec["exec_error_before"] = err_b
            rec["ex_before"] = ex_match(db_path, pred, gold_rows)
            if not rec["ex_before"]:
                gate, msg = GateProbe().first_failing_gate(s.question, pred, schema, db_path, s.evidence)
                rec["first_failing_gate"] = gate
                rec["gate_message"] = (msg or "")[:200]
            try:
                for c in (llm, judge):
                    if c is not None:
                        c.begin_sample()
                res = repair(question=s.question, sql=pred, schema=schema, db_path=db_path,
                             evidence=s.evidence, llm=llm, judge_llm=judge, policy=policy)
                usage = [c.end_sample() for c in (llm, judge) if c is not None]
                rec["repair_prompt_tokens"] = sum(u["prompt_tokens"] for u in usage)
                rec["repair_completion_tokens"] = sum(u["completion_tokens"] for u in usage)
                final = res.final_sql if res.status.value == "REPAIRED" else pred
                rec.update(status=res.status.value, final_sql=final,
                           patches=[p.patch_type for p in res.patches_applied],
                           patch_notes=[p.replacement_fragment for p in res.patches_applied],
                           abstain_reason=res.abstain_reason,
                           llm_calls=res.metrics.get("llm_calls", 0),
                           intent_log=res.metrics.get("intent_log", []),
                           repair_latency_ms=res.metrics.get("latency_ms"),
                           ast_nodes_changed=res.metrics.get("ast_nodes_changed"))
                rec["ex_after"] = rec["ex_before"] if final == pred else ex_match(db_path, final, gold_rows)
                rec["exec_ok_after"] = ok_b if final == pred else run_sql_full(db_path, final)[0]
            except Exception as e:  # noqa: BLE001
                rec.update(status="CRASH", error=f"{type(e).__name__}: {e}", final_sql=pred,
                           ex_after=rec["ex_before"], exec_ok_after=ok_b)
        rec["gen_meta"] = o.get("metadata", {})
        return rec

    with open(per_path, "a" if args.resume else "w", encoding="utf-8") as fout, \
            ThreadPoolExecutor(max_workers=args.workers) as pool:
        for rec in pool.map(eval_one, todo):
            if rec is None:
                continue
            with lock:
                records.append(rec)
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fout.flush()
    finished = len(records) >= len(outputs)

    if not finished:
        print(f"CHƯA XONG: {len(records)}/{len(outputs)} — chạy lại với --resume")
        return
    summarize(records, out_dir, args, llm)


def summarize(recs, out_dir, args, llm):
    n = len(recs)
    pct = lambda a, b: round(100.0 * a / b, 2) if b else 0.0  # noqa: E731
    ex_b = sum(r["ex_before"] for r in recs)
    ex_a = sum(r["ex_after"] for r in recs)
    wrong_b = [r for r in recs if not r["ex_before"]]
    right_b = [r for r in recs if r["ex_before"]]
    fixed = [r for r in wrong_b if r["ex_after"]]
    broken = [r for r in right_b if not r["ex_after"]]
    changed = [r for r in recs if r.get("status") == "REPAIRED"]
    false_rep = [r for r in changed if not r["ex_after"]]
    st = Counter(r.get("status") for r in recs)
    gates = Counter(r.get("first_failing_gate") or "none (chạy được nhưng sai kết quả)" for r in wrong_b
                    if r.get("status") != "NO_SQL")
    valid_b = sum(1 for r in recs if r.get("exec_ok_before"))
    valid_a = sum(1 for r in recs if r.get("exec_ok_after"))
    by_diff = defaultdict(lambda: [0, 0, 0])
    for r in recs:
        d = by_diff[r["difficulty"]]
        d[0] += 1; d[1] += r["ex_before"]; d[2] += r["ex_after"]
    llm_calls = sum(r.get("llm_calls", 0) for r in recs)
    gen_calls = sum((r.get("gen_meta") or {}).get("llm_calls", 0) or 0 for r in recs)

    summary = {
        "generator": args.gen, "split": args.split, "gate_f_llm": bool(args.llm), "semantic_repair": bool(getattr(args, "semantic", False)), "ast_only": bool(getattr(args, "ast_only", False)), "n": n,
        "EX_before_pct": pct(ex_b, n), "EX_after_pct": pct(ex_a, n), "EX_gain_points": round(pct(ex_a, n) - pct(ex_b, n), 2),
        "wrong_before": len(wrong_b), "fixed": len(fixed), "RSR_pct": pct(len(fixed), len(wrong_b)),
        "correct_before": len(right_b), "broken": len(broken), "RR_pct": pct(len(broken), len(right_b)),
        "repaired": len(changed), "false_repair": len(false_rep), "false_repair_rate_pct": pct(len(false_rep), len(changed)),
        "status_counts": dict(st), "abstain_rate_pct": pct(st.get("ABSTAIN", 0), n),
        "validity_before_pct": pct(valid_b, n), "validity_after_pct": pct(valid_a, n),
        "why_wrong_before": dict(gates.most_common()),
        "by_difficulty": {k: {"n": v[0], "EX_before": pct(v[1], v[0]), "EX_after": pct(v[2], v[0])} for k, v in by_diff.items()},
        "llm_calls_generation": gen_calls, "llm_calls_repair": llm_calls,
    }
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    L = [f"# {args.gen} trên phần '{args.split}' — trước / sau lớp sửa lỗi" + (" (AST-only: không đồ thị schema, không sửa giá trị, không LLM)" if getattr(args, "ast_only", False) else " (hướng 2: sửa ý nghĩa cục bộ bằng LLM)" if args.semantic else (" (Gate F bằng LLM)" if args.llm else "")), "",
         f"Số câu: **{n}**", "",
         "| Chỉ số | Giá trị |", "|---|---:|",
         f"| EX trước sửa | {summary['EX_before_pct']}% ({ex_b}/{n}) |",
         f"| **EX sau sửa** | **{summary['EX_after_pct']}%** ({ex_a}/{n}) |",
         f"| Câu sai được sửa thành đúng (RSR) | {len(fixed)}/{len(wrong_b)} ({summary['RSR_pct']}%) |",
         f"| Câu đúng bị làm sai (RR) | {len(broken)}/{len(right_b)} ({summary['RR_pct']}%) |",
         f"| Số câu bị thay đổi | {len(changed)} (sửa sai: {len(false_rep)}) |",
         f"| Từ chối sửa (ABSTAIN) | {st.get('ABSTAIN', 0)} ({summary['abstain_rate_pct']}%) |",
         f"| Chạy được: trước → sau | {summary['validity_before_pct']}% → {summary['validity_after_pct']}% |",
         f"| Lượt gọi LLM: sinh SQL / sửa lỗi | {gen_calls} / {llm_calls} |",
         "", "## Vì sao câu sai (trước khi sửa)", "", "| Cổng đầu tiên bị trượt | Số câu |", "|---|---:|"]
    L += [f"| {g} | {c} |" for g, c in gates.most_common()]
    L += ["", "## Theo độ khó", "", "| Độ khó | Số câu | EX trước | EX sau |", "|---|---:|---:|---:|"]
    for k in ("simple", "moderate", "challenging"):
        if k in summary["by_difficulty"]:
            d = summary["by_difficulty"][k]
            L.append(f"| {k} | {d['n']} | {d['EX_before']}% | {d['EX_after']}% |")
    with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
