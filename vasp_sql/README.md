# Package `vasp_sql/` — Mã nguồn Cốt lõi của Hệ thống VASP-SQL

Package `vasp_sql` chứa toàn bộ các module triển khai phương pháp sửa lỗi SQL cục bộ trên cây cú pháp trừu tượng có kiểm chứng (Verified AST-localized SQL Repair).

---

## Cấu trúc các module con

```text
vasp_sql/
├── core/           # Mô hình dữ liệu chuẩn (Schema, Feedback, Result, Policy, Config)
├── sandbox/        # Phòng thí nghiệm an toàn SQLite (Gate D & Gate E)
├── adapters/       # Bộ chuyển đổi I/O và nạp dữ liệu benchmark BIRD
├── ast/            # Phân tích cú pháp AST (sqlglot), Scope Analyzer, Joins Analyzer
├── schema_graph/   # Đồ thị tri thức CSDL (NetworkX) và thuật toán so khớp mờ
├── diagnosis/      # Động cơ chẩn đoán lỗi 5 tầng và lỗi giá trị lọc
├── repair/         # Bộ lập kế hoạch bản vá AST, các thao tác vá và sửa ngữ nghĩa
├── verifiers/      # Hệ thống 6 cổng kiểm chứng (Gate A → Gate F)
├── llm/            # Client kết nối LLM với bộ nhớ đệm trên đĩa (chạy lại không tốn thêm lượt gọi)
├── generators/     # Triển khai 3 phương pháp AI sinh SQL (G1, G2, G3)
├── pipeline.py     # Hàm repair() tích hợp toàn bộ quy trình đầu cuối
└── __init__.py     # Điểm xuất khẩu chính (xuất khẩu hàm repair)
```

---

## Chi tiết luồng xử lý chính (`pipeline.py`)

Hàm [`repair(...)`](file:///d:/Projects/VAST_SQL/vasp_sql/pipeline.py) hiện thực hóa Thuật toán 1 (Algorithm 1) từ đề xuất nghiên cứu:

1. **Kiểm tra ban đầu (Fast Path - Zero Intervention):**
   * Đưa câu SQL gốc qua Gate A (Cú pháp) và Gate B (Schema & Scope & Cartesian Check).
   * Nếu không phát hiện lỗi cấu trúc, đưa tiếp qua Gate D (An toàn) và Gate E (Chạy thử Sandbox).
   * Nếu câu SQL gốc chạy thành công và thỏa mãn Gate F, hệ thống trả về ngay trạng thái **`UNCHANGED`** (tuyệt đối không can thiệp vào câu đúng).
2. **Chẩn đoán lỗi (Diagnosis Phase):**
   * Nếu câu gốc thất bại ở bất kỳ cổng nào hoặc chạy ra 0 dòng kết quả bất thường:
   * Kích hoạt `DiagnosisEngine` (5 tầng) và `values.py` để lập báo cáo `DiagnosticReport`.
3. **Sinh & Ưu tiên ứng viên (Planning & Patching):**
   * `RepairPlanner` sử dụng các bản vá cục bộ (`ReplaceColumnPatch`, `ReplaceTablePatch`, `AddJoinPatch`, `ReplacePredicateValue`) kết hợp đồ thị tri thức để sinh ra danh sách `CandidateQuery`.
4. **Kiểm chứng đa tầng (Multi-Gate Verification):**
   * Từng câu ứng viên lần lượt được chạy qua toàn bộ chuỗi kiểm định Gate A $\to$ Gate B $\to$ Gate C $\to$ Gate D $\to$ Gate E $\to$ Gate F.
   * Ứng viên đầu tiên vượt qua 100% các cổng và đạt ngưỡng độ tin cậy được công nhận là **`REPAIRED`**.
5. **Từ chối sửa (Abstention):**
   * Nếu không có ứng viên nào vượt qua đủ các cổng kiểm chứng, hệ thống trả về **`ABSTAIN`** và giữ nguyên trạng thái an toàn, tránh sửa sai làm hỏng kết quả của người dùng.
