# Thư mục `vasp_sql/ast/` — Phân tích Cú pháp AST & Nhận diện Phạm vi (Scope)

Thư mục này chịu trách nhiệm biến câu lệnh SQL dạng chuỗi thành cây cú pháp trừu tượng (Abstract Syntax Tree - AST) có gắn kèm thông tin ngữ cảnh phạm vi (Scope), hỗ trợ các câu truy vấn phức tạp gồm subquery, bí danh (alias) và CTE (`WITH ...`).

---

## Chi tiết các file và nhiệm vụ

### 1. `parser.py` — `SQLParser`
* **`parse(sql, dialect='sqlite')`**: Phân tích cú pháp SQL thành cây AST của thư viện `sqlglot`.
* **`to_sql(ast, dialect='sqlite', pretty=False)`**: Chuyển đổi ngược cây AST thành chuỗi câu lệnh SQL chuẩn xác.
* **`validate_syntax(sql, dialect='sqlite')`** *(Gate A)*: Kiểm tra cú pháp SQL có hợp lệ trong dialect mục tiêu không, trả về `ParserFeedback` kèm số dòng, số cột bị lỗi.
* **`calculate_ast_edit_size(orig_ast, rep_ast)`**: Đo khoảng cách sửa đổi trên cây AST (đếm số node AST bị thay đổi) để đảm bảo nguyên tắc sửa cục bộ tối thiểu (AST Locality).

---

### 2. `scope.py` — `ScopeAnalyzer`
* **`ColumnBinding`**: Đối tượng liên kết một cột trong query với bảng vật lý sở hữu nó, phát hiện các trường hợp tên cột bị mơ hồ (ambiguous) khi nhiều bảng trong scope cùng có cột đó.
* **`TableBinding`**: Lưu trữ bảng, bí danh (alias), cờ nhận diện bảng thật, bảng tạm CTE hay subquery lồng.
* **`ScopeContext`**: Không gian ngữ cảnh của truy vấn (lưu danh sách bảng, CTE, cột và các mệnh đề JOIN).
* **`ScopeAnalyzer.analyze(query, schema)`**: Quét toàn bộ cây AST, bóc tách cấu trúc `WITH`, `FROM`, `JOIN` và tự động phân giải từng cột về đúng bảng vật lý dựa trên `SchemaMetadata`.

---

### 3. `joins.py` — Phân tích Phép nối theo từng SELECT & Quản lý Bí danh
* **Nhiệm vụ:** Giải quyết triệt để 2 lỗi thường gặp khi kiểm tra JOIN trên toàn bộ câu truy vấn:
  * Tránh báo lỗi nhầm khi bảng trong subquery (`IN`, `=`) bị gộp chung với bảng ở câu truy vấn ngoài.
  * Tránh bỏ sót lỗi khi câu có nhiều bảng nhưng bị thiếu điều kiện nối giữa 2 bảng cụ thể.
* **Các chức năng chính:**
  * **`from_items(select)`**: Lấy chính xác các mục trong mệnh đề `FROM` trực tiếp thuộc về khối `SELECT` đó.
  * **`alias_to_table(select)`**: Lập bản đồ ánh xạ từ bí danh viết tắt (`T1`, `T2`) về tên bảng CSDL thực tế cho từng phạm vi con.
  * **`unjoined_table_groups(select, schema)`**: Xây dựng đồ thị liên thông cục bộ giữa các bảng trong một mệnh đề SELECT; phát hiện chính xác các nhóm bảng bị rời rạc (chưa có điều kiện nối ON/WHERE) để kích hoạt bản vá nối bảng.
