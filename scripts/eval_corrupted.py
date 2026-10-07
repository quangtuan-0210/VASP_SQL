"""
Step 2 — Controlled-corruption evaluation on BIRD.

Takes gold SQL that the layer already leaves UNCHANGED (clean base, see eval_gold.py),
injects one labelled fault, runs repair() and checks the result against gold with
BIRD's EX criterion.

Fault types (one fault per variant, location recorded):
  column_typo : one column identifier misspelled (drop / swap / duplicate a character)
  table_typo  : one table identifier misspelled
  join_drop   : ON predicate removed from one JOIN (-> Cartesian product)

Outcome per variant:
  FIXED      REPAIRED and result == gold           (repair success)
  WRONG_FIX  REPAIRED but result != gold           (false repair)
  ABSTAIN    layer refused to change the query
  MISSED     layer returned UNCHANGED for a broken query

Variants whose corrupted query still returns the gold result are discarded ("ineffective").

Usage (from project root):
    py scripts/eval_corrupted.py                                 # uses results/gold_eval/per_sample.jsonl
    py scripts/eval_corrupted.py --per-type 200 --seed 42
Outputs: results/corrupted_eval/{variants.jsonl, summary.json, summary.md}
"""

import argparse
import json
import os
import random
import time
from collections import Counter, defaultdict

from sqlglot import exp

from eval_common import ex_match, run_sql_full

from vasp_sql.adapters.bird_loader import BirdDatasetLoader
from vasp_sql.ast.parser import SQLParser
from vasp_sql.pipeline import repair

PARSER = SQLParser(default_dialect="sqlite")
FAULT_TYPES = ("column_typo", "table_typo", "join_drop")


# ----------------------------------------------------------------------------- mutations
def misspell(name: str, rng: random.Random) -> str:
    if len(name) < 3:
        return name + name[-1]
    i = rng.randrange(1, len(name) - 1)
    op = rng.choice(("drop", "swap", "dup"))
    if op == "drop":
        return name[:i] + name[i + 1:]
    if op == "swap":
        return name[:i] + name[i + 1] + name[i] + name[i + 2:]
    return name[:i] + name[i] + name[i:]


def corrupt_column(ast, schema, rng):
    all_cols = {c.lower() for t in schema.tables.values() for c in t.columns}
    cands = [c for c in ast.find_all(exp.Column) if c.name and c.name.lower() in all_cols]
    if not cands:
        return None
    node = rng.choice(cands)
    for _ in range(5):
        new = misspell(node.name, rng)
        if new.lower() not in all_cols and new != node.name:
            break
    else:
        return None
    old = node.name
    node.set("this", exp.to_identifier(new, quoted=node.this.args.get("quoted", False)))
    return {"original": old, "corrupted": new}


def corrupt_table(ast, schema, rng):
    tables = {t.lower() for t in schema.tables}
    cands = [t for t in ast.find_all(exp.Table) if t.name and t.name.lower() in tables]
    if not cands:
        return None
    node = rng.choice(cands)
    for _ in range(5):
        new = misspell(node.name, rng)
        if new.lower() not in tables and new != node.name:
            break
    else:
        return None
    old = node.name
    node.set("this", exp.to_identifier(new, quoted=node.this.args.get("quoted", False)))
    return {"original": old, "corrupted": new}


def corrupt_join(ast, schema, rng):
    cands = [j for j in ast.find_all(exp.Join) if isinstance(j.args.get("on"), exp.EQ)]
    if not cands:
        return None
    j = rng.choice(cands)
    old = j.args["on"].sql(dialect="sqlite")
    j.set("on", None)
    j.set("side", None)
    j.set("kind", "CROSS")
    return {"original": old, "corrupted": f"CROSS JOIN {j.this.sql(dialect='sqlite')}"}


