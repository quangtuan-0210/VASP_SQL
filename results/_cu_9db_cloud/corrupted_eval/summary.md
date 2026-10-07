# Controlled-corruption evaluation (Step 2)

> Run 2026-10-02 on 9/11 BIRD dev databases (codebase_community and european_football_2 excluded: files >400 MB could not be copied to the run environment). Re-run on your machine for the full set.

Seed 42, up to 300 effective variants per fault type; bases = gold SQL left UNCHANGED in Step 1.

| Fault type | N | FIXED (RSR) | WRONG_FIX | ABSTAIN | MISSED | CRASH | avg AST edit (fixed) |
|---|---:|---:|---:|---:|---:|---:|---:|
| column_typo | 300 | 51 (17.0%) | 1 (0.3%) | 248 (82.7%) | 0 (0.0%) | 0 (0.0%) | 0.96 |
| table_typo | 300 | 2 (0.7%) | 0 (0.0%) | 298 (99.3%) | 0 (0.0%) | 0 (0.0%) | 0.0 |
| join_drop | 300 | 0 (0.0%) | 0 (0.0%) | 237 (79.0%) | 63 (21.0%) | 0 (0.0%) | None |
| ALL | 900 | 53 (5.9%) | 1 (0.1%) | 783 (87.0%) | 63 (7.0%) | 0 (0.0%) | 0.92 |

Skipped while generating variants:

- column_typo: {}
- table_typo: {}
- join_drop: {'not_applicable': 100, 'ineffective': 23}
