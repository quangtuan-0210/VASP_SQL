"""
Các phương pháp ĐỐI CHỨNG (mục 5.6 của kế hoạch), chạy trên cùng SQL do một AI sinh ra:

  rewrite        : "LLM viết lại" — đưa câu hỏi, gợi ý, schema và câu SQL cho LLM, yêu cầu kiểm tra
                   và trả về câu SQL đúng (viết lại cả câu nếu cần). Không định vị AST, không cổng kiểm chứng.
  execfb         : "sửa theo kết quả chạy" — như rewrite nhưng LLM được xem thêm kết quả chạy
                   (số dòng, vài dòng đầu, hoặc thông báo lỗi).
  execfb_verify  : như execfb, nhưng câu viết lại chỉ được nhận nếu GIÁM KHẢO ĐỘC LẬP (cùng giám khảo
                   với hướng 2) chọn nó thay vì câu gốc — tách riêng tác dụng của "sửa cục bộ".
  execfb_verify2 : như execfb_verify, nhưng giám khảo chấm 2 lần với vị trí 2 phương án đảo ngược;
                   chỉ nhận câu viết lại khi cả 2 lần đều chọn nó (chống thiên vị vị trí).

Quy tắc chung (công bằng với lớp sửa lỗi): câu viết lại không tách được SQL hoặc chạy lỗi -> giữ câu gốc.
Cùng mô hình (VT_MODEL_LLM, hiện là DeepSeek-V4-Pro) như 3 AI sinh SQL; số lượt gọi LLM được ghi lại để so chi phí.

Cách chạy:
    py scripts/eval_baselines.py --gen g1 --split tune --mode execfb --workers 6
Ghi: results/generators/<gen>/<split>/baseline_<mode>/{per_sample.jsonl, summary.json, summary.md}
"""

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from eval_common import ex_match, run_sql_full  # noqa: E402
from eval_generated import load_jsonl, summarize  # noqa: E402

from vasp_sql.adapters.bird_loader import BirdDatasetLoader  # noqa: E402
from vasp_sql.adapters.cleaner import extract_and_clean_sql  # noqa: E402
from vasp_sql.generators.schema_prompt import schema_ddl  # noqa: E402
from vasp_sql.llm import LLMClient  # noqa: E402
from vasp_sql.repair.semantic import SemanticRepairer, _norm  # noqa: E402
from vasp_sql.sandbox.executor import SQLiteSandboxExecutor  # noqa: E402

SYSTEM = "You are an expert in SQLite and Text-to-SQL."
PROMPT = """Database schema:
{schema}

Question: {question}
Hint: {evidence}

A system produced this SQL:
{sql}
{exec_info}
Check whether the SQL correctly answers the question. If it is correct, return it unchanged.
If it is wrong, return a corrected SQLite query.
Return only the final SQL inside a ```sql code block."""


