# Thư mục `vasp_sql/adapters/` — Bộ nạp dữ liệu BIRD & Chuẩn hóa Generator

Thư mục này hoạt động như một **"đầu cắm Type-C đa năng"** giúp hệ thống VASP-SQL kết nối với bộ dữ liệu benchmark và bất kỳ loại AI Agent nào mà không làm ảnh hưởng đến mã nguồn thuật toán sửa lỗi.

---

## Chi tiết các file và nhiệm vụ

### 1. `models.py`
* **`EvaluationSample`**: Lưu trữ 1 câu hỏi từ benchmark (`question_id`, `db_id`, `question`, `evidence`, `golden_sql`, `difficulty`).
* **`GeneratorOutput`**: Hợp đồng dữ liệu đầu ra chuẩn hóa từ mọi Agent:
  * `cleaned_sql`: Câu lệnh SQL đã được bóc tách sạch sẽ.
  * `is_generation_successful`: True nếu Agent tạo được SQL, False nếu bị crash/lỗi.
  * `metadata`: Lưu các chỉ số vận hành (số tokens, thời gian chạy, số bước suy luận).
  * `initial_execution_feedback`: Kết quả chạy thử ban đầu nếu Agent có thực thi.

---

### 2. `cleaner.py`
* **`extract_and_clean_sql(raw_text)`**: Bộ lọc thông minh giúp bóc tách câu SQL thuần túy từ văn bản phản hồi lộn xộn của LLM:
  * Bóc tách các khối mã Markdown: ` ```sql ... ``` ` hoặc ` ``` ... ``` `.
  * Cắt bỏ lời chào hoặc câu đàm thoại thừa của LLM (ví dụ: *"Here is your SQL query:"*, *"Output:"*).
  * Chuẩn hóa dấu chấm phẩy, khoảng trắng và comment.

---

### 3. `bird_loader.py`
* **`BirdDatasetLoader`**: Bộ nạp dữ liệu benchmark BIRD chính thức:
  * Đọc toàn bộ 1.534 câu hỏi từ `dev.json`.
  * Hỗ trợ lọc câu hỏi theo Database hoặc theo độ khó (`simple`, `moderate`, `challenging`).
  * Có sẵn phương thức `get_schema(db_id)` tự động nạp cấu trúc database SQLite tương ứng từ thư mục `dev_databases/`.

---

### 4. `base_adapter.py`
* **`BaseGeneratorAdapter`**: Lớp trừu tượng định nghĩa hàm `adapt(...)`. Cung cấp sẵn cơ chế fallback an toàn `_fallback_on_error(...)` để khi một Agent bị lỗi vỡ hạt nhân (crash), hệ thống vẫn chuyển thành đối tượng `GeneratorOutput` với `is_generation_successful=False` mà không làm dừng chương trình.

---

### 5. `single_agent_adapter.py`
* **`SingleAgentAdapter`**: Adapter cho Generator G1 (Mô hình prompt 1 lượt). Chuẩn hóa câu trả lời dạng chuỗi (string) hoặc dictionary thành `GeneratorOutput`.

---

### 6. `mac_sql_adapter.py`
* **`MacSqlAdapter`**: Adapter cho Generator G2 (Hệ thống Multi-Agent MAC-SQL). Bóc tách câu SQL cuối cùng và lưu vết toàn bộ các bài toán con (`sub_goals`), các bảng được chọn (`selected_tables`), và số vòng tinh chỉnh (`refinement_rounds`) vào metadata.

---

### 7. `langchain_adapter.py`
* **`LangChainAdapter`**: Adapter cho Generator G3 (LangChain SQL Tool Agent). Bóc tách các bước gọi tool (`intermediate_steps`), số lần truy vấn database, và nếu câu trả lời cuối là lời đàm thoại thì tự động trích xuất câu SQL từ lệnh gọi công cụ gần nhất.
