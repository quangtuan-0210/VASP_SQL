"""
Unit test cho Giai đoạn 2: adapter của các AI sinh SQL và bộ nạp dữ liệu BIRD.
"""

import os

import pytest
import yaml

from vasp_sql.adapters.models import EvaluationSample, GeneratorOutput
from vasp_sql.adapters.cleaner import extract_and_clean_sql
from vasp_sql.adapters.bird_loader import BirdDatasetLoader
from vasp_sql.adapters.single_agent_adapter import SingleAgentAdapter
from vasp_sql.adapters.mac_sql_adapter import MacSqlAdapter
from vasp_sql.adapters.langchain_adapter import LangChainAdapter


@pytest.fixture
def dummy_sample():
    return EvaluationSample(
        question_id=42,
        db_id="california_schools",
        question="What is the highest SAT score?",
        evidence="Use satscores table",
        golden_sql="SELECT MAX(AvgScrMath) FROM satscores;",
        difficulty="simple"
    )


def test_sql_cleaner_variations():
    """Kiểm tra tách SQL từ nhiều dạng câu trả lời của LLM."""
    # 1. Markdown có nhãn sql
    raw1 = "```sql\nSELECT * FROM schools WHERE county = 'Alameda';\n```"
    assert extract_and_clean_sql(raw1) == "SELECT * FROM schools WHERE county = 'Alameda';"

    # 2. Có câu dẫn hội thoại phía trước
    raw2 = "Here is the SQL query you requested:\nSELECT name FROM users WHERE age > 18;"
    assert extract_and_clean_sql(raw2) == "SELECT name FROM users WHERE age > 18;"

    # 3. Khối code không ghi ngôn ngữ, có comment ở cuối
    raw3 = "```\nSELECT dept_id, COUNT(*) FROM employees GROUP BY dept_id;\n```\nHope this helps!"
    assert extract_and_clean_sql(raw3) == "SELECT dept_id, COUNT(*) FROM employees GROUP BY dept_id;"

    # 4. Truy vấn CTE
    raw4 = "WITH cte AS (SELECT id FROM t) SELECT * FROM cte;"
    assert extract_and_clean_sql(raw4) == "WITH cte AS (SELECT id FROM t) SELECT * FROM cte;"

    # 5. Văn bản không hợp lệ -> trả về None
    assert extract_and_clean_sql("I cannot answer this question because schema is missing.") is None
    assert extract_and_clean_sql("") is None
    assert extract_and_clean_sql(None) is None


def test_bird_dataset_loader():
    """Kiểm tra bộ nạp BIRD trên dữ liệu thật có trên máy."""
    # Đường dẫn dữ liệu khác nhau giữa các máy: cho phép đổi file cấu hình qua biến VASP_BIRD_CONFIG
    cfg = os.environ.get("VASP_BIRD_CONFIG", "configs/bird_config.yaml")
    dev_json = yaml.safe_load(open(cfg, encoding="utf-8"))["benchmark"]["dev_json_path"]
    if not os.path.exists(dev_json):
        pytest.skip(f"không có dữ liệu BIRD tại {dev_json} (đặt VASP_BIRD_CONFIG trỏ tới file cấu hình đúng)")
    loader = BirdDatasetLoader(cfg)

    # Phải có 1534 mẫu
    assert len(loader) == 1534

    sample = loader.get_sample(0)
    assert sample.question_id is not None
    assert sample.db_id == "california_schools"
    assert len(sample.question) > 0

    # Kiểm tra lọc mẫu
    california_samples = loader.filter_samples(db_id="california_schools", limit=5)
    assert len(california_samples) == 5
    assert all(s.db_id == "california_schools" for s in california_samples)

    # Kiểm tra nạp schema
    schema = loader.get_schema("california_schools")
    assert schema.has_table("schools")
    assert schema.has_table("satscores")
    assert schema.has_table("frpm")


def test_single_agent_adapter(dummy_sample):
    """Kiểm tra chuẩn hoá đầu ra thô của single-agent."""
    adapter = SingleAgentAdapter()

    # Trường hợp thành công
    raw = "```sql\nSELECT MAX(AvgScrMath) FROM satscores;\n```"
    out = adapter.adapt(raw, dummy_sample, metadata={"latency_ms": 120.5})

    assert out.is_generation_successful
    assert out.cleaned_sql == "SELECT MAX(AvgScrMath) FROM satscores;"
    assert out.sample_id == 42
    assert out.generator_name == "baseline_single_agent"
    assert out.metadata["latency_ms"] == 120.5

    # Trường hợp thất bại (đầu ra rác)
    out_fail = adapter.adapt("Sorry, no SQL could be generated.", dummy_sample)
    assert not out_fail.is_generation_successful
    assert out_fail.cleaned_sql is None
    assert out_fail.generation_error is not None


def test_mac_sql_adapter(dummy_sample):
    """Kiểm tra chuẩn hoá đầu ra có cấu trúc của hệ nhiều agent MAC-SQL."""
    adapter = MacSqlAdapter()

    mac_output = {
        "final_sql": "SELECT MAX(AvgScrMath) FROM satscores;",
        "sub_goals": ["Identify math score column", "Calculate max"],
        "selected_tables": ["satscores"],
        "refinement_rounds": 1
    }

    out = adapter.adapt(mac_output, dummy_sample)

    assert out.is_generation_successful
    assert out.cleaned_sql == "SELECT MAX(AvgScrMath) FROM satscores;"
    assert out.metadata["sub_goals"] == ["Identify math score column", "Calculate max"]
    assert out.metadata["selected_tables"] == ["satscores"]
    assert out.metadata["refinement_rounds"] == 1


def test_langchain_adapter(dummy_sample):
    """Kiểm tra chuẩn hoá câu trả lời của LangChain SQL agent kèm các bước gọi công cụ."""
    adapter = LangChainAdapter()

    # Trường hợp 1: câu trả lời cuối có SQL
    langchain_output_1 = {
        "output": "The highest math score can be found using: SELECT MAX(AvgScrMath) FROM satscores;",
        "intermediate_steps": [
            ({"tool": "sql_db_schema", "tool_input": "satscores"}, "Table schema..."),
            ({"tool": "sql_db_query", "tool_input": "SELECT MAX(AvgScrMath) FROM satscores;"}, "[(800,)]")
        ]
    }

    out1 = adapter.adapt(langchain_output_1, dummy_sample)
    assert out1.is_generation_successful
    assert out1.cleaned_sql == "SELECT MAX(AvgScrMath) FROM satscores;"
    assert out1.metadata["intermediate_step_count"] == 2
    assert out1.metadata["tool_executed_queries_count"] == 1

    # Trường hợp 2: câu trả lời cuối chỉ là lời thoại, SQL nằm ở lần gọi công cụ cuối cùng
    langchain_output_2 = {
        "output": "The maximum score achieved is 800 based on the school database.",
        "intermediate_steps": [
            ({"tool": "sql_db_query", "tool_input": "SELECT MAX(AvgScrMath) FROM satscores;"}, "[(800,)]")
        ]
    }

    out2 = adapter.adapt(langchain_output_2, dummy_sample)
    assert out2.is_generation_successful
    assert out2.cleaned_sql == "SELECT MAX(AvgScrMath) FROM satscores;"
