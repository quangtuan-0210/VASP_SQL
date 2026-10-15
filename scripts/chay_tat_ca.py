"""
Chạy trọn một generator (G1/G2/G3) từ đầu đến cuối, KHÔNG giới hạn thời gian:
  1) sinh SQL cho tập tune rồi report (run_generator.py)
  2) chạy 7 cấu hình sửa lỗi cho từng tập (eval_generated.py / eval_baselines.py)

Phần nào đã xong thì tự bỏ qua, nên tắt giữa chừng rồi chạy lại vẫn tiếp tục được.

Dùng (mở terminal ở thư mục gốc project):
    python scripts/chay_tat_ca.py --gen g2
    python scripts/chay_tat_ca.py --gen g2 --data datasets/bird/dev_20240627

--data: thư mục BIRD dev (có dev.json, dev_tables.json, dev_databases/). Script tự tạo file cấu hình
        configs/bird_may_nay.yaml trỏ tới thư mục đó (dựa trên configs/bird_config.yaml).
"""

import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
N = {"tune": 300, "report": 1234}
CONFIGS = [  # (thư mục kết quả, lệnh, có gọi LLM?)
    ("eval", ["scripts/eval_generated.py"], False),
    ("eval_ast_only", ["scripts/eval_generated.py", "--ast-only"], False),
    ("eval_no_value", ["scripts/eval_generated.py", "--no-value-repair"], False),
    ("eval_no_graph", ["scripts/eval_generated.py", "--no-schema-graph"], False),
    ("eval_llm", ["scripts/eval_generated.py", "--llm"], True),
    ("baseline_rewrite", ["scripts/eval_baselines.py", "--mode", "rewrite"], True),
    ("baseline_execfb", ["scripts/eval_baselines.py", "--mode", "execfb"], True),
]


def dem(path):
    """Số câu (sample_id khác nhau) đã có trong một file .jsonl."""
    if not os.path.exists(path):
        return 0
    ids = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                ids.add(json.loads(line)["sample_id"])
            except Exception:
                pass
    return len(ids)


def tao_cau_hinh(data_dir):
    """Tạo configs/bird_may_nay.yaml: giống bird_config.yaml nhưng trỏ tới thư mục dữ liệu trên máy này."""
    import yaml
    data_dir = os.path.abspath(data_dir).replace("\\", "/")
    for f in ("dev.json", "dev_tables.json", "dev_databases"):
        if not os.path.exists(os.path.join(data_dir, f)):
            sys.exit(f"Không thấy {f} trong {data_dir} — kiểm tra lại --data")
    with open(os.path.join(ROOT, "configs", "bird_config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["benchmark"].update({
        "dataset_root": data_dir,
        "dev_json_path": data_dir + "/dev.json",
        "dev_tables_path": data_dir + "/dev_tables.json",
        "databases_dir": data_dir + "/dev_databases",
    })
    out = os.path.join(ROOT, "configs", "bird_may_nay.yaml")
    with open(out, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    return out


def chay(args):
    print("  $", " ".join(args[1:]), flush=True)
    t = time.time()
    r = subprocess.run(args, cwd=ROOT)
    print(f"  -> mã thoát {r.returncode}, {time.time() - t:.0f}s", flush=True)
    return r.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", required=True, choices=["g1", "g2", "g3"])
    ap.add_argument("--data", default=os.path.join(ROOT, "datasets", "bird", "dev_20240627"))
    ap.add_argument("--workers-gen", default="16", help="số câu sinh SQL song song")
    ap.add_argument("--workers-eval", default="4", help="số câu chấm song song (cấu hình không LLM)")
    ap.add_argument("--workers-llm", default="8", help="số câu chấm song song (cấu hình có LLM)")
    a = ap.parse_args()

    os.chdir(ROOT)
    cfg = tao_cau_hinh(a.data)
    py = sys.executable
    print(f"== {a.gen.upper()} | dữ liệu: {a.data} | cấu hình: {cfg}", flush=True)

    # 1) sinh SQL — chạy lại tối đa 3 lần (lần sau chỉ làm câu lỗi do mạng / server)
    for sp in ("tune", "report"):
        out = f"results/generators/{a.gen}/{sp}/outputs.jsonl"
        for _ in range(3):
            if dem(out) >= N[sp]:
                break
            print(f"\n[sinh SQL] {a.gen} {sp}: đã có {dem(out)}/{N[sp]}", flush=True)
            chay([py, "scripts/run_generator.py", "--gen", a.gen, "--split", sp, "--config", cfg,
                  "--workers", a.workers_gen, "--resume"])
        print(f"[sinh SQL] {a.gen} {sp}: {dem(out)}/{N[sp]}", flush=True)
        if dem(out) < N[sp]:
            sys.exit("Sinh SQL chưa đủ câu (server lỗi?) — chạy lại lệnh này sau.")

    # 2) 7 cấu hình sửa lỗi
    for sp in ("tune", "report"):
        n = dem(f"results/generators/{a.gen}/{sp}/outputs.jsonl")
        for d, cmd, llm in CONFIGS:
            o = f"results/generators/{a.gen}/{sp}/{d}"
            for _ in range(3):
                if os.path.exists(f"{o}/summary.json") and dem(f"{o}/per_sample.jsonl") >= n:
                    break
                print(f"\n[{sp} {d}] đã có {dem(o + '/per_sample.jsonl')}/{n}", flush=True)
                chay([py] + cmd + ["--gen", a.gen, "--split", sp, "--config", cfg,
                                   "--workers", a.workers_llm if llm else a.workers_eval, "--resume"])
            ok = os.path.exists(f"{o}/summary.json") and dem(f"{o}/per_sample.jsonl") >= n
            print(f"[{sp} {d}] {'XONG' if ok else 'CHƯA XONG'} ({dem(o + '/per_sample.jsonl')}/{n})", flush=True)

    print(f"\n=== {a.gen.upper()} XONG ===")


if __name__ == "__main__":
    main()
