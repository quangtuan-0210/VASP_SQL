"""
Các hàm tách và làm sạch SQL từ câu trả lời của LLM.
"""

import re
from typing import Optional


def extract_and_clean_sql(raw_text: Optional[str]) -> Optional[str]:
    """
    Tách câu SQL thuần từ câu trả lời thô của LLM.
    Xử lý khối code markdown, lời thoại, comment và khoảng trắng.
    """
    if not raw_text or not raw_text.strip():
        return None

    text = raw_text.strip()

    # 1. Tìm khối code markdown: ```sql ... ``` hoặc ``` ... ```
    codeblock_pattern = re.compile(r"```(?:sql)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
    matches = codeblock_pattern.findall(text)
    if matches:
        # Lấy khối có chứa từ khoá SELECT hoặc WITH
        for match in matches:
            if re.search(r"\b(SELECT|WITH)\b", match, re.IGNORECASE):
                text = match.strip()
                break
        else:
            text = matches[0].strip()

    # 2. Bỏ câu dẫn hội thoại ở đầu (VD "SQL:", "Query:", "Here is the query:")
    prefix_patterns = [
        r"^(?:Here is the (?:SQL|query)[\s\S]*?:)\s*",
        r"^(?:The SQL query is:)\s*",
        r"^(?:SQL|Query|Output)\s*:\s*",
    ]
    for pattern in prefix_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()

    # 3. Tìm từ khoá bắt đầu câu lệnh (SELECT hoặc WITH) nếu phía trước có lời thoại
    start_match = re.search(r"\b(SELECT|WITH)\b", text, re.IGNORECASE)
    if start_match and start_match.start() > 0:
        text = text[start_match.start():]

    # 4. Bỏ phần giải thích ở cuối nếu có (VD chữ phía sau dấu chấm phẩy kết thúc)
    # Nếu có dấu chấm phẩy, lấy mọi thứ tới dấu chấm phẩy
    semicolon_pos = text.find(";")
    if semicolon_pos != -1:
        text = text[:semicolon_pos + 1].strip()
    else:
        # Nếu không có dấu chấm phẩy, dọn khoảng trắng và thêm dấu chấm phẩy
        text = text.strip() + ";"

    # 5. Dọn comment nhiều dòng hoặc ký tự lạ ở cuối
    lines = [line for line in text.splitlines() if line.strip()]
    cleaned_sql = "\n".join(lines).strip()

    if not cleaned_sql or not re.search(r"\b(SELECT|WITH)\b", cleaned_sql, re.IGNORECASE):
        return None

    return cleaned_sql
