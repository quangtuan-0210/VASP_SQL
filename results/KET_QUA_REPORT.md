# Kết quả cuối

Khoảng tin cậy 95%: bootstrap theo mẫu (10000 lần, seed 2026). p: McNemar chính xác so với 'không sửa' (ý nghĩa ở mức 0,05). RSR = câu sai được sửa đúng / câu sai; RR = câu đúng bị làm hỏng / câu đúng; Sửa sai = bản sửa không ra kết quả đúng / số câu bị sửa.

Mốc 'không sửa' dùng chung cho mọi cấu hình (lấy từ `eval/`). Ba câu chạy sát giới hạn thời gian cho EX trước khác nhau giữa các lần chạy (G2: câu 518; G3: câu 1473, 1476); các câu này vẫn được so với mốc chung, nên có thể xuất hiện trong cột 'Làm hỏng' dù bản sửa không đổi câu.

Kết hợp có chốt an toàn: lấy bản VASP-SQL (+ bỏ cột thừa) nếu VASP đã sửa; ngược lại gửi sang Execution-feedback khi câu gốc có dấu hiệu đáng ngờ (lỗi chạy, VASP từ chối, rỗng, có NULL, có dòng lặp); còn lại giữ nguyên. Chi tiết từng câu: `generators/<g>/report/gated_execfb/`.

## Tập `report`

### G1 — một lần gọi LLM (n = 1234)

| Cấu hình | EX % [CI 95%] | Tăng EX [CI 95%] | Sửa đúng | Làm hỏng | RSR % | RR % | Sửa sai % | Từ chối % | p (McNemar) |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Không sửa (đầu ra gốc) | 63.21 [60.45, 65.88] | — | 0 | 0 | 0.0 | 0.0 | — | — | — |
| Execution-feedback repair | 64.18 [61.43, 66.86] | +0.97 [-0.24, +2.27] | 37 | 25 | 8.15 | 3.21 | 65.95 | 0.0 | 0.1619 |
| VASP-SQL + bỏ cột thừa | 63.70 [60.94, 66.45] | +0.49 [+0.08, +0.97] | 7 | 1 | 1.54 | 0.13 | 68.18 | 1.62 | 0.0703 |
| Kết hợp có chốt an toàn (đề xuất) | 65.07 [62.32, 67.75] | +1.86 [+0.97, +2.84] | 30 | 7 | 6.61 | 0.9 | 64.44 | 0.0 | 0.0002 |

**Theo độ khó** (EX %)

| | n | Không sửa (đầu ra gốc) | Execution-feedback repair | VASP-SQL + bỏ cột thừa | Kết hợp có chốt an toàn (đề xuất) |
| --- | ---: | ---: | ---: | ---: | ---: |
| challenging | 116 | 52.59 | 52.59 | 52.59 | 54.31 |
| moderate | 375 | 54.67 | 56.00 | 55.20 | 57.07 |
| simple | 743 | 69.18 | 70.12 | 69.72 | 70.79 |

**Theo database** (EX %)

| | n | Không sửa (đầu ra gốc) | Execution-feedback repair | VASP-SQL + bỏ cột thừa | Kết hợp có chốt an toàn (đề xuất) |
| --- | ---: | ---: | ---: | ---: | ---: |
| california_schools | 71 | 46.48 | 50.70 | 47.89 | 50.70 |
| card_games | 154 | 55.84 | 55.19 | 57.14 | 57.14 |
| codebase_community | 149 | 68.46 | 69.80 | 67.79 | 70.47 |
| debit_card_specializing | 52 | 59.62 | 57.69 | 61.54 | 61.54 |
| european_football_2 | 103 | 69.90 | 66.02 | 69.90 | 66.99 |
| financial | 86 | 54.65 | 55.81 | 55.81 | 56.98 |
| formula_1 | 140 | 60.71 | 65.71 | 60.71 | 64.29 |
| student_club | 127 | 78.74 | 82.68 | 78.74 | 81.89 |
| superhero | 104 | 84.62 | 86.54 | 86.54 | 86.54 |
| thrombosis_prediction | 131 | 60.31 | 58.78 | 60.31 | 63.36 |
| toxicology | 117 | 48.72 | 48.72 | 48.72 | 48.72 |

