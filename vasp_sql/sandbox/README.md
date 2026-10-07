# Thư mục `vasp_sql/sandbox/` — Phòng thí nghiệm thực thi an toàn

Thư mục này chịu trách nhiệm cách ly và chạy thử các câu lệnh SQL do AI sinh ra trước khi đưa vào sản xuất hoặc công nhận kết quả sửa lỗi. Phục vụ trực tiếp cho **Gate D (Safety)** và **Gate E (Execution)** trong bài báo.

---

## Chi tiết file và nhiệm vụ

### `executor.py` — `SQLiteSandboxExecutor`
* **Nhiệm vụ:** Bộ máy thực thi an toàn cho SQLite database.
* **4 Cơ chế bảo vệ cốt lõi:**
  1. **Gate D (Safety Check bằng AST):** Sử dụng thư viện `sqlglot` để soi cây cú pháp. Nếu phát hiện bất kỳ câu lệnh nào có thể sửa đổi hoặc phá hoại dữ liệu (`DROP`, `DELETE`, `UPDATE`, `INSERT`, `ALTER`, `CREATE`, `PRAGMA`), nó sẽ **chặn đứng 100%** và ném ra lỗi `safety_violation` ngay lập tức mà không bao giờ gửi vào database.
  2. **Read-only Connection:** Mở kết nối SQLite với URI `mode=ro` kèm theo lệnh `PRAGMA query_only = ON;`.
  3. **Hẹn giờ ngắt Timeout:** Sử dụng luồng nền `threading.Timer` kết hợp với lệnh ngắt luồng `conn.interrupt()` của SQLite. Nếu một câu truy vấn bị lặp vô tận hoặc ngốn CPU quá thời gian cho phép (mặc định 5s), Sandbox sẽ **tự động rút phích cắm ngắt ngay lập tức**, tránh làm treo máy tính.
  4. **Chống tràn RAM & Fingerprint (Gate E):** Giới hạn chỉ lấy tối đa 1.000 dòng kết quả đầu tiên, đồng thời tạo mã băm SHA-256 (`result_fingerprint`) của kết quả để phục vụ việc so sánh đối chiếu ngữ nghĩa (Semantic Regression Check).
