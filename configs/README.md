# Thư mục `configs/` — Cấu hình Thực nghiệm & Phân chia Dữ liệu

Thư mục này chứa các file cấu hình cài đặt thông số thí nghiệm cho hệ thống VASP-SQL, giúp người dùng dễ dàng tùy chỉnh tham số mà không cần sửa vào mã nguồn.

---

## Chi tiết các file và thư mục con

### 1. `bird_config.yaml`
* **Nhiệm vụ:** Thiết lập toàn bộ thông số chạy thử nghiệm trên benchmark BIRD (SQLite).
* **Nội dung cấu hình gồm 5 phần chính:**
  1. **`benchmark`**: Đường dẫn tới dataset BIRD (`dev.json`, `dev_tables.json`, và thư mục chứa các SQLite databases `dev_databases`).
  2. **`safety`**: Luật thực thi sandbox an toàn (chế độ chỉ đọc, cấm DDL/DML, timeout 5.0 giây, giới hạn tối đa 1.000 dòng).
  3. **`repair_budget`**: Ngân sách sửa lỗi (tối đa 5 ứng viên sửa, tối đa 2 lần gọi LLM, thời gian sửa tối đa 15 giây, độ tự tin tối thiểu 0.75).
  4. **`generators`**: Danh sách cấu hình cho 3 AI sinh SQL thử nghiệm:
     * G1: `Baseline-Single-Agent`
     * G2: `MAC-SQL`
     * G3: `LangChain-SQL-Agent` (giới hạn tối đa 5 bước lặp)
  5. **`metrics`**: Danh sách các chỉ số khoa học cần đo lường (`ExecutionAccuracy`, `RepairSuccessRate`, `RegressionRate`, `FalseRepairRate`, `AbstentionRate`, `ASTEditSize`, `LatencyAndCost`).

---

### 2. Thư mục `splits/` — Phân chia Tập Thử nghiệm Chuẩn
* **`bird_dev_split.json`**: File JSON chứa danh sách chỉ số (indices) câu hỏi được chia cố định bằng `scripts/make_splits.py` (seed 2026):
  * **`tune` (300 câu):** Tập câu hỏi dùng để phát triển, tìm lỗi và tinh chỉnh thuật toán.
  * **`report` (1.234 câu còn lại):** Tập câu hỏi giữ kín (holdout set), dùng để đánh giá và xuất số liệu khách quan cuối cùng cho bài báo / báo cáo đồ án.
