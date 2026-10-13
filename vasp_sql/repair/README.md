# Thư mục `vasp_sql/repair/` — Lập Kế hoạch & Áp dụng Bản vá AST

Thư mục này chịu trách nhiệm biến các chẩn đoán lỗi thành các bản vá cục bộ trên cây cú pháp AST (AST Patching), sinh ra các câu SQL ứng viên và thực hiện sửa lỗi ngữ nghĩa.

---

## Chi tiết các file và nhiệm vụ

### 1. `patches.py` — Các loại Bản vá AST Cục bộ
* **`BaseASTPatch`**: Lớp trừu tượng cho mọi thao tác vá AST, bắt buộc triển khai `apply(ast) -> (new_ast, was_modified)`.
* **`ReplaceColumnPatch`**: Vá lỗi gõ sai tên cột. Tự động nhận diện bí danh bảng (`T1.column_name`) hoặc tên bảng gốc để thay thế chính xác node cột mà không làm ảnh hưởng đến cấu trúc câu.
* **`ReplaceTablePatch`**: Vá lỗi gõ sai tên bảng trong mệnh đề `FROM`/`JOIN`, đồng thời bảo toàn nguyên vẹn bí danh bảng (alias) và các tiền tố tham chiếu.
* **`AddJoinPatch`**: Vá lỗi thiếu điều kiện JOIN (Cartesian product). Xóa bỏ cách viết bảng phân tách bằng dấu phẩy trong `FROM` và chèn vào mệnh đề `INNER JOIN ... ON ...` chuẩn xác theo đường đi khóa ngoại ngắn nhất từ đồ thị schema.
* **`ReplacePredicateValuePatch`**: Vá lỗi giá trị lọc trong mệnh đề `WHERE` khi câu truy vấn ra 0 dòng kết quả (được điều phối qua `values.py`).

---

### 2. `planner.py` — `RepairPlanner`
* **Nhiệm vụ:** Bộ lập kế hoạch sửa lỗi và quản lý ứng viên.
* **`CandidateQuery`**: Đóng gói câu truy vấn ứng viên sau khi được vá (`repaired_sql`, `repaired_ast`, danh sách bản vá đã áp dụng, điểm số chi phí, kích thước biến đổi AST edit size).
* **`plan_candidates(ast, report, schema, kg)`**: 
  * Tiếp nhận AST gốc và báo cáo chẩn đoán `DiagnosticReport`.
  * Lựa chọn và tổ hợp các bản vá cục bộ phù hợp.
  * Sinh danh sách các ứng viên có thứ tự ưu tiên (ưu tiên bản vá có độ tin cậy cao và can thiệp AST tối thiểu).
  * Kiểm soát số lượng ứng viên theo ngân sách `RepairBudget` (mặc định tối đa 5 ứng viên).

---

### 3. `semantic.py` — Sửa lỗi Ngữ nghĩa Cục bộ bằng LLM (Hướng 2)
* **Nhiệm vụ:** Xử lý các câu SQL đã vượt qua Gate A–E (đúng cú pháp, đúng bảng/cột, chạy được) nhưng trả về sai kết quả theo ý câu hỏi tự nhiên.
* **Quy trình 2 pha có kiểm chứng:**
  * **Pha 1 — Đề xuất sửa cục bộ:** LLM chỉ được phép đề xuất tối đa 2 thao tác sửa trong danh sách cố định:
    * `replace_expression`: thay thế biểu thức/cột/hàm.
    * `add_condition`: thêm điều kiện lọc vào `WHERE`.
    * `remove_condition`: loại bỏ điều kiện thừa trong `WHERE`.
    * `set_limit`: chỉnh sửa giá trị `LIMIT` / `OFFSET`.
    * `set_distinct`: bật / tắt `DISTINCT`.
    * *(LLM tuyệt đối không được viết lại toàn bộ câu SQL; hệ thống tự áp thao tác vào đúng node AST)*.
  * **Pha 2 — Giám khảo độc lập (Independent Verification):** Một mô hình LLM độc lập (ví dụ `gpt-oss-120b`) chấm điểm mù giữa câu gốc và câu sau sửa kèm kết quả thực thi để chọn câu trả lời đúng câu hỏi hơn. Nếu không chắc chắn, giữ nguyên câu gốc.
