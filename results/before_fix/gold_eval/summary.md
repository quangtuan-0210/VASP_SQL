# Gold-SQL regression check (Step 1)

Evaluated: **1534** samples (gold exec errors skipped: 0)

| Status | Count | % |
|---|---:|---:|
| UNCHANGED | 1488 | 97.0 |
| REPAIRED | 2 | 0.13 |
| ABSTAIN | 44 | 2.87 |
| CRASH | 0 | 0.0 |

Regression (REPAIRED and result no longer matches gold): **1** (0.07%)

## False alarms by first failing gate

| Gate | Count |
|---|---:|
| B | 45 |
| E | 1 |

## False alarms by cause

| Cause | Count |
|---|---:|
| unjoined | 32 |
| could not be resolved | 13 |
| timed out | 1 |

## Per database

| DB | UNCHANGED | REPAIRED | ABSTAIN | CRASH |
|---|---:|---:|---:|---:|
| california_schools | 87 | 0 | 2 | 0 |
| card_games | 175 | 0 | 16 | 0 |
| codebase_community | 174 | 2 | 10 | 0 |
| debit_card_specializing | 63 | 0 | 1 | 0 |
| european_football_2 | 122 | 0 | 7 | 0 |
| financial | 106 | 0 | 0 | 0 |
| formula_1 | 170 | 0 | 4 | 0 |
| student_club | 158 | 0 | 0 | 0 |
| superhero | 129 | 0 | 0 | 0 |
| thrombosis_prediction | 161 | 0 | 2 | 0 |
| toxicology | 143 | 0 | 2 | 0 |
