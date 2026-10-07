# VASP-SQL: Verified AST-localized SQL Repair

Hệ thống hậu xử lý độc lập giúp chẩn đoán, sửa lỗi cục bộ trên cây cú pháp trừu tượng (AST) và kiểm chứng đa tầng cho các câu lệnh SQL do AI Agent sinh ra.

---

## Cấu trúc thư mục dự án

```text
VAST_SQL/
├── configs/               # Cấu hình thực nghiệm (đường dẫn dữ liệu, thông số generator, ngân sách)
│   ├── bird_config.yaml
│   └── README.md
├── vasp_sql/              # Package mã nguồn chính
│   ├── core/              # Mô hình dữ liệu chuẩn (Schema, Feedback, Result, Policy)
│   ├── sandbox/           # Phòng thí nghiệm an toàn để chạy thử SQL (Gate D & E)
│   ├── adapters/          # Bộ nạp dữ liệu BIRD và chuyển đổi I/O cho các Generator
│   └── README.md
├── tests/                 # Bộ kiểm thử tự động (Unit tests)
│   ├── test_phase1.py     # Kiểm thử Giai đoạn 1 (Core models, Sandbox)
│   ├── test_phase2.py     # Kiểm thử Giai đoạn 2 (Cleaner, Loader, Adapters)
│   └── README.md
├── requirements.txt       # Danh sách thư viện phụ thuộc (sqlglot, networkx, pydantic, pytest)
├── VASP-SQL_Plan.docx     # Kế hoạch nghiên cứu chi tiết
└── README.md              # Tài liệu tổng quan dự án (file này)
```

---

## Hướng dẫn cài đặt và chạy thử

### 1. Cài đặt thư viện
```powershell
py -m pip install -r requirements.txt
```

### 2. Chạy toàn bộ bài test tự động
```powershell
py -m pytest -v
```
*(Hiện tại 11/11 bài test của Giai đoạn 1 và 2 đều đã vượt qua 100%)*
