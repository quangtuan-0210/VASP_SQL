"""
SQL extraction and cleanup utilities for LLM responses.
"""

import re
from typing import Optional


def extract_and_clean_sql(raw_text: Optional[str]) -> Optional[str]:
    """
    Extract pure SQL statement from raw LLM responses.
    Handles markdown codeblocks, conversational text, comments, and whitespace.
    """
    if not raw_text or not raw_text.strip():
        return None

    text = raw_text.strip()

    # 1. Check for markdown codeblocks: ```sql ... ``` or ``` ... ```
    codeblock_pattern = re.compile(r"```(?:sql)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
    matches = codeblock_pattern.findall(text)
    if matches:
        # Take the match that contains a SELECT or WITH keyword
        for match in matches:
            if re.search(r"\b(SELECT|WITH)\b", match, re.IGNORECASE):
                text = match.strip()
                break
        else:
            text = matches[0].strip()

    # 2. Strip conversational prefixes (e.g. "SQL:", "Query:", "Here is the query:")
    prefix_patterns = [
        r"^(?:Here is the (?:SQL|query)[\s\S]*?:)\s*",
        r"^(?:The SQL query is:)\s*",
        r"^(?:SQL|Query|Output)\s*:\s*",
    ]
    for pattern in prefix_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()

    # 3. Locate starting statement keyword (SELECT or WITH) if conversational text precedes it
    start_match = re.search(r"\b(SELECT|WITH)\b", text, re.IGNORECASE)
    if start_match and start_match.start() > 0:
        text = text[start_match.start():]

    # 4. Remove trailing explanations if any (e.g. text after the terminating semicolon)
    # If there is a semicolon, take everything up to the semicolon
    semicolon_pos = text.find(";")
    if semicolon_pos != -1:
        text = text[:semicolon_pos + 1].strip()
    else:
        # If no semicolon, clean whitespace and append semicolon
        text = text.strip() + ";"

    # 5. Clean multi-line comments or weird trailing tokens
    lines = [line for line in text.splitlines() if line.strip()]
    cleaned_sql = "\n".join(lines).strip()

    if not cleaned_sql or not re.search(r"\b(SELECT|WITH)\b", cleaned_sql, re.IGNORECASE):
        return None

    return cleaned_sql
