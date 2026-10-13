# VASP-SQL: Verified AST-localized SQL Repair

Lớp hậu xử lý độc lập cho SQL do AI (Text-to-SQL agent) sinh ra: **chẩn đoán** lỗi, **sửa cục bộ trên cây cú pháp (AST)**
và **kiểm chứng nhiều tầng (Gate A → F)** trước khi chấp nhận bản sửa. Đánh giá trên benchmark **BIRD** (SQLite),
theo kế hoạch `VASP-SQL_Plan.docx`.

---

## Ý tưởng chính

1. **Sửa cục bộ:** chỉ thay đúng chỗ lỗi trên AST — đổi tên cột/bảng, thêm điều kiện JOIN theo khóa ngoại,
   sửa giá trị lọc theo giá trị thật trong database — không viết lại cả câu.
2. **Không phá câu đúng:** câu đã đúng được giữ nguyên (`UNCHANGED`).
3. **Từ chối khi không chắc (`ABSTAIN`):** nhiều cách sửa ngang nhau mà cho kết quả khác nhau, hoặc thiếu bằng chứng → không sửa.
4. **6 cổng kiểm chứng:** A cú pháp · B schema/phạm vi/JOIN đầy đủ · C kiểu dữ liệu · D an toàn · E chạy thử trong sandbox
   chỉ-đọc · F ý định câu hỏi (LLM chỉ chấm phần vừa sửa).
5. **Đồ thị schema (khóa ngoại)** để tìm cách nối bảng; so tên gần đúng bằng khoảng cách chỉnh sửa (không dùng embedding).

---

## Cấu trúc thư mục

```text
VAST_SQL/
├── VASP-SQL_Plan.docx      # Kế hoạch nghiên cứu (7 giai đoạn, cổng A–F, chỉ số)
├── configs/
│   ├── bird_config.yaml    # Đường dẫn BIRD, an toàn sandbox, ngân sách sửa
│   └── splits/bird_dev_split.json   # tune 300 câu / report 1234 câu (phân tầng db × độ khó, seed 2026)
├── vasp_sql/               # Mã nguồn
│   ├── core/               # Mô hình dữ liệu, chính sách sửa (RepairPolicy), đọc .env
│   ├── sandbox/            # Chạy SQL chỉ-đọc, giới hạn thời gian (Gate D, E)
│   ├── ast/                # Parse (sqlglot), phạm vi/bí danh, phân tích JOIN theo từng SELECT
│   ├── schema_graph/       # Đồ thị khóa ngoại (networkx), so tên gần đúng
│   ├── diagnosis/          # Chẩn đoán nhiều tầng + lỗi giá trị lọc
│   ├── repair/             # Bản vá AST, lập phương án; semantic.py = hướng sửa ý nghĩa bằng LLM (phần mở rộng)
│   ├── verifiers/          # Gate A–E; intent.py = Gate F bằng LLM
│   ├── llm/                # Client chuẩn OpenAI: bộ nhớ đệm, đếm token theo câu, bật/tắt chế độ suy nghĩ
│   ├── generators/         # G1 một lần gọi · G2 MAC-SQL · G3 LangChain agent
│   ├── adapters/           # Nạp BIRD, chuẩn hoá đầu ra của các AI
│   └── pipeline.py         # repair(): chẩn đoán → bản vá → kiểm chứng → chấp nhận / từ chối
├── scripts/
│   ├── make_splits.py      # Tạo phần tune / report
│   ├── run_generator.py    # Cho G1/G2/G3 sinh SQL
│   ├── eval_gold.py        # Kiểm tra trên SQL đúng (không phá câu đúng)
│   ├── eval_corrupted.py   # Lỗi nhân tạo có kiểm soát (theo loại lỗi)
│   ├── eval_generated.py   # EX trước/sau sửa trên SQL do AI sinh (+ AST-only, ablation, Gate F)
│   ├── eval_baselines.py   # Đối chứng: LLM rewrite, execution-feedback repair (+ bản có giám khảo)
│   ├── eval_combined.py    # Phần mở rộng: ghép VASP-SQL → execfb_verify
│   ├── stats_report.py     # Thống kê cuối: CI 95% bootstrap, McNemar, theo độ khó/db/loại lỗi, chi phí
│   └── test_llm_connection.py
├── tests/                  # 60 test tự động
└── third_party/MAC-SQL/    # Mã MAC-SQL (không có LICENSE — chỉ dùng nội bộ, không phân phối lại)
```

---

## Hướng dẫn chạy

Các lệnh dưới đây chạy trong thư mục gốc của project (Windows dùng `py`, Linux/macOS dùng `python`).

### Bước 1 — Cài thư viện

```powershell
py -m pip install -r requirements.txt
```

### Bước 2 — Chuẩn bị dữ liệu BIRD

Tải bộ **BIRD dev** về máy, rồi mở `configs/bird_config.yaml` và sửa các đường dẫn
(`dev_json_path`, `dev_tables_path`, `databases_dir`) cho đúng chỗ đã lưu.

Nếu muốn chạy G2 (MAC-SQL): đặt mã MAC-SQL vào `third_party/MAC-SQL`
(hoặc đặt biến môi trường `VT_MACSQL_DIR` trỏ tới thư mục đó).

### Bước 3 — Khai báo mô hình LLM

Chép file mẫu rồi điền thông tin:

