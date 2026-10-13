# Thư mục `tests/` — Bộ Kiểm thử Tự động (Unit & Regression Tests)

Thư mục này chứa toàn bộ các bài kiểm thử tự động bằng framework `pytest`, đóng vai trò là "hàng rào bảo hiểm" đảm bảo code của hệ thống luôn hoạt động ổn định và chính xác (Hiện tại **60/60 test qua**; nếu dữ liệu BIRD không ở `D:/Projects/...`, đặt biến `VASP_BIRD_CONFIG` trỏ tới file cấu hình đúng).

---

## Hướng dẫn chạy kiểm thử

Mở terminal tại thư mục gốc của dự án (`d:\Projects\VAST_SQL`) và chạy lệnh:

```powershell
py -m pytest -v
```

Để chạy riêng một file test cụ thể:
```powershell
py -m pytest -v tests/test_phase1.py
py -m pytest -v tests/test_phase3_2.py
py -m pytest -v tests/test_phase3_7.py
```

---

## Chi tiết các file test và nhiệm vụ

### 1. [`test_phase1.py`](file:///d:/Projects/VAST_SQL/tests/test_phase1.py) (6 bài test) — Core Models & Sandbox
* `test_core_models_instantiation`: Khởi tạo và xác thực các model dữ liệu `ColumnMetadata`, `TableMetadata`, `SchemaMetadata`, `RepairResult`.
* `test_schema_introspection_from_sqlite`: Kiểm tra hàm tự động nạp cấu trúc database từ file SQLite (đọc đúng tên bảng, kiểu dữ liệu, khóa chính và khóa ngoại).
* `test_sandbox_safe_select`: Chạy `SELECT` hợp lệ trong Sandbox, kiểm tra số dòng kết quả và mã băm SHA-256 fingerprint.
* `test_sandbox_blocks_ddl_and_dml`: **Gate D**: Sandbox chặn đứng 100% các câu lệnh nguy hiểm (`DROP TABLE`, `DELETE`, `UPDATE`, `INSERT`).
* `test_sandbox_runtime_error_reporting`: Phân loại chính xác lỗi database (`no_such_column`, `no_such_table`).
* `test_sandbox_timeout`: **Cơ chế ngắt query treo**: Chạy vòng lặp đệ quy vô hạn và xác nhận Sandbox tự ngắt câu lệnh sau thời gian timeout.

---

### 2. [`test_phase2.py`](file:///d:/Projects/VAST_SQL/tests/test_phase2.py) (5 bài test) — Cleaner & Adapters
* `test_sql_cleaner_variations`: Bộ làm sạch SQL bóc tách chính xác từ khối markdown (```sql ... ```), câu đàm thoại thừa, CTE query, và từ chối chuỗi rác.
* `test_bird_dataset_loader`: Nạp thành công 1.534 câu hỏi từ file `dev.json` thực tế trên máy, lọc câu hỏi và nạp schema thật của database.
* `test_single_agent_adapter`: Chuẩn hóa kết quả từ Agent G1 (cả trường hợp sinh đúng và trường hợp bị lỗi).
* `test_mac_sql_adapter`: Bóc tách SQL và metadata phân rã bài toán từ Agent G2 (MAC-SQL).
* `test_langchain_adapter`: Trích xuất các bước gọi tool (`intermediate_steps`) và câu query từ Agent G3 (LangChain).

---

### 3. [`test_phase3_1.py`](file:///d:/Projects/VAST_SQL/tests/test_phase3_1.py) (5 bài test) — AST Parser & Scope & Schema Graph
* `test_sql_parser_operations`: Thao tác parse, to_sql, tính toán khoảng cách chỉnh sửa AST edit size.
* `test_scope_analyzer_alias_and_columns`: Nhận diện bí danh bảng, phân giải liên kết cột - bảng.
* `test_scope_analyzer_cte_support`: Hỗ trợ mệnh đề `WITH ... AS (...)` CTE.
* `test_schema_knowledge_graph_joins`: Tìm đường nối JOIN trực tiếp và bắc cầu nhiều chặng (Multi-hop) trên đồ thị khóa ngoại NetworkX.
* `test_schema_knowledge_graph_candidate_columns`: Gợi ý tên cột gần đúng khi bị gõ sai bằng độ đo tương đồng xâu ký tự.

---

### 4. [`test_phase3_2.py`](file:///d:/Projects/VAST_SQL/tests/test_phase3_2.py) (4 bài test) — Pipeline cơ bản & Vá lỗi
* `test_pipeline_unchanged_on_valid_query`: Câu SQL đúng sẵn được giữ nguyên trạng thái `UNCHANGED`.
* `test_pipeline_repairs_misspelled_column`: Tự động vá câu bị gõ sai tên cột thành `REPAIRED`.
* `test_pipeline_repairs_missing_join`: Tự động chèn `INNER JOIN ... ON ...` cho câu bị thiếu điều kiện nối (Cartesian product).
* `test_pipeline_abstains_on_irrecoverable_query`: Trả về `ABSTAIN` khi gặp câu lỗi không thể phục hồi.

