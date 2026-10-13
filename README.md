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

## Kết quả hiện có (không phụ thuộc mô hình LLM)

| Thí nghiệm | Kết quả |
|---|---|
| SQL đúng (gold) của tập report, 1234 câu | 1232 giữ nguyên, 1 sửa (q646 — lỗi sẵn của gold), 1 từ chối (quá thời gian) |
| Lỗi nhân tạo mới (seed 2027, chỉ câu report), 900 câu | Sửa đúng **92,2%**, sửa sai 0,4%; cột 96,7% · bảng 100% · thiếu JOIN 80,0%; trung bình 2,8 nút AST thay đổi |
| Cùng bộ đó, cấu hình **AST-only** (không đồ thị schema, không sửa giá trị) | 65,6% — thiếu JOIN **0%** → đồ thị schema đóng góp rõ |

Kết quả trên SQL do 3 AI sinh (G1 / G2 / G3) cùng các đối chứng: chạy `scripts/stats_report.py` sau khi chạy xong các cấu hình.

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

## Cài đặt & chạy

```powershell
py -m pip install -r requirements.txt
copy .env.example .env      # điền VT_BASE_URL, VT_API_KEY, VT_MODEL_LLM (và VT_REASONING_EFFORT nếu cần)
```

`.env` (không đưa lên git):

| Biến | Ý nghĩa |
|---|---|
| `VT_BASE_URL` | Địa chỉ API chuẩn OpenAI (VD `https://openrouter.ai/api/v1`) |
| `VT_API_KEY` | Khóa API |
| `VT_MODEL_LLM` | Tên mô hình — dùng chung cho cả 3 AI, Gate F và đối chứng |
| `VT_REASONING_EFFORT` | (tuỳ chọn) với mô hình có chế độ suy nghĩ: `none` = tắt, `low`/`high`/`max` |

Kiểm thử (nếu dữ liệu BIRD ở chỗ khác, đặt `VASP_BIRD_CONFIG` trỏ tới file cấu hình đúng):

```powershell
py -m pytest -q
```

Chạy thí nghiệm (mọi script có `--resume` và `--max-seconds` để chạy từng đoạn, dừng giữa chừng không mất kết quả):

```powershell
# không cần LLM
py scripts/eval_gold.py --split report --out <thư mục kết quả gold>
py scripts/eval_corrupted.py --split report --seed 2027 --gold-results <thư mục kết quả gold>/per_sample.jsonl --out <thư mục kết quả>
py scripts/eval_corrupted.py ... --ast-only --out <thư mục kết quả AST-only>

# 3 AI sinh SQL (cần LLM)
py scripts/run_generator.py --gen g1 --split report --workers 8 --resume

# các cấu hình theo mục 5.6 (với từng AI)
py scripts/eval_generated.py --gen g1 --split report                    # VASP-SQL không LLM (A–E)
py scripts/eval_generated.py --gen g1 --split report --llm              # VASP-SQL đầy đủ (A–F)
py scripts/eval_generated.py --gen g1 --split report --ast-only         # AST-only
py scripts/eval_generated.py --gen g1 --split report --no-value-repair  # ablation
py scripts/eval_generated.py --gen g1 --split report --no-schema-graph  # ablation
py scripts/eval_baselines.py --gen g1 --split report --mode rewrite
py scripts/eval_baselines.py --gen g1 --split report --mode execfb

# thống kê cuối
py scripts/stats_report.py --split report --price-in 0.09 --price-out 0.55
```

## Quy tắc thực nghiệm

- Tập **tune** dùng để chỉnh phương pháp; tập **report** chỉ chạy khi code đã đóng băng (lưu mã băm của code trước khi chạy).
- Mọi câu trả lời LLM được lưu đệm để chạy lại không tốn thêm và kiểm tra lại được.
- Kết quả thực nghiệm chỉ lưu trên máy, không đưa lên git.
- Chú thích trong code viết bằng tiếng Việt; prompt gửi LLM giữ tiếng Anh.