MUTATORS = {"column_typo": corrupt_column, "table_typo": corrupt_table, "join_drop": corrupt_join}


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/bird_config.yaml")
    ap.add_argument("--gold-results", default="results/gold_eval/per_sample.jsonl",
                    help="output of eval_gold.py; only UNCHANGED gold queries are used as clean bases")
    ap.add_argument("--per-type", type=int, default=300, help="max effective variants per fault type")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="results/corrupted_eval")
    ap.add_argument("--llm", action="store_true",
                    help="bật Gate F bằng LLM (đọc cấu hình server từ .env); câu trả lời được lưu đệm ở results/llm_cache.jsonl")
    ap.add_argument("--resume", action="store_true", help="continue an interrupted run")
    ap.add_argument("--max-seconds", type=float, default=None, help="stop after this many seconds (use with --resume)")
    args = ap.parse_args()

    loader = BirdDatasetLoader(args.config)
    llm = None
    if args.llm:
        from vasp_sql.llm import LLMClient  # chỉ cần khi bật --llm
        llm = LLMClient()
        print(f"Gate F dùng LLM: {llm.model} @ {llm.base_url}")
    by_id = {s.question_id: s for s in loader.samples}
    clean_ids = [r["question_id"] for r in load_jsonl(args.gold_results) if r.get("status") == "UNCHANGED"]
    os.makedirs(args.out, exist_ok=True)

    out_path = os.path.join(args.out, "variants.jsonl")
    previous = load_jsonl(out_path) if (args.resume and os.path.exists(out_path)) else []
    done = {(r["fault_type"], r["question_id"]): r for r in previous}
    records = [r for r in previous if "_skip" not in r]
    t0 = time.time()
    fout = open(out_path, "w", encoding="utf-8")
    for r in previous:  # rewrite cleanly
        fout.write(json.dumps(r, ensure_ascii=False) + "\n")
    finished = True
    for ftype in FAULT_TYPES:
        if not finished:
            break
        rng = random.Random(f"{args.seed}-{ftype}")
        order = clean_ids[:]
        rng.shuffle(order)
        made, skipped = 0, Counter()
        for qid in order:
            if made >= args.per_type:
                break
            s = by_id[qid]
            db_path = loader.get_db_path(s.db_id)
            schema = loader.get_schema(s.db_id)
            ast = PARSER.parse(s.golden_sql).copy()
            info = MUTATORS[ftype](ast, schema, rng)  # always called: keeps the random sequence identical on resume
            if info is None:
                skipped["not_applicable"] += 1
                continue
            prev = done.get((ftype, qid))
            if prev is not None:  # already handled in an earlier (interrupted) run
                if "_skip" in prev:
                    skipped[prev["_skip"]] += 1
                else:
                    made += 1
                continue
            if args.max_seconds and time.time() - t0 > args.max_seconds:
                finished = False
                break
            bad_sql = PARSER.to_sql(ast)

            def skip(reason):
                skipped[reason] += 1
                fout.write(json.dumps({"_skip": reason, "fault_type": ftype, "question_id": qid}) + "\n")
                fout.flush()

            ok, gold_rows, _ = run_sql_full(db_path, s.golden_sql)
            if not ok:
                skip("gold_exec_error")
                continue
            # discard variants that are still "correct"
            if ex_match(db_path, bad_sql, gold_rows, timeout_s=10.0):
                skip("ineffective")
                continue

            rec = {"fault_type": ftype, "question_id": qid, "db_id": s.db_id,
                   "difficulty": s.difficulty, "gold_sql": s.golden_sql,
                   "corrupted_sql": bad_sql, **{f"fault_{k}": v for k, v in info.items()}}
            try:
                res = repair(question=s.question, sql=bad_sql, schema=schema,
                             db_path=db_path, evidence=s.evidence, llm=llm)
                if res.status.value == "REPAIRED":
                    outcome = "FIXED" if ex_match(db_path, res.final_sql, gold_rows) else "WRONG_FIX"
                elif res.status.value == "ABSTAIN":
                    outcome = "ABSTAIN"
                else:
                    outcome = "MISSED"
                rec.update(outcome=outcome, status=res.status.value,
                           final_sql=res.final_sql if res.status.value == "REPAIRED" else None,
                           patches=[p.patch_type for p in res.patches_applied],
                           replacements=[p.replacement_fragment for p in res.patches_applied],
                           ast_edit_size=res.metrics.get("ast_edit_size"),
                           abstain_reason=res.abstain_reason,
                           latency_ms=res.metrics.get("latency_ms"),
                           llm_calls=res.metrics.get("llm_calls", 0),
                           intent_log=res.metrics.get("intent_log", []))
            except Exception as e:  # noqa: BLE001
                rec.update(outcome="CRASH", error=f"{type(e).__name__}: {e}")
            records.append(rec)
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fout.flush()
            made += 1
        print(f"  {ftype}: {made} variants, skipped={dict(skipped)} ({time.time() - t0:.0f}s)", flush=True)
        records.append({"_meta": True, "fault_type": ftype, "skipped": dict(skipped)})
    fout.close()
    if not finished:
        print(f"PARTIAL: {len(records)} variants so far — run again with --resume to continue")
        return
    summarize(records, args, time.time() - t0)
    if llm is not None:
        print("Thống kê LLM:", llm.stats)


