"""
Thống kê cuối (mục 5.7–5.8 của kế hoạch).

Với mỗi AI sinh SQL (G1/G2/G3) và mỗi cấu hình (mục 5.6 + ablation):
  1. EX + khoảng tin cậy 95% (bootstrap theo mẫu, lấy mẫu lại câu hỏi 10.000 lần),
     mức tăng EX so với "không sửa" + CI 95% (bootstrap theo cặp), McNemar chính xác,
     số câu sửa đúng / làm hỏng, RSR, RR, tỉ lệ sửa sai (false repair), tỉ lệ từ chối (abstain)
  2. Theo độ khó và theo database
  3. Theo loại lỗi: câu sai ban đầu bị chặn ở cổng nào; mỗi loại bản vá (đổi cột / bảng / thêm JOIN /
     sửa giá trị) sửa bao nhiêu câu, đúng bao nhiêu, làm hỏng bao nhiêu, số nút AST thay đổi trung bình
  4. Chi phí & độ trễ: token và thời gian của bước sinh SQL và bước sửa (tính cả lượt lấy từ bộ nhớ đệm,
     tức chi phí khi chạy lần đầu); ước tính USD nếu cho giá (--price-in/--price-out, USD / 1 triệu token)

Phần dữ liệu: --split report (mặc định). Bảng chính gồm các cấu hình trình bày trong báo cáo
(Execution-feedback, VASP-SQL + bỏ cột thừa, kết hợp có chốt an toàn); các cấu hình khác in gọn ở phần phụ lục.
Mốc "không sửa" lấy từ results/generators/<g>/<split>/eval/ cho mọi cấu hình.

Dùng:  python scripts/stats_report.py --split report [--price-in 0.09 --price-out 0.55]
Ghi:   results/KET_QUA_REPORT.md và results/ket_qua_report.json
"""

import argparse
import json
import os
import random
from collections import Counter, defaultdict
from math import comb

from eval_generated import load_jsonl

GENS = [("g1", "G1 — một lần gọi LLM"), ("g2", "G2 — MAC-SQL"), ("g3", "G3 — LangChain agent")]
METHODS = [  # (thư mục, tên hiển thị) — các cấu hình trình bày trong báo cáo
    ("baseline_execfb", "Execution-feedback repair"),
    ("eval_llm_proj", "VASP-SQL + bỏ cột thừa"),
    ("gated_execfb", "Kết hợp có chốt an toàn (đề xuất)"),
]
EXTRA_METHODS = [  # phụ lục: đã chạy nhưng không đưa vào bảng chính của báo cáo
    ("baseline_rewrite", "LLM rewrite"),
    ("eval_ast_only", "AST-only"),
    ("eval", "VASP-SQL không LLM (A–E)"),
    ("eval_llm", "VASP-SQL bản cũ (A–F, chưa có bỏ cột thừa)"),
    ("eval_no_value", "Ablation: tắt sửa giá trị"),
    ("eval_no_graph", "Ablation: tắt đồ thị schema"),
]
PATCH_NAMES = {"ReplaceColumn": "Đổi tên cột", "ReplaceTable": "Đổi tên bảng", "AddJoin": "Thêm điều kiện JOIN",
               "ReplacePredicateValue": "Sửa giá trị lọc", "SemanticEdit": "Sửa ý nghĩa (LLM)",
               "DropSelectColumn": "Bỏ cột thừa"}
B = 10000


