# Controlled-corruption evaluation (Step 2)

Seed 42, up to 300 effective variants per fault type; bases = gold SQL left UNCHANGED in Step 1.

| Fault type | N | FIXED (RSR) | WRONG_FIX | ABSTAIN | MISSED | CRASH | avg AST edit (fixed) |
|---|---:|---:|---:|---:|---:|---:|---:|
| column_typo | 300 | 292 (97.3%) | 2 (0.7%) | 6 (2.0%) | 0 (0.0%) | 0 (0.0%) | 0.23 |
| table_typo | 300 | 3 (1.0%) | 0 (0.0%) | 297 (99.0%) | 0 (0.0%) | 0 (0.0%) | 0.0 |
| join_drop | 300 | 265 (88.3%) | 9 (3.0%) | 22 (7.3%) | 4 (1.3%) | 0 (0.0%) | 7.0 |
| ALL | 900 | 560 (62.2%) | 11 (1.2%) | 325 (36.1%) | 4 (0.4%) | 0 (0.0%) | 3.43 |

Skipped while generating variants:

- column_typo: {}
- table_typo: {}
- join_drop: {'not_applicable': 109, 'ineffective': 23}
