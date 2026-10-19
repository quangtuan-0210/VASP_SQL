"""
Cách 3 — đoán "hình dạng câu trả lời" từ câu hỏi (KHÔNG nhìn SQL), để phát hiện câu SQL hiểu sai ý.

Với mỗi câu hỏi, LLM đoán:
  n_columns     : câu hỏi yêu cầu trả về mấy cột (null nếu không chắc)
  rows          : "one" (đúng 1 dòng: bao nhiêu / ai nhất / cái nào nhất...) | "many" | "unknown"
  value_kind    : "number" | "text" | "date" | "mixed" | "unknown"   (khi chỉ có 1 cột)
  is_percentage : câu hỏi hỏi tỉ lệ phần trăm (giá trị phải trong khoảng 0–100)

Kết quả dùng chung cho mọi bộ sinh SQL (chỉ phụ thuộc câu hỏi).
Ghi: results/shape/<split>.jsonl

Chạy:  python scripts/answer_shape.py --split tune --config configs/bird_may_nay.yaml --workers 8 --resume
"""

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vasp_sql.adapters.bird_loader import BirdDatasetLoader  # noqa: E402
from vasp_sql.llm import LLMClient  # noqa: E402
from vasp_sql.verifiers.intent import _parse_json  # noqa: E402

SYSTEM = ("You analyse questions for a Text-to-SQL benchmark (SQLite). Before any SQL is written, you predict "
          "the shape of the correct answer table. Reply with JSON only.")

PROMPT = """Question: {q}
Hint: {e}

Predict the shape of the result table that correctly answers the question. The answer is graded by comparing rows
exactly, so it should contain only the information the question asks to return.
- n_columns: how many columns the answer should have (each piece of information asked for is one column; e.g. "full name"
  stored as first and last name counts as 2 if the hint/question implies separate fields). Use null if unsure.
- rows: "one" if the answer is a single row (a count, a sum, an average, a percentage, a yes/no, or "which/who ... the most/least"
  with one winner); "many" if it asks to list several items; "unknown" if it could be either.
- value_kind (only meaningful for a single column): "number", "text", "date", "mixed" or "unknown".
- is_percentage: true only if the question asks for a percentage / rate in percent.
Reply JSON: {{"n_columns": <int or null>, "rows": "one" | "many" | "unknown", "value_kind": "...", "is_percentage": true | false}}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="tune", choices=["tune", "report"])
    ap.add_argument("--config", default="configs/bird_config.yaml")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--max-seconds", type=float, default=None)
    args = ap.parse_args()

    loader = BirdDatasetLoader(args.config)
    ids = set(json.load(open("configs/splits/bird_dev_split.json", encoding="utf-8"))[args.split])
    samples = [s for s in loader.samples if s.question_id in ids]
    os.makedirs(os.path.join("results", "shape"), exist_ok=True)
    out_path = os.path.join("results", "shape", f"{args.split}.jsonl")
    done = set()
    if args.resume and os.path.exists(out_path):
        for line in open(out_path, encoding="utf-8"):
            try:
                done.add(json.loads(line)["sample_id"])
            except Exception:
                pass
    todo = [s for s in samples if s.question_id not in done]
    llm = LLMClient()
    lock = threading.Lock()
    t0 = time.time()
    stop = threading.Event()

    def one(s):
        if stop.is_set():
            return None
        if args.max_seconds and time.time() - t0 > args.max_seconds:
            stop.set()
            return None
        text = llm.chat([{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": PROMPT.format(q=s.question, e=s.evidence or "(none)")}],
                        max_tokens=300)
        d = _parse_json(text)
        rec = {"sample_id": s.question_id, "n_columns": d.get("n_columns"), "rows": d.get("rows", "unknown"),
               "value_kind": d.get("value_kind", "unknown"), "is_percentage": bool(d.get("is_percentage", False))}
        with lock, open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(one, todo))
    n = sum(1 for _ in open(out_path, encoding="utf-8")) if os.path.exists(out_path) else 0
    print(("XONG" if n >= len(samples) else "CHƯA XONG") + f": {n}/{len(samples)}")


if __name__ == "__main__":
    main()
