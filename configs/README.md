# Thư mục `configs/` — Cấu hình thực nghiệm

Thư mục này chứa các file cấu hình cài đặt thông số thí nghiệm cho hệ thống VASP-SQL, giúp người dùng dễ dàng tùy chỉnh tham số mà không cần sửa vào mã nguồn.

---

## Chi tiết các file và nhiệm vụ

### `bird_config.yaml`
* **Nhiệm vụ:** Thiết lập toàn bộ thông số chạy thử nghiệm trên benchmark BIRD (SQLite).
* **Nội dung cấu hình gồm 5 phần chính:**
  1. **`benchmark`**: Đường dẫn tới dataset BIRD có sẵn trên máy tính , file `dev.json`, file `dev_tables.json`, và thư mục chứa các SQLite databases (`dev_databases`).
  2. **`safety`**: Luật thực thi sandbox an toàn (chế độ chỉ đọc, cấm DDL/DML, timeout 5.0 giây, giới hạn tối đa 1.000 dòng).
  3. **`repair_budget`**: Ngân sách sửa lỗi (tối đa 5 ứng viên sửa, tối đa 2 lần gọi LLM, thời gian sửa tối đa 15 giây, độ tự tin tối thiểu 0.75).
  4. **`generators`**: Danh sách cấu hình cho 3 AI sinh SQL thử nghiệm:
     * G1: `Baseline-Single-Agent`
     * G2: `MAC-SQL`
     * G3: `LangChain-SQL-Agent` (giới hạn tối đa 5 bước lặp)
  5. **`metrics`**: Danh sách các chỉ số khoa học cần đo lường (`ExecutionAccuracy`, `RepairSuccessRate`, `RegressionRate`, `FalseRepairRate`, `AbstentionRate`, `ASTEditSize`, `LatencyAndCost`).