def load_jsonl(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def summarize(records, args, elapsed):
    meta = {r["fault_type"]: r["skipped"] for r in records if r.get("_meta")}
    recs = [r for r in records if not r.get("_meta")]
    outcomes = ("FIXED", "WRONG_FIX", "ABSTAIN", "MISSED", "CRASH")
    table = defaultdict(Counter)
    for r in recs:
        table[r["fault_type"]][r["outcome"]] += 1
        table["ALL"][r["outcome"]] += 1
    edit = defaultdict(list)
    for r in recs:
        if r["outcome"] == "FIXED" and r.get("ast_edit_size") is not None:
            edit[r["fault_type"]].append(r["ast_edit_size"])

    summary = {"seed": args.seed, "per_type_cap": args.per_type, "elapsed_s": round(elapsed, 1),
               "skipped": meta, "results": {}}
    lines = ["# Controlled-corruption evaluation (Step 2)", "",
             f"Seed {args.seed}, up to {args.per_type} effective variants per fault type; "
             "bases = gold SQL left UNCHANGED in Step 1.", "",
             "| Fault type | N | FIXED (RSR) | WRONG_FIX | ABSTAIN | MISSED | CRASH | avg AST edit (fixed) |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for ft in list(FAULT_TYPES) + ["ALL"]:
        c = table[ft]
        n = sum(c.values())
        if not n:
            continue
        pct = lambda k: 100.0 * c.get(k, 0) / n  # noqa: E731
        sizes = edit[ft] if ft != "ALL" else [x for v in edit.values() for x in v]
        avg_edit = round(sum(sizes) / len(sizes), 2) if sizes else None
        summary["results"][ft] = {"n": n, **{k: c.get(k, 0) for k in outcomes},
                                  "repair_success_rate_pct": round(pct("FIXED"), 2),
                                  "false_repair_rate_pct": round(pct("WRONG_FIX"), 2),
                                  "abstention_rate_pct": round(pct("ABSTAIN"), 2),
                                  "avg_ast_edit_fixed": avg_edit}
        cells = " | ".join(f"{c.get(k, 0)} ({pct(k):.1f}%)" for k in outcomes)
        lines.append(f"| {ft} | {n} | {cells} | {avg_edit} |")
    used = [r for r in recs if r.get("llm_calls")]
    if used:
        calls = sum(r["llm_calls"] for r in used)
        chose = sum(1 for r in used for e in r.get("intent_log", []) if e.get("type") == "choose")
        rejected = sum(1 for r in used for e in r.get("intent_log", []) if e.get("verdict") == "no")
        summary["llm"] = {"variants_using_llm": len(used), "llm_calls": calls,
                          "choose_calls": chose, "candidates_rejected_by_intent": rejected}
        lines += ["", f"Gate F (LLM): dùng cho {len(used)} câu, {calls} lần gọi; "
                      f"{chose} lần chọn cách nối bảng; {rejected} bản sửa bị LLM loại vì sai ý."]
    lines += ["", "Skipped while generating variants:", ""]
    lines += [f"- {ft}: {sk}" for ft, sk in meta.items()]

    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    with open(os.path.join(args.out, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
