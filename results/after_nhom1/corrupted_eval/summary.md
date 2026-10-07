# Controlled-corruption evaluation (Step 2)

Seed 42, up to 300 effective variants per fault type; bases = gold SQL left UNCHANGED in Step 1.

| Fault type | N | FIXED (RSR) | WRONG_FIX | ABSTAIN | MISSED | CRASH | avg AST edit (fixed) |
|---|---:|---:|---:|---:|---:|---:|---:|
| column_typo | 300 | 292 (97.3%) | 0 (0.0%) | 8 (2.7%) | 0 (0.0%) | 0 (0.0%) | 0.23 |
| table_typo | 300 | 300 (100.0%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) | 0.0 |
| join_drop | 300 | 244 (81.3%) | 0 (0.0%) | 52 (17.3%) | 4 (1.3%) | 0 (0.0%) | 7.0 |
| ALL | 900 | 836 (92.9%) | 0 (0.0%) | 60 (6.7%) | 4 (0.4%) | 0 (0.0%) | 2.12 |

Skipped while generating variants:

- column_typo: {}
- table_typo: {}
- join_drop: {'not_applicable': 109, 'ineffective': 23}
