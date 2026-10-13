# Thư mục `vasp_sql/generators/` — Triển khai 3 Mô hình AI Sinh SQL (Text-to-SQL Generators)

Thư mục này chứa mã nguồn triển khai và tích hợp 3 phương pháp AI sinh SQL đại diện được sử dụng làm đầu vào thực nghiệm cho hệ thống VASP-SQL.

---

## Chi tiết các file và nhiệm vụ

### 1. `schema_prompt.py`
* **Nhiệm vụ:** Chuẩn bị ngữ cảnh cấu trúc cơ sở dữ liệu (schema representation) đưa vào prompt cho LLM.
* **Chức năng:** Trích xuất các bảng, danh sách cột, khóa chính, khóa ngoại và các dòng dữ liệu mẫu đại diện để cung cấp đầy đủ thông tin nhất cho LLM sinh SQL.

---

### 2. `single_agent.py` — Generator G1 (Baseline Single Agent)
* **Nhiệm vụ:** Mô hình sinh SQL một lượt (Zero-shot / Few-shot direct generation).
* **Đặc điểm:** Nhận câu hỏi tự nhiên kèm schema và evidence, gọi LLM sinh trực tiếp câu SQL trong 1 lượt (Single turn). Đây là baseline phổ biến nhất trong các hệ thống Text-to-SQL cơ bản.

---

### 3. `mac_sql.py` — Generator G2 (Multi-Agent Collaboration SQL)
* **Nhiệm vụ:** Tái hiện kiến trúc đa tác tử (Multi-Agent) theo bài báo MAC-SQL.
* **Cấu trúc tác tử:**
  * **Selector Agent:** Phân tích câu hỏi và lọc ra tập hợp con các bảng/cột liên quan nhất nhằm giảm kích thước context.
  * **Decomposer / Generator Agent:** Chia nhỏ bài toán phức tạp thành các mục tiêu con và sinh câu lệnh SQL tương ứng.
  * **Refiner Agent:** Chạy thử SQL và thực hiện tinh chỉnh câu lệnh khi gặp lỗi thực thi.

---

### 4. `langchain_agent.py` — Generator G3 (LangChain ReAct SQL Tool Agent)
* **Nhiệm vụ:** Tác tử sinh SQL dạng vòng lặp tương tác công cụ (ReAct Loop).
* **Đặc điểm:** Agent được trang bị các công cụ: `sql_db_list_tables`, `sql_db_schema`, `sql_db_query`, `sql_db_query_checker`. Agent tự suy nghĩ, gọi công cụ kiểm tra CSDL nhiều vòng trước khi đưa ra câu SQL cuối cùng.
