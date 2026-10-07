# Thư mục `tests/` — Bộ kiểm thử tự động (Unit Tests)

Thư mục này chứa toàn bộ các bài kiểm thử tự động bằng framework `pytest`, đóng vai trò là "hàng rào bảo hiểm" đảm bảo code của hệ thống luôn hoạt động ổn định và chính xác.

---

## Hướng dẫn chạy kiểm thử

Mở terminal tại thư mục gốc của dự án (`d:\Projects\VAST_SQL`) và chạy lệnh:

```powershell
py -m pytest -v
```

Để chạy riêng một file test cụ thể:
```powershell
py -m pytest -v tests/test_phase1.py
py -m pytest -v tests/test_phase2.py
```

---

## Chi tiết các file test và nhiệm vụ

### 1. [`test_phase1.py`](file:///d:/Projects/VAST_SQL/tests/test_phase1.py) (6 bài test)
* `test_core_models_instantiation`: Kiểm tra khởi tạo và xác thực các model dữ liệu `ColumnMetadata`, `TableMetadata`, `SchemaMetadata`, `RepairResult`.
* `test_schema_introspection_from_sqlite`: Kiểm tra hàm tự động nạp cấu trúc database từ file SQLite (đọc đúng tên bảng, kiểu dữ liệu, khóa chính và khóa ngoại).
* `test_sandbox_safe_select`: Kiểm tra chạy một câu lệnh `SELECT` hợp lệ trong Sandbox, kiểm tra số dòng kết quả và mã hash fingerprint.
* `test_sandbox_blocks_ddl_and_dml`: **Bài test an toàn Gate D**: Kiểm tra Sandbox chặn đứng 100% các câu lệnh nguy hiểm (`DROP TABLE`, `DELETE`, `UPDATE`, `INSERT`).
* `test_sandbox_runtime_error_reporting`: Kiểm tra phân loại chính xác lỗi database (`no_such_column`, `no_such_table`).
* `test_sandbox_timeout`: **Bài test ngắt câu lệnh bị đơ**: Chạy một câu SQL vòng lặp đệ quy vô hạn và xác nhận Sandbox tự ngắt câu lệnh sau thời gian timeout.

---

### 2. [`test_phase2.py`](file:///d:/Projects/VAST_SQL/tests/test_phase2.py) (5 bài test)
* `test_sql_cleaner_variations`: Kiểm tra bộ làm sạch SQL bóc tách chính xác từ khối markdown (```sql ... ```), câu đàm thoại thừa, CTE query, và từ chối chuỗi rác.
* `test_bird_dataset_loader`: Kiểm tra nạp thành công 1.534 câu hỏi từ file `dev.json` thực tế trên máy, lọc câu hỏi và nạp schema thật của database `california_schools`.
* `test_single_agent_adapter`: Kiểm tra chuẩn hóa kết quả từ Agent G1 (cả trường hợp sinh đúng và trường hợp bị lỗi).
* `test_mac_sql_adapter`: Kiểm tra bóc tách SQL và metadata phân rã bài toán từ Agent G2 (MAC-SQL).
* `test_langchain_adapter`: Kiểm tra trích xuất các bước gọi tool (`intermediate_steps`) và câu query từ Agent G3 (LangChain).
