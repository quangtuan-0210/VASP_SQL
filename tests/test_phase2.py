"""
Unit tests for Phase 2: Generator Adapters & BIRD Dataset Loader.
"""

import pytest

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
    """Test SQL extraction from various LLM response formats."""
    # 1. Markdown with sql tag
    raw1 = "```sql\nSELECT * FROM schools WHERE county = 'Alameda';\n```"
    assert extract_and_clean_sql(raw1) == "SELECT * FROM schools WHERE county = 'Alameda';"

    # 2. Conversational preface
    raw2 = "Here is the SQL query you requested:\nSELECT name FROM users WHERE age > 18;"
    assert extract_and_clean_sql(raw2) == "SELECT name FROM users WHERE age > 18;"

    # 3. Code fence without language tag and trailing comments
    raw3 = "```\nSELECT dept_id, COUNT(*) FROM employees GROUP BY dept_id;\n```\nHope this helps!"
    assert extract_and_clean_sql(raw3) == "SELECT dept_id, COUNT(*) FROM employees GROUP BY dept_id;"

    # 4. CTE query
    raw4 = "WITH cte AS (SELECT id FROM t) SELECT * FROM cte;"
    assert extract_and_clean_sql(raw4) == "WITH cte AS (SELECT id FROM t) SELECT * FROM cte;"

    # 5. Invalid text returning None
    assert extract_and_clean_sql("I cannot answer this question because schema is missing.") is None
    assert extract_and_clean_sql("") is None
    assert extract_and_clean_sql(None) is None


def test_bird_dataset_loader():
    """Test BIRD dataset loader on the real dataset present on disk."""
    loader = BirdDatasetLoader("configs/bird_config.yaml")

    # Should have 1534 samples
    assert len(loader) == 1534

    sample = loader.get_sample(0)
    assert sample.question_id is not None
    assert sample.db_id == "california_schools"
    assert len(sample.question) > 0

    # Test filtering
    california_samples = loader.filter_samples(db_id="california_schools", limit=5)
    assert len(california_samples) == 5
    assert all(s.db_id == "california_schools" for s in california_samples)

    # Test loading schema
    schema = loader.get_schema("california_schools")
    assert schema.has_table("schools")
    assert schema.has_table("satscores")
    assert schema.has_table("frpm")


def test_single_agent_adapter(dummy_sample):
    """Test adapting raw single-agent prompt output."""
    adapter = SingleAgentAdapter()

    # Success case
    raw = "```sql\nSELECT MAX(AvgScrMath) FROM satscores;\n```"
    out = adapter.adapt(raw, dummy_sample, metadata={"latency_ms": 120.5})

    assert out.is_generation_successful
    assert out.cleaned_sql == "SELECT MAX(AvgScrMath) FROM satscores;"
    assert out.sample_id == 42
    assert out.generator_name == "baseline_single_agent"
    assert out.metadata["latency_ms"] == 120.5

    # Failure case (garbage output)
    out_fail = adapter.adapt("Sorry, no SQL could be generated.", dummy_sample)
    assert not out_fail.is_generation_successful
    assert out_fail.cleaned_sql is None
    assert out_fail.generation_error is not None


def test_mac_sql_adapter(dummy_sample):
    """Test adapting MAC-SQL multi-agent structured output."""
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
    """Test adapting LangChain SQL agent response with intermediate tool steps."""
    adapter = LangChainAdapter()

    # Case 1: Final answer has SQL
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

    # Case 2: Final answer is just conversational text, SQL is in the last tool call
    langchain_output_2 = {
        "output": "The maximum score achieved is 800 based on the school database.",
        "intermediate_steps": [
            ({"tool": "sql_db_query", "tool_input": "SELECT MAX(AvgScrMath) FROM satscores;"}, "[(800,)]")
        ]
    }

    out2 = adapter.adapt(langchain_output_2, dummy_sample)
    assert out2.is_generation_successful
    assert out2.cleaned_sql == "SELECT MAX(AvgScrMath) FROM satscores;"