def mcnemar_exact(b, c):
    """p hai phía của McNemar chính xác (nhị thức, p=0.5); b, c = số câu sai->đúng và đúng->sai."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)


def ci(vals):
    vals = sorted(vals)
    return round(vals[int(0.025 * len(vals))], 2), round(vals[int(0.975 * len(vals)) - 1], 2)


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def load_split(g, splits, methods):
    """{method_dir: {sample_id: rec}} gộp các phần (split) được yêu cầu; chỉ giữ cấu hình có đủ mọi phần.
    Luôn nạp thêm "eval" làm mốc (EX trước khi sửa, độ khó, lý do sai)."""
    out = {}
    for d in dict.fromkeys(["eval"] + [m for m, _ in methods]):
        merged, ok = {}, True
        for sp in splits:
            p = os.path.join("results", "generators", g, sp, d, "per_sample.jsonl")
            if not os.path.exists(p):
                ok = False
                break
            merged.update({r["sample_id"]: r for r in load_jsonl(p)})
        if ok and merged:
            out[d] = merged
    return out


def analyse(g, gname, splits, rng, price_in, price_out, methods=METHODS, detail=True):
    recs = load_split(g, splits, methods)
    if not recs or "eval" not in recs:
        return None, []
    ref = recs["eval"]
    ids = sorted(ref)
    n = len(ids)
    before = [bool(ref[i]["ex_before"]) for i in ids]
    boots = [[rng.randrange(n) for _ in range(n)] for _ in range(B)]
    db_of = {i: ref[i]["db_id"] for i in ids}
    diff_of = {i: ref[i]["difficulty"] for i in ids}
    rows, per_db, per_diff, costs, patch_tables = [], defaultdict(dict), defaultdict(dict), [], {}

    # chi phí bước sinh SQL (giống nhau cho mọi cấu hình)
    gm = [ref[i].get("gen_meta") or {} for i in ids]
    gen_cost = {"calls": mean([m.get("llm_calls") for m in gm]), "prompt": mean([m.get("prompt_tokens") for m in gm]),
                "completion": mean([m.get("completion_tokens") for m in gm]),
                "latency_s": mean([(m.get("latency_ms") or 0) / 1000 for m in gm])}

    def add_row(key, name, after, r=None):
        ex = 100.0 * sum(after) / n
        fixed = sum(1 for a, b in zip(after, before) if a and not b)
        broken = sum(1 for a, b in zip(after, before) if b and not a)
        row = {"key": key, "name": name, "n": n, "EX": round(ex, 2),
               "EX_CI95": ci([100.0 * sum(after[j] for j in s) / n for s in boots]),
               "gain": round(ex - 100.0 * sum(before) / n, 2),
               "gain_CI95": ci([100.0 * sum(after[j] - before[j] for j in s) / n for s in boots]),
               "fixed": fixed, "broken": broken,
               "RSR_pct": round(100.0 * fixed / max(1, n - sum(before)), 2),
               "RR_pct": round(100.0 * broken / max(1, sum(before)), 2),
               "p_mcnemar": round(mcnemar_exact(fixed, broken), 4)}
        if r is not None:
            st = Counter(r[i].get("status") for i in ids)
            changed = [i for i in ids if r[i].get("status") == "REPAIRED" or r[i].get("changed")]
            row.update(repaired=len(changed),
                       false_repair_pct=round(100.0 * sum(1 for i in changed if not r[i]["ex_after"]) / max(1, len(changed)), 2),
                       abstain_pct=round(100.0 * st.get("ABSTAIN", 0) / n, 2),
                       ast_nodes_changed=mean([r[i].get("ast_nodes_changed") for i in changed]))
            c = {"calls": mean([r[i].get("llm_calls", 0) for i in ids]),
                 "prompt": mean([r[i].get("repair_prompt_tokens") for i in ids]),
                 "completion": mean([r[i].get("repair_completion_tokens") for i in ids]),
                 "latency_ms": mean([r[i].get("repair_latency_ms") for i in ids])}
            if key != "gated_execfb":  # chi phí của bản kết hợp = VASP-SQL + Execution-feedback cho câu được gửi
                costs.append((name, c))
        rows.append(row)
        dsum, fsum = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
        for i, a in zip(ids, after):
            dsum[db_of[i]][0] += 1; dsum[db_of[i]][1] += a
            fsum[diff_of[i]][0] += 1; fsum[diff_of[i]][1] += a
        for k_, (k, c_) in dsum.items():
            per_db[k_][key] = (k, round(100.0 * c_ / k, 2))
        for k_, (k, c_) in fsum.items():
            per_diff[k_][key] = (k, round(100.0 * c_ / k, 2))

    add_row("none", "Không sửa (đầu ra gốc)", before)
    for d, name in methods:
        if d not in recs:
            continue
        r = recs[d]
        assert set(r) == set(ids), f"{g}/{d}: tập câu không khớp"
        # Mốc "trước khi sửa" lấy chung từ "eval" cho mọi cấu hình. Vài câu chạy sát giới hạn thời gian có thể
        # cho EX trước khác nhau giữa các lần chạy (nhiễu thời gian); khi đó vẫn so với mốc chung.
        lech = [i for i in ids if bool(r[i]["ex_before"]) != before[ids.index(i)]]
        if lech:
            print(f"  lưu ý {g}/{d}: {len(lech)} câu có EX trước lệch mốc (nhiễu thời gian): {lech}")
        add_row(d, name, [bool(r[i]["ex_after"]) for i in ids], r)
        if d.startswith("eval"):
            t = defaultdict(lambda: [0, 0, 0])  # loại bản vá -> [số câu sửa, sai->đúng, đúng->sai]
            for i in ids:
                if r[i].get("status") == "REPAIRED":
                    for pt in sorted(set(r[i].get("patches") or [])):
                        t[pt][0] += 1
                        t[pt][1] += (not r[i]["ex_before"]) and r[i]["ex_after"]
                        t[pt][2] += r[i]["ex_before"] and not r[i]["ex_after"]
            patch_tables[name] = dict(t)
    why = Counter(ref[i].get("first_failing_gate") or "chạy được nhưng sai kết quả" for i in ids
                  if not ref[i]["ex_before"] and ref[i].get("status") != "NO_SQL")
    no_sql = sum(1 for i in ids if ref[i].get("status") == "NO_SQL")

    # ---------- Markdown
    L = [f"### {gname} (n = {n})", "",
         "| Cấu hình | EX % [CI 95%] | Tăng EX [CI 95%] | Sửa đúng | Làm hỏng | RSR % | RR % | Sửa sai % | Từ chối % | p (McNemar) |",
         "|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        gain = "—" if r["key"] == "none" else f"{r['gain']:+.2f} [{r['gain_CI95'][0]:+.2f}, {r['gain_CI95'][1]:+.2f}]"
        p = "—" if r["key"] == "none" else f"{r['p_mcnemar']:.4f}"
        L.append(f"| {r['name']} | {r['EX']:.2f} [{r['EX_CI95'][0]:.2f}, {r['EX_CI95'][1]:.2f}] | {gain} | {r['fixed']} | "
                 f"{r['broken']} | {r['RSR_pct']} | {r['RR_pct']} | {r.get('false_repair_pct', '—')} | {r.get('abstain_pct', '—')} | {p} |")
    if not detail:
        L.append("")
        return {"rows": rows}, L
    keys = [r["key"] for r in rows]
    names = {r["key"]: r["name"] for r in rows}
    for title, table in (("Theo độ khó", per_diff), ("Theo database", per_db)):
        L += ["", f"**{title}** (EX %)", "", "| | n | " + " | ".join(names[k] for k in keys) + " |",
              "|---|---:|" + "---:|" * len(keys)]
        for k_, v in sorted(table.items()):
            L.append(f"| {k_} | {v[keys[0]][0]} | " + " | ".join(f"{v[k][1]:.2f}" for k in keys) + " |")
    L += ["", f"**Câu sai ban đầu — bị chặn ở đâu** (tổng {n - sum(before)} câu sai; {no_sql} câu không sinh được SQL)", "",
          "| Nguyên nhân | Số câu |", "|---|---:|"]
    L += [f"| {'Gate ' + k if len(k) == 1 else k} | {v} |" for k, v in why.most_common()]
    for name, t in patch_tables.items():
        if not t:
            continue
        L += ["", f"**Theo loại bản vá — {name}**", "", "| Loại bản vá | Số câu sửa | Sai → đúng | Đúng → sai |", "|---|---:|---:|---:|"]
        L += [f"| {PATCH_NAMES.get(k, k)} | {v[0]} | {v[1]} | {v[2]} |" for k, v in sorted(t.items())]
    asts = [(r["name"], r.get("ast_nodes_changed")) for r in rows if r.get("ast_nodes_changed") is not None]
    if asts:
        L += ["", "**Tính cục bộ** — số nút AST thay đổi trung bình (câu được sửa): " +
              "; ".join(f"{a}: {b}" for a, b in asts)]
    L += ["", "**Chi phí & độ trễ trung bình mỗi câu** (tính cả lượt lấy từ bộ nhớ đệm)", "",
          "| Bước | Lượt gọi LLM | Token vào | Token ra | Thời gian | Ước tính USD / 1000 câu |", "|---|---:|---:|---:|---:|---:|"]

    def usd(c):
        if price_in is None or c.get("prompt") is None:
            return "—"
        return f"{1000 * ((c['prompt'] or 0) * price_in + (c['completion'] or 0) * (price_out or 0)) / 1e6:.2f}"
    L.append(f"| Sinh SQL | {gen_cost['calls']} | {gen_cost['prompt']} | {gen_cost['completion']} | {gen_cost['latency_s']} s | {usd(gen_cost)} |")
    for name, c in costs:
        L.append(f"| Sửa: {name} | {c['calls']} | {c['prompt'] if c['prompt'] is not None else '—'} | "
                 f"{c['completion'] if c['completion'] is not None else '—'} | {c['latency_ms']} ms | {usd(c)} |")
    L.append("")
    return {"rows": rows, "per_db": per_db, "per_difficulty": per_diff, "why_wrong": dict(why),
            "patch_tables": patch_tables, "gen_cost": gen_cost, "repair_costs": dict(costs)}, L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="report")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--price-in", type=float, default=None, help="USD / 1 triệu token vào")
    ap.add_argument("--price-out", type=float, default=None, help="USD / 1 triệu token ra")
    args = ap.parse_args()

    out = {}
    L = ["# Kết quả cuối", "",
         f"Khoảng tin cậy 95%: bootstrap theo mẫu ({B} lần, seed {args.seed}). p: McNemar chính xác so với "
         "'không sửa' (ý nghĩa ở mức 0,05). RSR = câu sai được sửa đúng / câu sai; RR = câu đúng bị làm hỏng / câu đúng; "
         "Sửa sai = bản sửa không ra kết quả đúng / số câu bị sửa.", ""]
    parts = [(f"Tập `{args.split}`", [args.split], METHODS, True, ""),
             (f"Phụ lục — các cấu hình khác đã chạy trên tập `{args.split}` (không đưa vào bảng chính)", [args.split],
              EXTRA_METHODS, False, "_phu_luc")]
    for title, splits, methods, detail, suffix in parts:
        sec, any_ = [f"## {title}", ""], False
        for g, gname in GENS:
            res, lines = analyse(g, gname, splits, random.Random(args.seed), args.price_in, args.price_out,
                                 methods, detail)
            if res:
                any_ = True
                out.setdefault("+".join(splits) + suffix, {})[g] = res
                sec += lines
        if any_:
            L += sec

    with open(os.path.join("results", "ket_qua_report.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    with open(os.path.join("results", "KET_QUA_REPORT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
