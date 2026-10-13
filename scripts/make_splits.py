"""
Chia bộ BIRD dev thành 2 phần cố định:
  - tune   (~300 câu): dùng để thử, tìm lỗi, chỉnh code / prompt thoải mái.
  - report (phần còn lại): KHÔNG nhìn vào khi phát triển; chỉ chạy MỘT lần khi code đã chốt
    -> đây là số liệu đưa vào bài báo.

Chia theo tầng (stratified) theo cặp (database, độ khó) để hai phần có phân bố giống nhau.
Chạy lại với cùng seed luôn ra cùng một cách chia.

Cách chạy (từ thư mục gốc project):
    py scripts/make_splits.py                 # ghi configs/splits/bird_dev_split.json
"""

import argparse
import json
import os
import random
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vasp_sql.adapters.bird_loader import BirdDatasetLoader  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/bird_config.yaml")
    ap.add_argument("--tune-size", type=int, default=300)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out", default="configs/splits/bird_dev_split.json")
    args = ap.parse_args()

    samples = BirdDatasetLoader(args.config).samples
    rng = random.Random(args.seed)
    strata = defaultdict(list)
    for s in samples:
        strata[(s.db_id, s.difficulty)].append(s.question_id)

    # Số câu lấy từ mỗi tầng tỉ lệ với kích thước tầng (làm tròn theo phần dư lớn nhất)
    total = len(samples)
    quota = {k: args.tune_size * len(v) / total for k, v in strata.items()}
    take = {k: int(q) for k, q in quota.items()}
    rest = args.tune_size - sum(take.values())
    for k in sorted(quota, key=lambda k: quota[k] - take[k], reverse=True)[:rest]:
        take[k] += 1

    tune = []
    for k in sorted(strata):
        ids = sorted(strata[k])
        rng.shuffle(ids)
        tune.extend(ids[: take[k]])
    tune = sorted(tune)
    tune_set = set(tune)
    report = sorted(s.question_id for s in samples if s.question_id not in tune_set)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"seed": args.seed, "source": "BIRD dev (1534)", "tune": tune, "report": report}, f)

    by_id = {s.question_id: s for s in samples}
    for name, ids in (("tune", tune), ("report", report)):
        diff = Counter(by_id[i].difficulty for i in ids)
        dbs = Counter(by_id[i].db_id for i in ids)
        print(f"{name}: {len(ids)} câu | độ khó {dict(diff)} | {len(dbs)} database")


if __name__ == "__main__":
    main()
