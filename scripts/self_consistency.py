"""
Bỏ phiếu nhiều lần sinh (self-consistency) cho G1.

Ý tưởng: cho G1 sinh thêm vài câu SQL khác nhau (temperature > 0), chạy thử tất cả,
gom các câu cho CÙNG kết quả vào một nhóm, rồi chọn nhóm đông nhất.
Câu hiểu sai ý thường mỗi lần sai một kiểu, còn câu hiểu đúng thì hay ra cùng một kết quả.

Hai bước (chạy được từng đoạn ngắn, có --resume):
  1. --phase gen  : sinh thêm (k-1) câu cho mỗi câu hỏi -> results/generators/g1/<split>/sc_candidates.jsonl
                    (câu thứ 0 là câu gốc của G1 trong outputs.jsonl, temperature = 0)
  2. --phase vote : chạy các câu, bỏ phiếu, chấm EX -> results/generators/g1/<split>/self_consistency/
Quy tắc bỏ phiếu (cố định trước khi xem kết quả):
  - chỉ tính câu chạy được và ra ít nhất 1 dòng; hai câu cùng nhóm khi tập các dòng kết quả giống nhau
  - chọn nhóm đông nhất; hoà thì ưu tiên nhóm có câu gốc, sau đó nhóm có câu xuất hiện sớm nhất
  - không câu nào hợp lệ -> giữ câu gốc

Chạy:
  python scripts/self_consistency.py --phase gen  --split tune --config configs/bird_may_nay.yaml --k 5 --resume
  python scripts/self_consistency.py --phase vote --split tune --config configs/bird_may_nay.yaml --resume
"""

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_common import ex_match, run_sql_full  # noqa: E402

from vasp_sql.adapters.bird_loader import BirdDatasetLoader  # noqa: E402
from vasp_sql.generators.single_agent import SingleAgentGenerator  # noqa: E402
from vasp_sql.llm import LLMClient  # noqa: E402


class NoCacheLLM(LLMClient):
    """Không dùng bộ nhớ đệm: cùng một prompt phải cho ra các câu khác nhau (kết quả được lưu riêng)."""

    def chat_full(self, messages, max_tokens=400, use_cache=True, stop=None):
        return super().chat_full(messages, max_tokens=max_tokens, use_cache=False, stop=stop)


def load_jsonl(path):
    out = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    out[r["sample_id"]] = r
                except (json.JSONDecodeError, KeyError):
                    continue
    return out


def phase_gen(args, loader, base_dir, ids):
    out_path = os.path.join(base_dir, "sc_candidates.jsonl")
    done = load_jsonl(out_path) if args.resume else {}
    todo = [i for i in ids if i not in done]
    llm = NoCacheLLM(temperature=args.temperature)
    gen = SingleAgentGenerator(llm)
    by_id = {s.question_id: s for s in loader.samples}
    lock = threading.Lock()
    t0 = time.time()
    stop = threading.Event()

    def work(qid):
        if stop.is_set():
            return None
        s = by_id[qid]
        db = loader.get_db_path(s.db_id)
        # gọi song song (k-1) lần để mỗi câu hỏi xong nhanh
        with ThreadPoolExecutor(max_workers=args.k - 1) as inner:
            outs = list(inner.map(lambda _: gen.generate(s, db), range(args.k - 1)))
        sqls = [o.cleaned_sql if o.is_generation_successful else None for o in outs]
        if all(x is None for x in sqls):
            return None  # lỗi server: bỏ, lần sau chạy lại
        return {"sample_id": qid, "extra_sql": sqls}

    n = 0
    with open(out_path, "a" if args.resume else "w", encoding="utf-8") as f, \
            ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(work, q) for q in todo]
        for fut in as_completed(futs):
            r = fut.result()
            if r is not None:
                with lock:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                    f.flush()
                n += 1
            if args.max_seconds and time.time() - t0 > args.max_seconds:
                stop.set()
    total = len(done) + n
    print(("XONG" if total >= len(ids) else "CHƯA XONG") + f" gen: {total}/{len(ids)} (+{n}, {time.time() - t0:.0f}s)")


