"""
Kết hợp có chốt an toàn: VASP-SQL trước, rồi Execution-feedback CHỈ cho câu có dấu hiệu đáng ngờ.

Quy tắc (đóng băng ngày 2026-10-19, chọn trên tập tune; KHÔNG chỉnh theo tập report):
  1. VASP-SQL (cấu hình eval_llm_proj: Gate F bằng LLM + bỏ cột thừa) đã sửa câu  -> lấy bản VASP.
  2. Ngược lại, nếu câu gốc có ít nhất một dấu hiệu dưới đây -> lấy bản Execution-feedback:
       - loi_chay     : câu gốc không chạy được (lỗi / quá thời gian)
       - vasp_tu_choi : VASP-SQL từ chối sửa (ABSTAIN)
       - rong         : kết quả 0 dòng
       - co_null      : kết quả có ô NULL
       - trung_dong   : kết quả có dòng lặp lại
  3. Còn lại -> giữ câu gốc.
Các dấu hiệu đều tính được khi chạy thật (không cần biết đáp án).

Không gọi LLM: dùng kết quả sẵn có của
    results/generators/<gen>/<split>/eval_llm_proj/per_sample.jsonl   (VASP-SQL)
    results/generators/<gen>/<split>/baseline_execfb/per_sample.jsonl (Execution-feedback)
Ghi: results/generators/<gen>/<split>/gated_execfb/{per_sample.jsonl, summary.json}

Chạy:  python scripts/eval_gated.py --gen g1 --split report --config configs/bird_may_nay.yaml
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_common import run_sql_full  # noqa: E402

from vasp_sql.adapters.bird_loader import BirdDatasetLoader  # noqa: E402

SIGNALS = ("loi_chay", "vasp_tu_choi", "rong", "co_null", "trung_dong")


def load(path):
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            out[r["sample_id"]] = r
    return out


def signals_of(vasp_rec, db_path):
    """Các dấu hiệu đáng ngờ của câu gốc (chạy lại câu gốc trong sandbox chỉ-đọc)."""
    sig = set()
    if vasp_rec.get("status") == "ABSTAIN":
        sig.add("vasp_tu_choi")
    sql = vasp_rec.get("generated_sql")
    ok, rows, _ = run_sql_full(db_path, sql) if sql else (False, None, None)
    if not ok or rows is None:
        sig.add("loi_chay")
        return sig
    if len(rows) == 0:
        sig.add("rong")
    if any(v is None for row in rows for v in row):
        sig.add("co_null")
    if len(rows) != len(set(rows)):
        sig.add("trung_dong")
    return sig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", required=True)
    ap.add_argument("--split", default="report")
    ap.add_argument("--config", default="configs/bird_config.yaml")
    ap.add_argument("--execfb-dir", default="baseline_execfb", help="thư mục kết quả Execution-feedback dùng ở bước 2")
    ap.add_argument("--out-dir", default="gated_execfb")
    ap.add_argument("--dump-sent", default=None, help="ghi danh sách sample_id được gửi sang Execution-feedback ra file JSON")
    args = ap.parse_args()

    base = os.path.join("results", "generators", args.gen, args.split)
    vasp = load(os.path.join(base, "eval_llm_proj", "per_sample.jsonl"))
    efb_path = os.path.join(base, args.execfb_dir, "per_sample.jsonl")
    efb = load(efb_path) if os.path.exists(efb_path) else {}
    loader = BirdDatasetLoader(args.config)
    out_dir = os.path.join(base, args.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    recs, sent, missing = [], [], 0
    for sid, v in vasp.items():
        e = efb.get(sid)
        if v.get("status") == "REPAIRED":
            source, sig = "vasp", set()
        else:
            sig = signals_of(v, loader.get_db_path(v["db_id"]))
            source = "execfb" if sig else "goc"
        if source == "execfb":
            sent.append(sid)
            if e is None:  # chưa chạy Execution-feedback cho câu này
                missing += 1
                continue
        ex_after = v["ex_after"] if source == "vasp" else (e["ex_after"] if source == "execfb" else v["ex_before"])
        final_sql = v["final_sql"] if source == "vasp" else (e.get("final_sql") if source == "execfb" else v.get("generated_sql"))
        recs.append({"sample_id": sid, "db_id": v["db_id"], "difficulty": v.get("difficulty"),
                     "ex_before": v["ex_before"], "ex_after": ex_after, "source": source,
                     "signals": sorted(sig), "final_sql": final_sql,
                     "changed": (final_sql or "") != (v.get("generated_sql") or "")})

    if args.dump_sent:
        json.dump(sorted(sent), open(args.dump_sent, "w", encoding="utf-8"))
        print(f"đã ghi {len(sent)} câu được gửi -> {args.dump_sent}")
    if missing:
        print(f"CHƯA ĐỦ: {missing} câu được gửi chưa có kết quả trong {args.execfb_dir}")
        return
    n = len(recs)
    fixed = sum(1 for r in recs if r["ex_after"] and not r["ex_before"])
    broken = sum(1 for r in recs if r["ex_before"] and not r["ex_after"])
    summary = {"generator": args.gen, "split": args.split, "n": n, "signals": list(SIGNALS),
               "EX_before_pct": round(100 * sum(r["ex_before"] for r in recs) / n, 2),
               "EX_after_pct": round(100 * sum(r["ex_after"] for r in recs) / n, 2),
               "fixed": fixed, "broken": broken,
               "from_vasp": sum(r["source"] == "vasp" for r in recs),
               "sent_to_execfb": sum(r["source"] == "execfb" for r in recs)}
    with open(os.path.join(out_dir, "per_sample.jsonl"), "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
