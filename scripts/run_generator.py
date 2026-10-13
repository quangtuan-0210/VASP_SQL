"""
Chạy một AI sinh SQL (G1 / G2 / G3) trên một phần của BIRD dev và lưu đầu ra đã chuẩn hoá.

Cách chạy (từ thư mục gốc project):
    py scripts/run_generator.py --gen g1 --split tune
    py scripts/run_generator.py --gen g1 --split tune --workers 4 --resume
Đầu ra: results/generators/<gen>/<split>/outputs.jsonl  (mỗi dòng một GeneratorOutput)

- --split tune   : 300 câu để chỉnh sửa (xem configs/splits/bird_dev_split.json)
- --split report : 1.234 câu để báo cáo — chỉ chạy khi code đã chốt
- Câu trả lời LLM được lưu đệm (results/llm_cache.jsonl) nên chạy lại không tốn thêm lượt gọi.
"""

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vasp_sql.adapters.bird_loader import BirdDatasetLoader  # noqa: E402
from vasp_sql.llm import LLMClient  # noqa: E402


def make_generator(name, llm, args, loader=None):
    if name == "g1":
        from vasp_sql.generators.single_agent import SingleAgentGenerator
        return SingleAgentGenerator(llm, use_evidence=not args.no_evidence)
    if name == "g3":
        from vasp_sql.generators.langchain_agent import LangChainSQLGenerator
        return LangChainSQLGenerator(llm, use_evidence=not args.no_evidence)
    if name == "g2":
        from vasp_sql.generators.mac_sql import MacSqlGenerator
        return MacSqlGenerator(llm, databases_dir=loader.databases_dir, tables_json_path=loader.tables_json_path,
                               use_evidence=not args.no_evidence)
    raise SystemExit(f"Chưa có AI sinh SQL '{name}' (hiện có: g1, g2, g3)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", required=True, help="g1 | g2 | g3")
    ap.add_argument("--split", default="tune", choices=["tune", "report", "all"])
    ap.add_argument("--config", default="configs/bird_config.yaml")
    ap.add_argument("--split-file", default="configs/splits/bird_dev_split.json")
    ap.add_argument("--workers", type=int, default=4, help="số yêu cầu gửi song song tới server LLM")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-evidence", action="store_true", help="không đưa gợi ý (evidence) của BIRD vào prompt")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--max-seconds", type=float, default=None, help="dừng sau N giây (dùng với --resume)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    loader = BirdDatasetLoader(args.config)
    by_id = {s.question_id: s for s in loader.samples}
    if args.split == "all":
        ids = sorted(by_id)
    else:
        ids = json.load(open(args.split_file, encoding="utf-8"))[args.split]
    if args.limit:
        ids = ids[: args.limit]

    out_dir = args.out or os.path.join("results", "generators", args.gen, args.split)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "outputs.jsonl")

    done = set()
    if args.resume and os.path.exists(out_path):
        # Câu thất bại do MẤT KẾT NỐI / bị giới hạn tốc độ (HTTP 429) / hết lượt thử (không phải do AI) thì bỏ đi để chạy lại;
        # câu thất bại vì lý do khác là kết quả thật của AI nên giữ nguyên.
        keep = []
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    rec["sample_id"]
                except (json.JSONDecodeError, KeyError):
                    continue
                err = rec.get("generation_error") or ""
                if not rec.get("is_generation_successful") and ("LLMError" in err or "Connection" in err or "Gọi LLM thất bại" in err or "HTTP 429" in err):
                    continue
                keep.append(line if line.endswith("\n") else line + "\n")
                done.add(rec["sample_id"])
        with open(out_path, "w", encoding="utf-8") as f:
            f.writelines(keep)
    todo = [i for i in ids if i not in done]

    llm = LLMClient()
    gen = make_generator(args.gen, llm, args, loader)
    print(f"{gen.name}: {len(todo)} câu cần chạy ({len(done)} đã có) | model {llm.model} | {args.workers} luồng")

    lock = threading.Lock()
    t0 = time.time()
    stop = threading.Event()
    n_ok = n_fail = 0

    def work(qid):
        if stop.is_set():
            return None
        s = by_id[qid]
        return gen.generate(s, loader.get_db_path(s.db_id))

    with open(out_path, "a" if args.resume else "w", encoding="utf-8") as fout, \
            ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(work, q) for q in todo]
        for k, fut in enumerate(as_completed(futures), 1):
            out = fut.result()
            if out is None:
                continue
            with lock:
                fout.write(out.model_dump_json() + "\n")
                fout.flush()
            if out.is_generation_successful:
                n_ok += 1
            else:
                n_fail += 1
            if k % 25 == 0:
                print(f"  {k}/{len(todo)} ({time.time() - t0:.0f}s)", flush=True)
            if args.max_seconds and time.time() - t0 > args.max_seconds and not stop.is_set():
                stop.set()  # các việc chưa bắt đầu sẽ bỏ qua; chạy lại với --resume để làm tiếp

    total_done = len(done) + n_ok + n_fail
    status = "XONG" if total_done >= len(ids) else "CHƯA XONG — chạy lại với --resume"
    print(f"{status}: {total_done}/{len(ids)} | lần này sinh được {n_ok}, thất bại {n_fail} | "
          f"{time.time() - t0:.0f}s | LLM {llm.stats}")


if __name__ == "__main__":
    main()