---

### 5. [`test_phase3_3.py`](file:///d:/Projects/VAST_SQL/tests/test_phase3_3.py) (8 bài test) — Bản vá hiểu Alias & Scoped Joins
* Kiểm tra bản vá đổi tên cột và thêm JOIN sử dụng đúng tên tắt (`T1`, `T2`) đang có trong câu.
* Kiểm tra nối bảng theo từng khối SELECT riêng biệt (không gộp subquery bên trong với query bên ngoài).
* Hỗ trợ tên cột tự đặt (`COUNT(*) AS post_count`, cột của subquery / CTE).
* Sửa lỗi đảo cột khi đi ngược chiều khóa ngoại trong đồ thị schema.

---

### 6. [`test_phase3_4.py`](file:///d:/Projects/VAST_SQL/tests/test_phase3_4.py) (7 bài test) — So khớp Mờ & Chống Đoán Mò
* `test_matching_osa_distance`: Tính khoảng cách sửa đổi OSA (nhận diện lỗi đảo 2 chữ cái liền kề `ABL` $\to$ `ALB`).
* `test_matching_best_unique_match_ambiguous`: Từ chối đoán khi 2 ứng viên có điểm số ngang nhau (`A1` $\to$ `A10` hay `A11`).
* `test_pipeline_abstains_on_ambiguous_join`: Từ chối sửa khi giữa 2 bảng có nhiều khóa ngoại cho kết quả dữ liệu khác nhau.
* `test_pipeline_repairs_misspelled_table`: Tự động sửa tên bảng gõ sai mà bảo toàn alias và tiền tố bảng.

---

### 7. [`test_phase3_5.py`](file:///d:/Projects/VAST_SQL/tests/test_phase3_5.py) (7 bài test) — Kiểm chứng Gate F (Intent Verification)
* Kiểm tra cơ chế Gate F bằng Fake LLM độc lập (không cần mạng):
  * `check`: LLM phê duyệt bản sửa khớp ý câu hỏi hoặc từ chối bản sửa sai lệch.
  * `choose`: LLM lựa chọn đường nối bảng đúng khi có nhiều khóa ngoại (ví dụ: nối qua màu mắt hay màu tóc).
  * Xử lý an toàn khi LLM trả về không đọc được hoặc `unsure` $\to$ hệ thống giữ nguyên câu gốc.

---

### 8. [`test_phase3_6.py`](file:///d:/Projects/VAST_SQL/tests/test_phase3_6.py) (7 bài test) — Bản vá Giá trị lọc WHERE (0 dòng)
* Kiểm tra `ReplacePredicateValuePatch`:
  * Sửa lỗi lệch số `0` ở đầu chuỗi (ví dụ: `CharterNum = '40'` $\to$ `'0040'`).
  * Sửa lỗi lệch chữ hoa / chữ thường (ví dụ: `'brown'` $\to$ `'Brown'`).
  * Sửa lỗi khoảng trắng và định dạng ngày tháng (`'2019-8-20'` $\to$ `'2019-08-20'`).
  * Từ chối sửa khi có từ 2 giá trị ứng viên tương đồng trong cột CSDL.

---

### 9. [`test_phase3_7.py`](file:///d:/Projects/VAST_SQL/tests/test_phase3_7.py) (6 bài test) — Hướng 2 Sửa lỗi Ngữ nghĩa Cục bộ
* Kiểm tra quy trình 2 pha bằng Fake LLM:
  * Pha 1: LLM đề xuất thao tác cục bộ (`replace_expression`, `add_condition`, `remove_condition`, `set_limit`, `set_distinct`).
  * Pha 2: Giám khảo độc lập đối chiếu kết quả thực thi và phê duyệt bản sửa.
  * Bảo đảm không làm hỏng câu đúng và chặn các thao tác sửa toàn câu.

---

### 10. `test_report_tools.py` (5 bài test) — Công cụ báo cáo
* Chỉ số "số nút AST thay đổi" đếm cả đổi tên (đổi 1 tên = 1 nút); chỉ số ngân sách cũ giữ nguyên hành vi.
* Công thức McNemar chính xác (VD 5 câu sửa đúng, 0 câu hỏng → p = 0,0625).
* LLMClient: tắt chế độ suy nghĩ gửi `thinking: disabled`; đếm token theo từng câu (kể cả lượt lấy từ bộ nhớ đệm);
  không lấy phần nháp của mô hình suy nghĩ làm câu trả lời.