**Câu sai ban đầu — bị chặn ở đâu** (tổng 454 câu sai; 0 câu không sinh được SQL)

| Nguyên nhân | Số câu |
| --- | ---: |
| chạy được nhưng sai kết quả | 435 |
| Gate B | 13 |
| Gate E | 5 |
| Gate A | 1 |

**Theo loại bản vá — VASP-SQL + bỏ cột thừa**

| Loại bản vá | Số câu sửa | Sai → đúng | Đúng → sai |
| --- | ---: | ---: | ---: |
| Bỏ cột thừa | 12 | 2 | 1 |
| Đổi tên cột | 1 | 0 | 0 |
| Sửa giá trị lọc | 9 | 5 | 0 |

**Tính cục bộ** — số nút AST thay đổi trung bình (câu được sửa): VASP-SQL + bỏ cột thừa: 3.6

### G2 — MAC-SQL (n = 1234)

| Cấu hình | EX % [CI 95%] | Tăng EX [CI 95%] | Sửa đúng | Làm hỏng | RSR % | RR % | Sửa sai % | Từ chối % | p (McNemar) |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Không sửa (đầu ra gốc) | 65.24 [62.56, 67.91] | — | 0 | 0 | 0.0 | 0.0 | — | — | — |
| Execution-feedback repair | 64.83 [62.07, 67.50] | -0.41 [-1.70, +0.81] | 30 | 35 | 6.99 | 4.35 | 55.15 | 0.0 | 0.6201 |
| VASP-SQL + bỏ cột thừa | 65.48 [62.80, 68.15] | +0.24 [-0.08, +0.65] | 4 | 1 | 0.93 | 0.12 | 62.5 | 0.89 | 0.3750 |
| Kết hợp có chốt an toàn (đề xuất) | 65.32 [62.64, 67.99] | +0.08 [-0.57, +0.73] | 9 | 8 | 2.1 | 0.99 | 52.54 | 0.0 | 1.0000 |

**Theo độ khó** (EX %)

| | n | Không sửa (đầu ra gốc) | Execution-feedback repair | VASP-SQL + bỏ cột thừa | Kết hợp có chốt an toàn (đề xuất) |
| --- | ---: | ---: | ---: | ---: | ---: |
| challenging | 116 | 54.31 | 52.59 | 54.31 | 54.31 |
| moderate | 375 | 57.87 | 57.33 | 58.40 | 57.60 |
| simple | 743 | 70.66 | 70.52 | 70.79 | 70.93 |

**Theo database** (EX %)

| | n | Không sửa (đầu ra gốc) | Execution-feedback repair | VASP-SQL + bỏ cột thừa | Kết hợp có chốt an toàn (đề xuất) |
| --- | ---: | ---: | ---: | ---: | ---: |
| california_schools | 71 | 49.30 | 52.11 | 49.30 | 50.70 |
| card_games | 154 | 59.09 | 57.14 | 60.39 | 59.74 |
| codebase_community | 149 | 66.44 | 65.10 | 65.77 | 66.44 |
| debit_card_specializing | 52 | 71.15 | 67.31 | 71.15 | 71.15 |
| european_football_2 | 103 | 69.90 | 66.02 | 69.90 | 66.99 |
| financial | 86 | 66.28 | 67.44 | 66.28 | 66.28 |
| formula_1 | 140 | 62.86 | 62.14 | 63.57 | 62.86 |
| student_club | 127 | 80.31 | 81.10 | 81.10 | 80.31 |
| superhero | 104 | 84.62 | 85.58 | 84.62 | 84.62 |
| thrombosis_prediction | 131 | 56.49 | 56.49 | 56.49 | 56.49 |
| toxicology | 117 | 52.99 | 54.70 | 52.99 | 54.70 |

**Câu sai ban đầu — bị chặn ở đâu** (tổng 429 câu sai; 1 câu không sinh được SQL)

