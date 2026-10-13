# Thư mục `vasp_sql/schema_graph/` — Đồ thị tri thức CSDL & Thuật toán So khớp Mờ

Thư mục này xây dựng và quản lý đồ thị tri thức cơ sở dữ liệu bằng thư viện `networkx`, phục vụ việc tự động tìm đường nối `JOIN` hợp lệ giữa các bảng và cung cấp thuật toán so khớp tên bảng/cột bị gõ sai.

---

## Chi tiết các file và nhiệm vụ

### 1. `graph.py` — `SchemaKnowledgeGraph`

* **Cấu trúc Đồ thị:**
  * **Đồ thị bảng (`table_graph` - `MultiGraph`):** Các node là bảng, các cạnh là quan hệ khóa ngoại (Foreign Key) nối từ `table_a` sang `table_b`.
  * **Đồ thị đầy đủ (`full_graph`):** Chứa cả node bảng và node cột, thể hiện quan hệ sở hữu `has_column` và tham chiếu `references`.

* **Các chức năng cốt lõi:**
  1. **`build_from_schema(schema)`**: Tự động dựng đồ thị từ `SchemaMetadata`.
  2. **`find_join_path(table_a, table_b)`**: Sử dụng thuật toán đường đi ngắn nhất (Shortest Path) trên đồ thị khóa ngoại để tìm đường nối giữa 2 bảng bất kỳ.
     * Hỗ trợ cả **nối trực tiếp** (`employees -> departments`).
     * Hỗ trợ **nối bắc cầu nhiều chặng (Multi-hop)** (`tasks -> employees -> departments`).
  3. **`find_connecting_join_tree(required_tables)`**: Tự động tìm tập hợp các bước `JOIN` liên kết tất cả các bảng cần thiết lại với nhau (xấp xỉ cây Steiner tối thiểu).
  4. **`find_candidate_columns(fuzzy_col_name, table_name)`**: Gợi ý các tên cột đúng khi câu SQL của AI bị gõ sai tên cột.

---

### 2. `matching.py` — So khớp Mờ Tên Bảng & Cột (Fuzzy Name Matching)

* **Nhiệm vụ:** Tìm kiếm tên bảng hoặc tên cột chuẩn xác khi AI gõ sai chính tả, bảo đảm không đoán mò khi xảy ra mơ hồ.
* **Cơ chế xếp hạng 2 tín hiệu:**
  1. **Khoảng cách OSA (Optimal String Alignment / Damerau-Levenshtein):** Tính các thao tác thêm, xóa, thay thế và **đặc biệt là đảo 2 ký tự liền kề** (ví dụ: `ABL` $\to$ `ALB` chỉ tốn 1 bước đảo, trong khi đổi sang `ALP` tốn 2 bước).
  2. **Độ tương đồng SequenceMatcher:** Phân xử thứ hạng dự phòng cho các tên dài.
* **Cơ chế chống đoán mò (`best_unique_match`):**
  * Chỉ chấp nhận ứng viên tốt nhất khi nó **vượt trội rõ rệt** so với ứng viên thứ hai.
  * Nếu 2 ứng viên có điểm số ngang nhau (ví dụ: `A1` cách cả `A10` lẫn `A11` đúng 1 bước), thuật toán sẽ gắn cờ mơ hồ (ambiguous) để hệ thống từ chối sửa (`ABSTAIN`), triệt tiêu hoàn toàn lỗi sửa sai.