def vote(db, sqls, timeout_s):
    """sqls[0] = câu gốc. Trả về (chỉ số câu được chọn, số phiếu, số câu hợp lệ)."""
    groups = {}  # khoá = tập dòng kết quả -> danh sách chỉ số câu
    for i, sql in enumerate(sqls):
        if not sql:
            continue
        ok, rows, _ = run_sql_full(db, sql, timeout_s)
        if not ok or not rows:
            continue
        try:
            key = frozenset(rows)
        except TypeError:
            continue
        groups.setdefault(key, []).append(i)
    if not groups:
        return 0, 0, 0
    best = max(groups.values(), key=lambda g: (len(g), 0 in g, -g[0]))
    return best[0], len(best), sum(len(g) for g in groups.values())


def phase_vote(args, loader, base_dir, ids):
    outs = load_jsonl(os.path.join(base_dir, "outputs.jsonl"))
    cands = load_jsonl(os.path.join(base_dir, "sc_candidates.jsonl"))
    out_dir = os.path.join(base_dir, "self_consistency")
    os.makedirs(out_dir, exist_ok=True)
    per_path = os.path.join(out_dir, "per_sample.jsonl")
    done = load_jsonl(per_path) if args.resume else {}
    by_id = {s.question_id: s for s in loader.samples}
    todo = [i for i in ids if i not in done and i in cands and i in outs]
    t0 = time.time()
    stop = threading.Event()
    lock = threading.Lock()

    def work(qid):
        if stop.is_set():
            return None
        s = by_id[qid]
        db = loader.get_db_path(s.db_id)
        orig = outs[qid].get("cleaned_sql")
        sqls = [orig] + cands[qid]["extra_sql"]
        idx, votes, valid = vote(db, sqls, args.timeout)
        final = sqls[idx]
        ok, gold, _ = run_sql_full(db, s.golden_sql)
        gold = gold if ok else None
        ex_before = bool(orig) and ex_match(db, orig, gold)
        ex_after = ex_before if final == orig else (bool(final) and ex_match(db, final, gold))
        return {"sample_id": qid, "db_id": s.db_id, "ex_before": ex_before, "ex_after": ex_after,
                "chosen": idx, "votes": votes, "valid": valid, "changed": final != orig, "final_sql": final}

    n = 0
    with open(per_path, "a" if args.resume else "w", encoding="utf-8") as f, \
            ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(work, q) for q in todo]
        for fut in as_completed(futs):
            r = fut.result()
            if r is not None:
                with lock:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                    f.flush()
                n += 1
            if args.max_seconds and time.time() - t0 > args.max_seconds:
                stop.set()

    recs = list(load_jsonl(per_path).values())
    m = len(recs)
    if m >= len(ids):
        fixed = sum(r["ex_after"] and not r["ex_before"] for r in recs)
        broken = sum(r["ex_before"] and not r["ex_after"] for r in recs)
        summ = {"split": args.split, "n": m, "k": args.k,
                "EX_before_pct": round(100 * sum(r["ex_before"] for r in recs) / m, 2),
                "EX_after_pct": round(100 * sum(r["ex_after"] for r in recs) / m, 2),
                "fixed": fixed, "broken": broken, "changed": sum(r["changed"] for r in recs)}
        json.dump(summ, open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("XONG vote:", json.dumps(summ, ensure_ascii=False))
    else:
        print(f"CHƯA XONG vote: {m}/{len(ids)} (+{n}, {time.time() - t0:.0f}s)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True, choices=["gen", "vote"])
    ap.add_argument("--split", default="tune", choices=["tune", "report"])
    ap.add_argument("--config", default="configs/bird_config.yaml")
    ap.add_argument("--k", type=int, default=5, help="tổng số câu mỗi câu hỏi (kể cả câu gốc)")
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--max-seconds", type=float, default=None)
    args = ap.parse_args()

    loader = BirdDatasetLoader(args.config)
    ids = json.load(open("configs/splits/bird_dev_split.json", encoding="utf-8"))[args.split]
    base_dir = os.path.join("results", "generators", "g1", args.split)
    (phase_gen if args.phase == "gen" else phase_vote)(args, loader, base_dir, ids)


if __name__ == "__main__":
    main()
