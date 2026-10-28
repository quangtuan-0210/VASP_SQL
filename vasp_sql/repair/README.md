# Thư mục `vasp_sql/repair/` — Lập Kế hoạch & Áp dụng Bản vá AST

Thư mục này chịu trách nhiệm biến các chẩn đoán lỗi thành các bản vá cục bộ trên cây cú pháp AST (AST Patching), sinh ra các câu SQL ứng viên và bớt cột thừa ở SELECT.

---

## Chi tiết các file và nhiệm vụ

### 1. `patches.py` — Các loại Bản vá AST Cục bộ *(thuật toán gốc)*
* **`BaseASTPatch`**: Lớp trừu tượng cho mọi thao tác vá AST, bắt buộc triển khai `apply(ast) -> (new_ast, was_modified)`.
* **`ReplaceColumnPatch`**: Vá lỗi gõ sai tên cột. Tự động nhận diện bí danh bảng (`T1.column_name`) hoặc tên bảng gốc để thay thế chính xác node cột mà không làm ảnh hưởng đến cấu trúc câu.
* **`ReplaceTablePatch`**: Vá lỗi gõ sai tên bảng trong mệnh đề `FROM`/`JOIN`, đồng thời bảo toàn nguyên vẹn bí danh bảng (alias) và các tiền tố tham chiếu.
* **`AddJoinPatch`**: Vá lỗi thiếu điều kiện JOIN (Cartesian product). Xóa bỏ cách viết bảng phân tách bằng dấu phẩy trong `FROM` và chèn vào mệnh đề `INNER JOIN ... ON ...` chuẩn xác theo đường đi khóa ngoại ngắn nhất từ đồ thị schema.
* **`ReplacePredicateValuePatch`**: Vá lỗi giá trị lọc trong mệnh đề `WHERE` khi câu truy vấn ra 0 dòng kết quả (được điều phối qua `values.py`).

---

### 2. `planner.py` — `RepairPlanner` *(thuật toán gốc)*
* **Nhiệm vụ:** Bộ lập kế hoạch sửa lỗi và quản lý ứng viên.
* **`CandidateQuery`**: Đóng gói câu truy vấn ứng viên sau khi được vá (`repaired_sql`, `repaired_ast`, danh sách bản vá đã áp dụng, điểm số chi phí, kích thước biến đổi AST edit size).
* **`plan_candidates(ast, report, schema, kg)`**: 
  * Tiếp nhận AST gốc và báo cáo chẩn đoán `DiagnosticReport`.
  * Lựa chọn và tổ hợp các bản vá cục bộ phù hợp.
  * Sinh danh sách các ứng viên có thứ tự ưu tiên (ưu tiên bản vá có độ tin cậy cao và can thiệp AST tối thiểu).
  * Kiểm soát số lượng ứng viên theo ngân sách `RepairBudget` (mặc định tối đa 5 ứng viên).

---

### 3. `projection.py` — Bỏ cột thừa *(tinh chỉnh 1)*
* **Nhiệm vụ:** Sửa câu chạy được nhưng SELECT trả về thêm cột mà câu hỏi không hỏi (BIRD so khớp từng dòng nên thừa cột là sai).
* **Cách làm:** LLM chỉ chọn giữ cột nào trong danh sách đang có; bản vá chỉ được **bớt** cột ở SELECT ngoài cùng.
  Câu sau khi bớt phải qua lại Gate A–E và giữ nguyên số dòng; không chắc thì giữ nguyên câu.
* **Bật bằng:** `--projection` (`RepairPolicy.allow_projection_repair`). Được gọi từ `_projection_attempt` trong `pipeline.py`.