def exec_info(feed) -> str:
    if feed is None:
        return ""
    if not feed.is_success:
        return f"\nExecution error: {feed.error_message}\n"
    rows = json.dumps((feed.result_preview or [])[:5], ensure_ascii=False, default=str)[:600]
    return f"\nExecution result: {feed.row_count} row(s). First rows: {rows}\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", required=True)
    ap.add_argument("--split", default="tune")
    ap.add_argument("--mode", required=True, choices=["rewrite", "execfb", "execfb_verify", "execfb_verify2"])
    ap.add_argument("--config", default="configs/bird_config.yaml")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--judge-model", default=None)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--max-seconds", type=float, default=None)
    args = ap.parse_args()
    args.llm, args.semantic = True, False

    base = os.path.join("results", "generators", args.gen, args.split)
    outputs = load_jsonl(os.path.join(base, "outputs.jsonl"))
    out_dir = os.path.join(base, f"baseline_{args.mode}")
    os.makedirs(out_dir, exist_ok=True)
    per_path = os.path.join(out_dir, "per_sample.jsonl")

    loader = BirdDatasetLoader(args.config)
    by_id = {s.question_id: s for s in loader.samples}
    llm = LLMClient()
    judge = LLMClient(model=args.judge_model or os.environ.get("VT_MODEL_JUDGE", "gpt-oss-120b")) \
        if args.mode.startswith("execfb_verify") else None

    records = load_jsonl(per_path) if args.resume else []
    done = {r["sample_id"] for r in records}
    todo = [o for o in outputs if o["sample_id"] not in done]
    t0 = time.time()
    stop = threading.Event()
    lock = threading.Lock()

    def one(o):
        if stop.is_set() or (args.max_seconds and time.time() - t0 > args.max_seconds):
            stop.set()
            return None
        s = by_id[o["sample_id"]]
        db_path = loader.get_db_path(s.db_id)
        _, gold_rows, _ = run_sql_full(db_path, s.golden_sql)
        pred = o.get("cleaned_sql")
        rec = {"sample_id": s.question_id, "db_id": s.db_id, "difficulty": s.difficulty,
               "generated_sql": pred, "gen_meta": o.get("metadata", {}), "llm_calls": 0}
        if not pred:
            rec.update(status="NO_SQL", ex_before=False, ex_after=False)
            return rec
        sandbox = SQLiteSandboxExecutor(db_path=db_path)
        feed = sandbox.execute(pred)
        rec["exec_ok_before"] = feed.is_success
        rec["ex_before"] = ex_match(db_path, pred, gold_rows)
        t_rep = time.time()
        for c in (llm, judge):
            if c is not None:
                c.begin_sample()
        prompt = PROMPT.format(schema=schema_ddl(db_path), question=s.question, evidence=s.evidence or "(none)",
                               sql=pred, exec_info=exec_info(feed) if args.mode != "rewrite" else "")
        text = llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}], max_tokens=2000)
        rec["llm_calls"] = 1
        new = extract_and_clean_sql(text)
        final = pred
        note = "kept original"
        if new and _norm(new) != _norm(pred):
            new_feed = sandbox.execute(new)
            if new_feed.is_success:
                final, note = new, "rewritten"
                if args.mode.startswith("execfb_verify"):
                    sem = SemanticRepairer(llm, judge)
                    fn = sem.verify_both_orders if args.mode == "execfb_verify2" else sem.verify
                    ok = fn(s.question, s.question, s.evidence, loader.get_schema(s.db_id),
                            pred, feed, new, new_feed)
                    rec["llm_calls"] += len(sem.log)
                    rec["intent_log"] = sem.log
                    if not ok:
                        final, note = pred, "judge rejected rewrite"
            else:
                note = "rewrite failed to execute"
        rec["status"] = "REPAIRED" if final != pred else "UNCHANGED"
        rec["final_sql"] = final
        rec["note"] = note
        usage = [c.end_sample() for c in (llm, judge) if c is not None]
        rec["repair_prompt_tokens"] = sum(u["prompt_tokens"] for u in usage)
        rec["repair_completion_tokens"] = sum(u["completion_tokens"] for u in usage)
        rec["repair_latency_ms"] = round((time.time() - t_rep) * 1000, 1)
        rec["ex_after"] = rec["ex_before"] if final == pred else ex_match(db_path, final, gold_rows)
        rec["exec_ok_after"] = feed.is_success if final == pred else True
        return rec

    with open(per_path, "a" if args.resume else "w", encoding="utf-8") as fout, \
            ThreadPoolExecutor(max_workers=args.workers) as pool:
        for rec in pool.map(one, todo):
            if rec is None:
                continue
            with lock:
                records.append(rec)
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fout.flush()
    if len(records) < len(outputs):
        print(f"CHƯA XONG: {len(records)}/{len(outputs)} — chạy lại với --resume")
        return
    args.gen = f"{args.gen} [đối chứng: {args.mode}]"
    summarize(records, out_dir, args, llm)


if __name__ == "__main__":
    main()
