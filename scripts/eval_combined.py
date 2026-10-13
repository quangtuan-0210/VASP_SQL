"""
Ghép 2 lớp sửa lỗi (không gọi LLM lại — chỉ ghép kết quả đã chạy theo từng câu).

Thứ tự:
  1. VASP-SQL (không LLM) xem trước. Câu nào nó sửa (REPAIRED) -> giữ bản sửa của VASP-SQL.
  2. Câu còn lại -> lấy kết quả của "viết lại + kết quả chạy + giám khảo" (execfb_verify).

Hai biến thể:
  A: câu VASP để nguyên (UNCHANGED) HOẶC từ chối (ABSTAIN) đều chuyển cho execfb_verify.
  B: chỉ câu UNCHANGED mới chuyển; câu ABSTAIN giữ nguyên bản gốc (thận trọng hơn).

Ghép offline là hợp lệ vì execfb_verify đã chạy trên đúng câu SQL gốc — cũng chính là
đầu vào nó sẽ nhận nếu chạy sau VASP-SQL với câu VASP không sửa.

Dùng:  python scripts/eval_combined.py --gen g1 --split tune --variant A [--fallback execfb_verify2]
Kết quả: results/generators/<gen>/<split>/combined_<variant>[_judge2]/
"""

import argparse
import os
from types import SimpleNamespace

from eval_generated import load_jsonl, summarize


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", required=True)
    ap.add_argument("--split", default="tune")
    ap.add_argument("--variant", choices=["A", "B"], default="A")
    ap.add_argument("--fallback", choices=["execfb_verify", "execfb_verify2"], default="execfb_verify",
                    help="phương pháp xử lý các câu VASP-SQL không sửa (execfb_verify2 = giám khảo chấm 2 lần)")
    args = ap.parse_args()

    base = os.path.join("results", "generators", args.gen, args.split)
    vasp = {r["sample_id"]: r for r in load_jsonl(os.path.join(base, "eval", "per_sample.jsonl"))}
    efv = {r["sample_id"]: r for r in load_jsonl(os.path.join(base, f"baseline_{args.fallback}", "per_sample.jsonl"))}
    assert set(vasp) == set(efv), "hai file kết quả không cùng tập câu"

    recs = []
    for sid, v in vasp.items():
        if v.get("status") == "REPAIRED":
            r = dict(v, source="vasp")
        elif v.get("status") == "ABSTAIN" and args.variant == "B":
            r = dict(v, source="giu_nguyen")  # VASP từ chối -> giữ câu gốc
        else:
            r = dict(efv[sid], source=args.fallback)
        recs.append(r)

    suffix = "" if args.fallback == "execfb_verify" else "_judge2"
    out = os.path.join(base, f"combined_{args.variant}{suffix}")
    os.makedirs(out, exist_ok=True)
    fake_args = SimpleNamespace(gen=f"{args.gen} [ghép {args.variant}: VASP-SQL -> {args.fallback}]",
                                split=args.split, llm=True, semantic=False)
    summarize(recs, out, fake_args, None)
    with open(os.path.join(out, "per_sample.jsonl"), "w", encoding="utf-8") as f:
        import json
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
