# Thư mục `scripts/` — Các Kịch bản Đánh giá & Thực nghiệm Thực tế

Thư mục này chứa toàn bộ các kịch bản thực thi đánh giá benchmark BIRD, chạy thực nghiệm 3 AI sinh SQL và đo lường các phương pháp đối chứng theo kế hoạch nghiên cứu.

---

## Danh sách các script và nhiệm vụ

**Gốc hay tinh chỉnh:** thuật toán gốc chạy bằng `eval_gold.py`, `eval_corrupted.py`, `eval_generated.py` (mặc định / `--llm`).
Tinh chỉnh 1 = `eval_generated.py --projection`; tinh chỉnh 2 = `eval_gated.py`. `eval_baselines.py` là đối chứng.

### 1. `make_splits.py` — Phân chia Tập Dữ liệu Chuẩn
* **Nhiệm vụ:** Chia 1.534 câu hỏi của BIRD dev thành 2 phần cố định có phân tầng (stratified) theo cặp `(database, độ khó)`:
  * **Tập `tune` (~300 câu):** Dùng để thử nghiệm, tìm lỗi, tinh chỉnh thuật toán và prompt.
  * **Tập `report` (1.234 câu còn lại):** Tập kiểm thử giữ kín (holdout test set), chỉ chạy một lần khi đã chốt mã nguồn để lấy số liệu vào bài báo.
* **Cách chạy:**
  ```powershell
  py scripts/make_splits.py
  ```

---

### 2. `run_generator.py` — Chạy các AI Sinh SQL
* **Nhiệm vụ:** Chạy một trong 3 AI sinh SQL (`g1`, `g2`, `g3`) trên tập `tune` hoặc `report` và lưu đầu ra đã chuẩn hóa (chọn thư mục bằng `--out`).
* **Cách chạy:**
  ```powershell
  py scripts/run_generator.py --generator g1 --split tune
  ```

---

### 3. `eval_gold.py` — Đánh giá Tính Không Phá hoại trên Gold SQL *(gốc; `--projection` cho tinh chỉnh 1)*
* **Nhiệm vụ:** Chạy hệ thống VASP-SQL trên 1.534 câu Gold SQL chuẩn của BIRD dev để đo xem hệ thống có giữ nguyên câu đúng không (`UNCHANGED`) hay sửa làm hỏng kết quả (`Regression Rate`).
* **Cách chạy:**
  ```powershell
  py scripts/eval_gold.py
  ```

---

### 4. `eval_corrupted.py` — Đánh giá Khả năng Sửa Lỗi Cố ý (Corrupted Benchmark) *(thuật toán gốc)*
* **Nhiệm vụ:** Thử nghiệm trên 900 câu bị tiêm lỗi có kiểm soát (300 câu gõ sai cột, 300 câu gõ sai bảng, 300 câu thiếu JOIN). Đo lường tỷ lệ sửa đúng, tỷ lệ từ chối (`ABSTAIN`) và tỷ lệ sửa sai.
* **Cách chạy:**
  ```powershell
  py scripts/eval_corrupted.py --split report --seed 2027 --gold-results <thư mục kết quả gold>/per_sample.jsonl --out <thư mục kết quả>
  py scripts/eval_corrupted.py ... --ast-only   # đối chứng AST-only
  py scripts/eval_corrupted.py ... --llm        # kèm Gate F bằng LLM
  ```

---

### 5. `eval_generated.py` — Chấm điểm SQL do AI Sinh ra (Trước & Sau VASP-SQL) *(gốc + tinh chỉnh 1)*
* **Nhiệm vụ:** Đọc kết quả SQL do G1/G2/G3 sinh ra, chạy qua VASP-SQL và so sánh Execution Accuracy (EX) trước và sau khi sửa lỗi.
  Ghi thêm từng câu: loại bản vá, số nút AST thay đổi, token và thời gian của bước sửa.
* **Cách chạy:**
  ```powershell
  py scripts/eval_generated.py --gen g1 --split report                    # VASP-SQL không LLM (A–E)
  py scripts/eval_generated.py --gen g1 --split report --llm              # VASP-SQL đầy đủ (A–F)
  py scripts/eval_generated.py --gen g1 --split report --llm --projection # tinh chỉnh 1: + bỏ cột thừa (eval_llm_proj)
  py scripts/eval_generated.py --gen g1 --split report --ast-only         # đối chứng AST-only
  py scripts/eval_generated.py --gen g1 --split report --no-value-repair  # ablation
  py scripts/eval_generated.py --gen g1 --split report --no-schema-graph  # ablation
  ```

---

### 6. `eval_baselines.py` — Đánh giá các Phương pháp Đối chứng
* **Nhiệm vụ:** Đo lường các baseline sửa lỗi truyền thống để so sánh với VASP-SQL:
  * `rewrite`: LLM viết lại toàn bộ câu SQL từ đầu.
  * `execfb`: LLM xem câu SQL và lỗi thực thi SQLite để viết lại.
  * `execfb_verify`: LLM viết lại có kèm giám khảo độc lập chấm điểm.
* **Cách chạy:**
  ```powershell
  py scripts/eval_baselines.py --gen g1 --split report --mode rewrite
  py scripts/eval_baselines.py --gen g1 --split report --mode execfb
  ```

---

### 7. `eval_gated.py` — Kết hợp có chốt an toàn *(tinh chỉnh 2, phương pháp đề xuất cuối)*
* **Nhiệm vụ:** VASP-SQL + bỏ cột thừa chạy trước; câu nó không sửa mà có dấu hiệu đáng ngờ (lỗi chạy, VASP từ chối,
  rỗng, có NULL, có dòng lặp) thì lấy bản Execution-feedback; còn lại giữ nguyên. Ghép offline từ kết quả từng câu,
  không gọi LLM thêm. Quy tắc đóng băng ngày 2026-10-19 trên tập tune.
* **Cách chạy:** `py scripts/eval_gated.py --gen g1 --split report --config configs/bird_may_nay.yaml`

---

### 7b. `stats_report.py` — Thống kê cuối
* **Nhiệm vụ:** EX + khoảng tin cậy 95% (bootstrap), McNemar chính xác, RSR/RR/sửa sai/từ chối, theo độ khó, database,
  loại lỗi và loại bản vá, số nút AST thay đổi, chi phí & độ trễ. Có thêm bảng gộp tune + report nếu đủ dữ liệu.
* **Cách chạy:** `py scripts/stats_report.py --split report --price-in 0.09 --price-out 0.55`

---

### 8. `eval_common.py` — Thư viện Tiện ích Dùng chung
* **Nhiệm vụ:** Chứa các hàm dùng chung: thực thi SQL với timeout, chuẩn hóa bảng kết quả để so sánh độ chính xác EX (sắp xếp dòng/cột, chuẩn hóa số thực), ghi file tổng kết Markdown/JSON.

---

### 9. `test_llm_connection.py` — Kiểm tra Kết nối Server LLM
* **Nhiệm vụ:** Kiểm tra nhanh kết nối tới server LLM chuẩn OpenAI theo file `.env`.
* **Cách chạy:**
  ```powershell
  py scripts/test_llm_connection.py
  ```
