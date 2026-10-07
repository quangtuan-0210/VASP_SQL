# Thư mục `vasp_sql/core/` — Mô hình dữ liệu & Quy chuẩn cốt lõi

Thư mục này đóng vai trò là **"bộ khung xương"** và **"ngôn ngữ giao tiếp chung"** cho toàn bộ hệ thống VASP-SQL.

---

## Chi tiết các file và nhiệm vụ

### 1. `schema.py`
* **Nhiệm vụ:** Quản lý "Hồ sơ lý lịch" của cơ sở dữ liệu.
* **Các lớp chính:**
  * `DataType`: Kiểu dữ liệu chuẩn hóa (`INTEGER`, `REAL`, `TEXT`, `BLOB`, `BOOLEAN`).
  * `ColumnMetadata`: Tên cột, kiểu dữ liệu, khóa chính (`is_primary_key`), cho phép NULL (`is_nullable`).
  * `TableMetadata`: Tên bảng, danh sách cột, danh sách khóa chính.
  * `ForeignKeyRelation`: Mối quan hệ khóa ngoại giữa các bảng (`from_table.col -> to_table.col`).
  * `SchemaMetadata`: Toàn bộ cấu trúc của một database.
  * **Hàm đặc biệt:** `SchemaMetadata.from_sqlite_db(...)` — Tự động kết nối tới file SQLite bất kỳ và dùng `PRAGMA` để trích xuất sạch sẽ 100% cấu trúc bảng, cột và khóa ngoại.

---

### 2. `feedback.py`
* **Nhiệm vụ:** Lưu lại kết quả kiểm tra cú pháp và kết quả chạy thử trong Sandbox.
* **Các lớp chính:**
  * `ParserFeedback`: Lưu trạng thái parse cú pháp, dòng/cột bị lỗi.
  * `ExecutionFeedback`: Lưu kết quả chạy thật: Thành công hay thất bại, mã lỗi (`no_such_table`, `no_such_column`, `timeout`), thời gian chạy (`execution_time_ms`), số dòng trả về, và mã băm kết quả (`result_fingerprint`).

---

### 3. `result.py`
* **Nhiệm vụ:** Định nghĩa "Biên bản kết luận cuối cùng" sau khi xử lý câu SQL.
* **Các lớp chính:**
  * `RepairStatus`: 3 trạng thái duy nhất của hệ thống:
    * `UNCHANGED`: Câu gốc đã đúng, giữ nguyên không đụng vào.
    * `REPAIRED`: Đã sửa thành công và vượt qua toàn bộ các cổng kiểm định.
    * `ABSTAIN`: Từ chối sửa nếu không đủ bằng chứng tin cậy.
  * `GateType` & `GateResult`: Kết quả kiểm định của từng cổng (Gate A $\to$ Gate F).
  * `ASTPatchRecord`: Ghi nhận vị trí mổ xẻ trên cây AST (sửa từ gì thành từ gì, lý do sửa và bằng chứng).
  * `RepairResult`: Toàn bộ phiếu kết quả trả về cho người dùng/hệ thống gọi.

---

### 4. `config.py`
* **Nhiệm vụ:** Quản lý các thông số cài đặt an toàn và ngân sách.
* **Các lớp chính:**
  * `ExecutionSafetyPolicy`: Cài đặt luật chạy sandbox (read-only, cấm DDL/DML, timeout 5s, max 1000 dòng).
  * `RepairBudget`: Ngân sách tài nguyên (tối đa 5 ứng viên, tối đa 2 lần gọi LLM, giới hạn thời gian).
  * `BenchmarkConfig`: Cấu hình benchmark (tên benchmark, dialect).

---

### 5. `policy.py`
* **Nhiệm vụ:** Điều khiển chính sách sửa lỗi.
* **Lớp chính:**
  * `RepairPolicy`: Tùy chỉnh danh sách các cổng kiểm định bắt buộc phải chạy, ngưỡng độ tin cậy tối thiểu (`min_confidence_threshold`), cho phép dùng luật ký hiệu hoặc LLM.
