# Thư mục `vasp_sql/verifiers/` — Hệ thống Cổng Kiểm chứng Đa tầng (Multi-Gate Verification)

Thư mục này đóng vai trò là "hàng rào kiểm định chất lượng" nghiêm ngặt trong kiến trúc VASP-SQL, đảm bảo mọi câu SQL ứng viên phải vượt qua tất cả các kiểm chứng trước khi được công nhận là `REPAIRED` hoặc giữ nguyên `UNCHANGED`.

---

## Chi tiết các file và nhiệm vụ

### 1. `gates.py` — `VerificationPipeline` (Gate A → Gate E)
* **Nhiệm vụ:** Quản lý quy trình kiểm định tuần tự qua 5 cổng kỹ thuật:
  * **Gate A — Cú pháp (Syntax Gate):** Sử dụng `SQLParser.validate_syntax()` kiểm tra tính đúng đắn của ngữ pháp SQL trên dialect mục tiêu (SQLite).
  * **Gate B — Cấu trúc & Phạm vi (Schema & Scope Gate):** 
    * Kiểm tra sự tồn tại của tất cả các bảng và cột xuất hiện trong truy vấn.
    * Giải quyết xung đột phạm vi và bí danh (alias).
    * **Chặn đứng Cartesian Product:** Kiểm tra toàn vẹn liên kết JOIN (mọi bảng trong mệnh đề FROM phải có điều kiện ON hoặc WHERE kết nối, tránh sinh tích đề-các làm nổ bộ nhớ).
  * **Gate C — Tương thích Kiểu dữ liệu (Type Consistency Gate):** Kiểm tra tính hợp lệ của các phép toán trên kiểu dữ liệu (không cho phép tính toán số học trên cột kiểu TEXT/BLOB).
  * **Gate D — An toàn Thực thi (Safety Gate):** Phối hợp với `SQLiteSandboxExecutor.check_safety_ast()` để loại bỏ 100% các lệnh có thể sửa đổi cấu trúc hoặc dữ liệu CSDL (`DROP`, `DELETE`, `UPDATE`, `INSERT`, `ALTER`, `PRAGMA`).
  * **Gate E — Thực thi Thực tế (Execution Sandbox Gate):** Chạy thử câu truy vấn trong SQLite Sandbox trong chế độ chỉ đọc, kiểm tra không bị lỗi runtime, không bị timeout và trả về kết quả hợp lệ kèm mã fingerprint SHA-256.

---

### 2. `intent.py` — Kiểm chứng Ngữ nghĩa & Ý đồ Câu hỏi (Gate F)
* **Nhiệm vụ:** Sử dụng LLM độc lập làm giám khảo ngữ nghĩa cho 2 tình huống:
  * **Chức năng 1 (`check`):** Kiểm tra xem câu SQL sau khi sửa đổi có trả lời đúng trọng tâm câu hỏi tự nhiên ban đầu không (`yes` / `no` / `unsure`). Tránh tình trạng câu SQL chạy được trong CSDL nhưng bị lệch sang ý khác.
  * **Chức năng 2 (`choose`):** Khi đồ thị Schema Knowledge Graph tìm thấy nhiều đường nối khóa ngoại hợp lệ giữa 2 bảng nhưng cho ra kết quả dữ liệu khác nhau (ví dụ: nối bảng `superhero` sang `colour` thông qua màu mắt hay màu tóc), LLM đọc câu hỏi tự nhiên để chọn phương án khớp nhất.
* **Nguyên tắc an toàn:**
  * LLM tại Gate F chỉ đóng vai trò phân xử hoặc loại bỏ giữa các phương án đã vượt qua Gate A–E; tuyệt đối không tự sinh hoặc sửa câu SQL tại cổng này.
  * Khi LLM trả lời "không chắc chắn" (`unsure`), hệ thống ưu tiên giữ nguyên câu gốc hoặc từ chối sửa (`ABSTAIN`).