```powershell
copy .env.example .env
```

| Biến trong `.env` | Điền gì |
|---|---|
| `VT_BASE_URL` | Địa chỉ API theo chuẩn OpenAI, VD `https://openrouter.ai/api/v1` |
| `VT_API_KEY` | Khóa API |
| `VT_MODEL_LLM` | Tên mô hình — dùng chung cho cả 3 AI, Gate F và các đối chứng |
| `VT_REASONING_EFFORT` | Không bắt buộc. Với mô hình có chế độ "suy nghĩ": `none` = tắt, `low` / `high` / `max` = mức suy nghĩ |

File `.env` chứa khóa bí mật, đã được loại khỏi git.

### Bước 4 — Kiểm tra code chạy đúng

```powershell
py -m pytest -q
```

Nếu dữ liệu BIRD không nằm đúng chỗ ghi trong `configs/bird_config.yaml`, đặt biến `VASP_BIRD_CONFIG`
trỏ tới file cấu hình của máy mình; bài test cần dữ liệu sẽ tự bỏ qua nếu không tìm thấy dữ liệu.

### Bước 5 — Thí nghiệm không cần LLM

Kiểm tra lớp sửa lỗi trên SQL đúng và trên SQL bị làm hỏng có chủ đích. Không tốn tiền, không cần `.env`.

```powershell
# 5a. Chạy trên SQL đúng của BIRD -> lớp sửa lỗi phải giữ nguyên, không sửa nhầm
py scripts/eval_gold.py --split report

# 5b. Làm hỏng SQL đúng (sai tên cột, sai tên bảng, mất điều kiện JOIN) rồi xem sửa lại được bao nhiêu
#     (dùng kết quả của bước 5a để chỉ lấy những câu lớp sửa lỗi đã giữ nguyên)
py scripts/eval_corrupted.py --split report --seed 2027

# 5c. Như 5b nhưng tắt đồ thị schema (cấu hình đối chứng AST-only), lưu sang thư mục khác để so sánh
py scripts/eval_corrupted.py --split report --seed 2027 --ast-only --out <thư mục khác>
```

### Bước 6 — Cho 3 AI sinh SQL (cần LLM)

```powershell
py scripts/run_generator.py --gen g1 --split report
py scripts/run_generator.py --gen g2 --split report
py scripts/run_generator.py --gen g3 --split report
```

- `g1` = một lần gọi LLM, `g2` = MAC-SQL, `g3` = agent LangChain.
- `--split report` = 1234 câu dùng để báo cáo; `--split tune` = 300 câu dùng để chỉnh phương pháp.
- Thêm `--workers 8` để gửi nhiều câu cùng lúc cho nhanh (nếu server cho phép).

### Bước 7 — Chạy lớp sửa lỗi và các phương pháp so sánh

Chạy lần lượt cho từng AI — thay `g1` bằng `g2`, `g3`:

```powershell
# Phương pháp đề xuất
py scripts/eval_generated.py --gen g1 --split report                    # VASP-SQL không dùng LLM (Gate A–E)
py scripts/eval_generated.py --gen g1 --split report --llm              # VASP-SQL đầy đủ (thêm Gate F bằng LLM)

# Đối chứng (mục 5.6 của kế hoạch)
py scripts/eval_generated.py --gen g1 --split report --ast-only         # AST-only: không đồ thị schema, không sửa giá trị
py scripts/eval_baselines.py --gen g1 --split report --mode rewrite     # LLM viết lại cả câu
py scripts/eval_baselines.py --gen g1 --split report --mode execfb      # LLM viết lại khi được xem kết quả chạy

# Ablation: tắt từng phần để xem phần đó đóng góp bao nhiêu
py scripts/eval_generated.py --gen g1 --split report --no-value-repair  # tắt sửa giá trị lọc
py scripts/eval_generated.py --gen g1 --split report --no-schema-graph  # tắt đồ thị schema
```

### Bước 8 — Tổng hợp thống kê

```powershell
py scripts/stats_report.py --split report
```

Gom kết quả của mọi cấu hình thành một bảng: EX, khoảng tin cậy 95%, kiểm định McNemar, kết quả theo độ khó,
database và loại lỗi, số nút AST thay đổi, chi phí và độ trễ. Muốn ước tính chi phí theo USD thì thêm giá token
của mô hình đang dùng: `--price-in <giá 1 triệu token vào> --price-out <giá 1 triệu token ra>`.

### Mẹo khi chạy lâu

- Các script ở bước 5–7 đều có `--resume`: bị ngắt giữa chừng thì chạy lại cùng lệnh kèm `--resume` để làm tiếp, không mất phần đã xong.
- `--max-seconds N`: tự dừng sau N giây (dùng cùng `--resume` để chạy từng đoạn).
- Câu trả lời của LLM được lưu đệm: chạy lại cùng câu không tốn thêm lượt gọi.

## Quy tắc thực nghiệm

- Tập **tune** dùng để chỉnh phương pháp; tập **report** chỉ chạy khi code đã đóng băng (lưu mã băm của code trước khi chạy).
- Mọi câu trả lời LLM được lưu đệm để chạy lại không tốn thêm và kiểm tra lại được.
- Kết quả thực nghiệm chỉ lưu trên máy, không đưa lên git.
- Chú thích trong code viết bằng tiếng Việt; prompt gửi LLM giữ tiếng Anh.
