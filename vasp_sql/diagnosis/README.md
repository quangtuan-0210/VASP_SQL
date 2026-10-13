# Thư mục `vasp_sql/diagnosis/` — Động cơ Chẩn đoán Lỗi Đa tầng

Thư mục này chịu trách nhiệm "bắt bệnh" cho các câu lệnh SQL được sinh ra bởi AI Agent hoặc câu SQL bị lỗi, trước khi chuyển giao thông tin sang bộ lập kế hoạch sửa lỗi (`repair`).

---

## Chi tiết các file và nhiệm vụ

### 1. `engine.py` — `DiagnosisEngine`
* **Nhiệm vụ:** Bộ chẩn đoán 5 tầng (5-layer diagnostic engine) phân tích AST và phản hồi thực thi.
* **Các cấu trúc dữ liệu:**
  * `FaultCategory`: Danh mục phân loại lỗi (`SYNTAX_ERROR`, `TABLE_NOT_FOUND`, `COLUMN_NOT_FOUND`, `MISSING_JOIN`, `EXECUTION_ERROR`, `FILTER_VALUE_MISMATCH`, v.v.).
  * `DiagnosedFault`: Chi tiết từng lỗi được phát hiện (loại lỗi, phần tử mục tiêu, lý do, bằng chứng thực nghiệm, độ tự tin `confidence`, danh sách gợi ý thay thế).
  * `DiagnosticReport`: Bản báo cáo tổng hợp toàn bộ lỗi của một câu SQL.
* **5 Tầng chẩn đoán:**
  * **Tầng 1 (Cú pháp):** Kiểm tra cú pháp thông qua SQLParser.
  * **Tầng 2 (Bảng tồn tại):** Kiểm tra các bảng trong FROM/JOIN có nằm trong SchemaMetadata không.
  * **Tầng 3 (Cột & So khớp mờ):** Rà soát từng cột; nếu cột không tồn tại, sử dụng thuật toán so khớp mờ từ Knowledge Graph để tìm cột ứng viên thay thế gần nhất.
  * **Tầng 4 (Toàn vẹn JOIN):** Kiểm tra tích đề-các (Cartesian product) hoặc thiếu mệnh đề `ON`/`USING` giữa các bảng vật lý độc lập; tự động tra cứu đồ thị để tìm đường nối khóa ngoại.
  * **Tầng 5 (Phản hồi thực thi):** Bắt lỗi runtime từ SQLite Sandbox (`no_such_table`, `no_such_column`, `syntax_error`) và ánh xạ sang lỗi AST tương ứng.

---

### 2. `values.py` — Chẩn đoán Giá trị lọc không khớp CSDL
* **Nhiệm vụ:** Phát hiện tình huống câu SQL đúng cú pháp, đúng bảng/cột, chạy được nhưng **trả về 0 dòng kết quả (rỗng)** do giá trị lọc trong `WHERE` không khớp cách lưu trữ trong CSDL (ví dụ: `CharterNum = '40'` trong khi CSDL lưu `'0040'`).
* **Cơ chế hoạt động:**
  * Rà soát các biểu thức so sánh đẳng thức (`col = val`) hoặc tập hợp (`col IN (...)`).
  * Kiểm tra giá trị thực tế trong cột tương ứng của CSDL.
  * Nếu không có, tìm kiếm giá trị thật gần nhất:
    * Mức 1: Khác biệt cách viết (hoa/thường, khoảng trắng, dấu câu, số 0 ở đầu).
    * Mức 2: Gõ sai nhẹ (khoảng cách Levenshtein ≤ 2) với chuỗi chữ đủ dài.
  * Chỉ đề xuất sửa khi có duy nhất 1 ứng viên rõ ràng; nếu có từ 2 ứng viên ngang nhau trở lên thì từ chối đề xuất (tránh sửa bừa).
