"""
Đọc file .env (không cần thư viện ngoài).

Mỗi dòng dạng KEY=VALUE trong file .env ở thư mục gốc project được nạp vào os.environ.
Biến đã có sẵn trong môi trường thì được ưu tiên hơn giá trị trong file.
"""

import os
from pathlib import Path

# Thư mục gốc project (VAST_SQL/)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_env(path=None, override: bool = False) -> dict:
    env_path = Path(path) if path else PROJECT_ROOT / ".env"
    loaded = {}
    if not env_path.exists():
        return loaded
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        # Bỏ qua dòng trống và dòng chú thích
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if override or key not in os.environ:
            os.environ[key] = value
        loaded[key] = value
    return loaded
