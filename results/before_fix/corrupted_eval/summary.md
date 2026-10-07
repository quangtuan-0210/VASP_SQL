# Controlled-corruption evaluation (Step 2)

Seed 42, up to 300 effective variants per fault type; bases = gold SQL left UNCHANGED in Step 1.

| Fault type | N | FIXED (RSR) | WRONG_FIX | ABSTAIN | MISSED | CRASH | avg AST edit (fixed) |
|---|---:|---:|---:|---:|---:|---:|---:|
| column_typo | 300 | 64 (21.3%) | 0 (0.0%) | 236 (78.7%) | 0 (0.0%) | 0 (0.0%) | 0.98 |
| table_typo | 300 | 3 (1.0%) | 0 (0.0%) | 297 (99.0%) | 0 (0.0%) | 0 (0.0%) | 0.0 |
| join_drop | 300 | 0 (0.0%) | 0 (0.0%) | 247 (82.3%) | 53 (17.7%) | 0 (0.0%) | None |
| ALL | 900 | 67 (7.4%) | 0 (0.0%) | 780 (86.7%) | 53 (5.9%) | 0 (0.0%) | 0.94 |

Skipped while generating variants:

- column_typo: {}
- table_typo: {}
- join_drop: {'not_applicable': 109, 'ineffective': 21}
