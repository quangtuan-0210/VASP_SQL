"""
Dò lỗi "quy ước ngầm" của BIRD trên SQL do bộ sinh tạo ra (chỉ dùng để quyết định có nên làm bản vá hay không).

Hai quy tắc, phát hiện KHÔNG cần đáp án:
  R1 chia_nguyen : câu SQL có phép chia mà cả hai vế đều không phải số thực (không CAST REAL, không hằng số
                   có dấu chấm) -> SQLite chia nguyên, ra số tròn/0. Vá: CAST(tử số AS REAL).
                   Biến thể R1k: chỉ áp khi câu hỏi/gợi ý có từ chỉ tỉ lệ (percent, ratio, rate, average, ...).
  R2 nhan_100    : câu hỏi hỏi phần trăm (percent/percentage) mà SQL không có hằng số 100 -> nhân biểu thức
                   có phép chia trong SELECT với 100.
Áp lên câu cuối của VASP-SQL + bỏ cột thừa (eval_llm_proj/final_sql), chấm EX với đáp án để đếm
sửa đúng / làm hỏng trên toàn bộ câu (cả câu đang đúng, để thấy rủi ro).

Chạy: python scripts/convention_probe.py --gens g1,g2,g3 --split tune --config configs/bird_may_nay.yaml
"""

import argparse
import json
import os
import re
import sys

import sqlglot
from sqlglot import exp

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_common import ex_match, run_sql_full  # noqa: E402
from eval_generated import load_jsonl  # noqa: E402

from vasp_sql.adapters.bird_loader import BirdDatasetLoader  # noqa: E402

REAL_TYPES = {"REAL", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC"}
RATIO_WORDS = re.compile(r"percent|ratio|rate|average|avg|proportion|how many times|divid|per ", re.I)
PERCENT_WORDS = re.compile(r"percent", re.I)


def is_real(e: exp.Expression) -> bool:
    """Vế này chắc chắn là số thực (đã CAST REAL, hoặc có hằng số thập phân, hoặc hàm AVG)."""
    for n in e.walk():
        if isinstance(n, exp.Cast) and n.to.this.name in REAL_TYPES:
            return True
        if isinstance(n, exp.Literal) and n.is_number and "." in n.this:
            return True
        if isinstance(n, exp.Avg):
            return True
    return False


def cast_div(sql: str):
    try:
        t = sqlglot.parse_one(sql, read="sqlite")
    except Exception:  # noqa: BLE001
        return None
    changed = False
    for d in list(t.find_all(exp.Div)):
        if is_real(d.this) or is_real(d.expression):
            continue
        d.set("this", exp.Cast(this=d.this.copy(), to=exp.DataType.build("REAL")))
        changed = True
    return t.sql(dialect="sqlite") if changed else None


def times_100(sql: str):
    try:
        t = sqlglot.parse_one(sql, read="sqlite")
    except Exception:  # noqa: BLE001
        return None
    if any(isinstance(n, exp.Literal) and n.is_number and float(n.this) == 100 for n in t.walk()):
        return None
    sel = t if isinstance(t, exp.Select) else t.find(exp.Select)
    if sel is None:
        return None
    for i, p in enumerate(sel.expressions):
        inner = p.this if isinstance(p, exp.Alias) else p
        if inner.find(exp.Div) is None:
            continue
        new = exp.Mul(this=exp.Paren(this=inner.copy()), expression=exp.Literal.number(100))
        if isinstance(p, exp.Alias):
            p.set("this", new)
        else:
            sel.expressions[i] = new
            new.parent = sel
        return t.sql(dialect="sqlite")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gens", default="g1,g2,g3")
    ap.add_argument("--split", default="tune")
    ap.add_argument("--config", default="configs/bird_config.yaml")
    args = ap.parse_args()
    loader = BirdDatasetLoader(args.config)
    by_id = {s.question_id: s for s in loader.samples}
    out = {}
    for g in args.gens.split(","):
        recs = load_jsonl(os.path.join("results", "generators", g, args.split, "eval_llm_proj", "per_sample.jsonl"))
        stat = {k: {"ap": 0, "sua": 0, "hong": 0, "ids_sua": [], "ids_hong": []} for k in ("R1", "R1k", "R2", "R1+R2")}
        wrong = sum(1 for r in recs if not r["ex_after"])
        for r in recs:
            sql = r.get("final_sql") or r.get("generated_sql")
            if not sql:
                continue
            s = by_id[r["sample_id"]]
            db = loader.get_db_path(s.db_id)
            text = f"{s.question} {s.evidence or ''}"
            c1 = cast_div(sql)
            c2 = times_100(sql) if PERCENT_WORDS.search(s.question) else None
            c12 = times_100(c1 or sql) if PERCENT_WORDS.search(s.question) else None
            c12 = c12 or c1
            cand = {"R1": c1, "R1k": c1 if RATIO_WORDS.search(text) else None, "R2": c2, "R1+R2": c12}
            gold = None
            for k, new in cand.items():
                if not new:
                    continue
                if gold is None:
                    _, gold, _ = run_sql_full(db, s.golden_sql)
                ok = ex_match(db, new, gold)
                st = stat[k]
                st["ap"] += 1
                if ok and not r["ex_after"]:
                    st["sua"] += 1
                    st["ids_sua"].append(r["sample_id"])
                if r["ex_after"] and not ok:
                    st["hong"] += 1
                    st["ids_hong"].append(r["sample_id"])
        out[g] = {"n": len(recs), "sai": wrong, **stat}
        print(g, f"n={len(recs)} sai={wrong}",
              " | ".join(f"{k}: áp {v['ap']}, sửa {v['sua']}, hỏng {v['hong']}" for k, v in stat.items()), flush=True)
    os.makedirs(os.path.join("results", args.split), exist_ok=True)
    json.dump(out, open(os.path.join("results", args.split, "convention_probe.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
