"""
Unit tests for Phase 3.1: Scope-aware AST & Schema Knowledge Graph.
"""

import pytest

from vasp_sql.ast.parser import SQLParser
from vasp_sql.ast.scope import ScopeAnalyzer
from vasp_sql.core.schema import (
    ColumnMetadata,
    ForeignKeyRelation,
    SchemaMetadata,
    TableMetadata,
)
from vasp_sql.schema_graph.graph import SchemaKnowledgeGraph


@pytest.fixture
def sample_company_schema():
    """Build synthetic company schema: employees -> departments, projects -> departments."""
    schema = SchemaMetadata(db_id="corp")

    # Departments
    dept_tbl = TableMetadata(name="departments")
    dept_tbl.add_column(ColumnMetadata(name="dept_id", data_type="INTEGER", is_primary_key=True))
    dept_tbl.add_column(ColumnMetadata(name="dept_name", data_type="TEXT"))
    schema.add_table(dept_tbl)

    # Employees
    emp_tbl = TableMetadata(name="employees")
    emp_tbl.add_column(ColumnMetadata(name="emp_id", data_type="INTEGER", is_primary_key=True))
    emp_tbl.add_column(ColumnMetadata(name="emp_name", data_type="TEXT"))
    emp_tbl.add_column(ColumnMetadata(name="dept_id", data_type="INTEGER"))
    schema.add_table(emp_tbl)

    # Tasks (multi-hop: tasks -> employees -> departments)
    task_tbl = TableMetadata(name="tasks")
    task_tbl.add_column(ColumnMetadata(name="task_id", data_type="INTEGER", is_primary_key=True))
    task_tbl.add_column(ColumnMetadata(name="title", data_type="TEXT"))
    task_tbl.add_column(ColumnMetadata(name="emp_id", data_type="INTEGER"))
    schema.add_table(task_tbl)

    schema.foreign_keys.append(
        ForeignKeyRelation(from_table="employees", from_column="dept_id", to_table="departments", to_column="dept_id")
    )
    schema.foreign_keys.append(
        ForeignKeyRelation(from_table="tasks", from_column="emp_id", to_table="employees", to_column="emp_id")
    )

    return schema


def test_sql_parser_operations():
    """Test SQLParser parsing, serialization, and syntax validation."""
    parser = SQLParser(default_dialect="sqlite")

    sql = "SELECT name, salary FROM employees WHERE dept_id = 1;"
    ast = parser.parse(sql)
    assert ast is not None

    reconstructed = parser.to_sql(ast)
    assert "SELECT" in reconstructed.upper()
    assert "employees" in reconstructed.lower()

    # Syntax validation (Valid)
    feedback_valid = parser.validate_syntax(sql)
    assert feedback_valid.is_valid_syntax

    # Syntax validation (Invalid SQL)
    feedback_invalid = parser.validate_syntax("SELECT FROM WHERE ;")
    assert not feedback_invalid.is_valid_syntax
    assert feedback_invalid.error_message is not None

    # AST Edit Size calculation
    ast1 = parser.parse("SELECT name FROM employees;")
    ast2 = parser.parse("SELECT emp_name FROM employees;")
    dist_small = parser.calculate_ast_edit_size(ast1, ast2)

    ast3 = parser.parse("SELECT d.dept_name, count(*) FROM departments d JOIN employees e ON d.id = e.id GROUP BY d.dept_name;")
    dist_large = parser.calculate_ast_edit_size(ast1, ast3)
    assert dist_small < dist_large


def test_scope_analyzer_alias_and_columns(sample_company_schema):
    """Test ScopeAnalyzer resolving aliases and column ownership."""
    analyzer = ScopeAnalyzer()

    # Query with alias
    sql = "SELECT e.emp_name, d.dept_name FROM employees AS e JOIN departments AS d ON e.dept_id = d.dept_id;"
    scope = analyzer.analyze(sql, schema=sample_company_schema)

    assert "e" in scope.tables
    assert "d" in scope.tables
    assert scope.tables["e"].table_name == "employees"
    assert scope.tables["d"].table_name == "departments"

    # Verify column bindings
    emp_binding = next(b for b in scope.column_bindings if b.column_name.lower() == "emp_name")
    assert emp_binding.resolved_table == "employees"
    assert emp_binding.qualifier == "e"

    dept_binding = next(b for b in scope.column_bindings if b.column_name.lower() == "dept_name")
    assert dept_binding.resolved_table == "departments"
    assert dept_binding.qualifier == "d"


def test_scope_analyzer_cte_support():
    """Test ScopeAnalyzer correctly identifying CTEs."""
    analyzer = ScopeAnalyzer()

    sql = """
    WITH active_emp AS (
        SELECT emp_id, emp_name FROM employees WHERE salary > 50000
    )
    SELECT emp_name FROM active_emp;
    """
    scope = analyzer.analyze(sql)

    assert "active_emp" in scope.ctes
    assert scope.tables["active_emp"].is_cte


def test_schema_knowledge_graph_joins(sample_company_schema):
    """Test SchemaKnowledgeGraph direct and multi-hop foreign key path finding."""
    kg = SchemaKnowledgeGraph(sample_company_schema)

    # 1. Direct foreign key: employees -> departments
    direct_path = kg.find_join_path("employees", "departments")
    assert direct_path is not None
    assert len(direct_path) == 1
    assert direct_path[0].from_table == "employees"
    assert direct_path[0].to_table == "departments"
    assert direct_path[0].from_column == "dept_id"
    assert direct_path[0].to_column == "dept_id"
    assert direct_path[0].to_sql_predicate() == "employees.dept_id = departments.dept_id"

    # 2. Multi-hop foreign key: tasks -> employees -> departments
    multi_hop = kg.find_join_path("tasks", "departments")
    assert multi_hop is not None
    assert len(multi_hop) == 2
    assert multi_hop[0].from_table == "tasks"
    assert multi_hop[0].to_table == "employees"
    assert multi_hop[1].from_table == "employees"
    assert multi_hop[1].to_table == "departments"

    # 3. Steiner connecting tree for all 3 tables
    tree_steps = kg.find_connecting_join_tree(["tasks", "departments", "employees"])
    assert len(tree_steps) >= 2


def test_schema_knowledge_graph_candidate_columns(sample_company_schema):
    """Test SchemaKnowledgeGraph fuzzy column candidate matching."""
    kg = SchemaKnowledgeGraph(sample_company_schema)

    # Search for misspelled 'departmnt_nam'
    candidates = kg.find_candidate_columns("departmnt_nam", table_name="departments")
    assert len(candidates) > 0
    top_col = candidates[0]
    assert top_col[0] == "departments"
    assert top_col[1] == "dept_name"
    assert top_col[2] > 0.6  # Similarity ratio should be high