| Nguyên nhân | Số câu |
| --- | ---: |
| chạy được nhưng sai kết quả | 421 |
| Gate B | 6 |
| Gate E | 1 |

**Theo loại bản vá — VASP-SQL + bỏ cột thừa**

| Loại bản vá | Số câu sửa | Sai → đúng | Đúng → sai |
|---|---:|---:|---:|
| Bỏ cột thừa | 8 | 3 | 1 |

**Tính cục bộ** — số nút AST thay đổi trung bình (câu được sửa): VASP-SQL + bỏ cột thừa: 4.2

### G3 — LangChain agent (n = 1234)

| Cấu hình | EX % [CI 95%] | Tăng EX [CI 95%] | Sửa đúng | Làm hỏng | RSR % | RR % | Sửa sai % | Từ chối % | p (McNemar) |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Không sửa (đầu ra gốc) | 56.32 [53.57, 59.08] | — | 0 | 0 | 0.0 | 0.0 | — | — | — |
| Execution-feedback repair | 59.72 [56.89, 62.48] | +3.40 [+1.70, +5.19] | 84 | 42 | 15.58 | 6.04 | 55.24 | 0.0 | 0.0002 |
| VASP-SQL + bỏ cột thừa | 60.45 [57.70, 63.13] | +4.13 [+3.00, +5.35] | 54 | 3 | 10.02 | 0.43 | 50.91 | 0.32 | 0.0000 |
| Kết hợp có chốt an toàn (đề xuất) | 61.10 [58.43, 63.78] | +4.78 [+3.32, +6.32] | 72 | 13 | 13.36 | 1.87 | 54.5 | 0.0 | 0.0000 |

**Theo độ khó** (EX %)

| | n | Không sửa (đầu ra gốc) | Execution-feedback repair | VASP-SQL + bỏ cột thừa | Kết hợp có chốt an toàn (đề xuất) |
| --- | ---: | ---: | ---: | ---: | ---: |
| challenging | 116 | 40.52 | 50.00 | 41.38 | 43.10 |
| moderate | 375 | 51.73 | 53.33 | 55.47 | 56.00 |
| simple | 743 | 61.10 | 64.47 | 65.95 | 66.49 |

**Theo database** (EX %)

| | n | Không sửa (đầu ra gốc) | Execution-feedback repair | VASP-SQL + bỏ cột thừa | Kết hợp có chốt an toàn (đề xuất) |
| --- | ---: | ---: | ---: | ---: | ---: |
| california_schools | 71 | 36.62 | 45.07 | 43.66 | 46.48 |
| card_games | 154 | 45.45 | 51.30 | 48.70 | 50.00 |
| codebase_community | 149 | 65.77 | 64.43 | 66.44 | 66.44 |
| debit_card_specializing | 52 | 44.23 | 48.08 | 48.08 | 48.08 |
| european_football_2 | 103 | 55.34 | 56.31 | 61.17 | 61.17 |
| financial | 86 | 59.30 | 62.79 | 62.79 | 63.95 |
| formula_1 | 140 | 53.57 | 55.71 | 57.86 | 57.14 |
| student_club | 127 | 72.44 | 79.53 | 77.17 | 78.74 |
| superhero | 104 | 81.73 | 85.58 | 85.58 | 85.58 |
| thrombosis_prediction | 131 | 48.09 | 51.91 | 52.67 | 54.20 |
| toxicology | 117 | 47.01 | 48.72 | 52.99 | 52.99 |

**Câu sai ban đầu — bị chặn ở đâu** (tổng 539 câu sai; 25 câu không sinh được SQL)

| Nguyên nhân | Số câu |
| --- | ---: |
| chạy được nhưng sai kết quả | 510 |
| Gate B | 3 |
| Gate A | 1 |

**Theo loại bản vá — VASP-SQL + bỏ cột thừa**

| Loại bản vá | Số câu sửa | Sai → đúng | Đúng → sai |
| --- | ---: | ---: | ---: |
| Bỏ cột thừa | 108 | 53 | 1 |
| Đổi tên cột | 1 | 0 | 0 |
| Sửa giá trị lọc | 1 | 1 | 0 |

