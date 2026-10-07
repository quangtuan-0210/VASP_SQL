# Gold-SQL regression check (Step 1)

Evaluated: **1534** samples (gold exec errors skipped: 0)

| Status | Count | % |
|---|---:|---:|
| UNCHANGED | 1532 | 99.87 |
| REPAIRED | 1 | 0.07 |
| ABSTAIN | 1 | 0.07 |
| CRASH | 0 | 0.0 |

Regression (REPAIRED and result no longer matches gold): **1** (0.07%)

## False alarms by first failing gate

| Gate | Count |
|---|---:|
| E | 1 |
| B | 1 |

## False alarms by cause

| Cause | Count |
|---|---:|
| timed out | 1 |
| unjoined | 1 |

## Per database

| DB | UNCHANGED | REPAIRED | ABSTAIN | CRASH |
|---|---:|---:|---:|---:|
| california_schools | 89 | 0 | 0 | 0 |
| card_games | 191 | 0 | 0 | 0 |
| codebase_community | 184 | 1 | 1 | 0 |
| debit_card_specializing | 64 | 0 | 0 | 0 |
| european_football_2 | 129 | 0 | 0 | 0 |
| financial | 106 | 0 | 0 | 0 |
| formula_1 | 174 | 0 | 0 | 0 |
| student_club | 158 | 0 | 0 | 0 |
| superhero | 129 | 0 | 0 | 0 |
| thrombosis_prediction | 163 | 0 | 0 | 0 |
| toxicology | 145 | 0 | 0 | 0 |
