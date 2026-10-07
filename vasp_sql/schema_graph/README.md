# Thư mục `vasp_sql/schema_graph/` — Đồ thị tri thức CSDL (Knowledge Graph)

Thư mục này xây dựng và quản lý đồ thị tri thức cơ sở dữ liệu bằng thư viện `networkx`, phục vụ việc tự động tìm đường nối `JOIN` hợp lệ giữa các bảng và gợi ý sửa tên cột.

---

## Chi tiết file và nhiệm vụ

### `graph.py` — `SchemaKnowledgeGraph`

* **Cấu trúc Đồ thị:**
  * **Đồ thị bảng (`table_graph` - `MultiGraph`):** Các node là bảng, các cạnh là quan hệ khóa ngoại (Foreign Key) nối từ `table_a` sang `table_b`.
  * **Đồ thị đầy đủ (`full_graph`):** Chứa cả node bảng và node cột, thể hiện quan hệ sở hữu `has_column` và tham chiếu `references`.

* **Các chức năng cốt lõi:**
  1. **`build_from_schema(schema)`**: Tự động dựng đồ thị từ `SchemaMetadata`.
  2. **`find_join_path(table_a, table_b)`**: Sử dụng thuật toán đường đi ngắn nhất (Shortest Path) trên đồ thị khóa ngoại để tìm đường nối giữa 2 bảng bất kỳ.
     * Hỗ trợ cả **nối trực tiếp** (`employees -> departments`).
     * Hỗ trợ **nối bắc cầu nhiều chặng (Multi-hop)** (`tasks -> employees -> departments`).
  3. **`find_connecting_join_tree(required_tables)`**: Tự động tìm tập hợp các bước `JOIN` liên kết tất cả các bảng cần thiết lại với nhau (xấp xỉ cây Steiner tối thiểu).
  4. **`find_candidate_columns(fuzzy_col_name, table_name)`**: Gợi ý các tên cột đúng khi câu SQL của AI bị gõ sai tên cột (sử dụng độ đo tương đồng xâu ký tự).
