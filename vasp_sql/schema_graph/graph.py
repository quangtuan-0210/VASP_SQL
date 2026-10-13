"""
Đồ thị tri thức schema dùng NetworkX.
Tìm đường nối theo khóa ngoại, nối nhiều chặng và tìm cột gần giống.
"""

from difflib import SequenceMatcher
from typing import Dict, List, Optional, Set, Tuple
import networkx as nx
from pydantic import BaseModel, Field

from vasp_sql.core.schema import ForeignKeyRelation, SchemaMetadata


class JoinPathStep(BaseModel):
    from_table: str = Field(description="Source table name.")
    from_column: str = Field(description="Source column name used in join predicate.")
    to_table: str = Field(description="Target table name.")
    to_column: str = Field(description="Target column name used in join predicate.")

    def to_sql_predicate(self, alias_map: Optional[Dict[str, str]] = None) -> str:
        """
        Tạo điều kiện ON bằng SQL, dùng tên tắt nếu được cung cấp.
        Ví dụ: 'e.dept_id = d.dept_id'
        """
        alias_map = alias_map or {}
        tbl_a = alias_map.get(self.from_table.lower(), self.from_table)
        tbl_b = alias_map.get(self.to_table.lower(), self.to_table)
        return f"{tbl_a}.{self.from_column} = {tbl_b}.{self.to_column}"


class SchemaKnowledgeGraph:
    """
    Biểu diễn schema database dạng đồ thị:
    - Phân cấp bảng -> cột
    - Đồ thị quan hệ khóa ngoại để tự động dựng phép nối nhiều bảng
    """

    def __init__(self, schema: Optional[SchemaMetadata] = None):
        self.schema = schema
        # Dùng MultiGraph cho bảng để có thể có nhiều khóa ngoại giữa cùng hai bảng
        self.table_graph = nx.MultiGraph()
        # Đồ thị đầy đủ gồm cả nút bảng và nút cột
        self.full_graph = nx.Graph()

        if schema:
            self.build_from_schema(schema)

    def build_from_schema(self, schema: SchemaMetadata) -> None:
        """
        Dựng đồ thị tri thức từ SchemaMetadata.
        """
        self.schema = schema
        self.table_graph.clear()
        self.full_graph.clear()

        # 1. Thêm nút bảng và nút cột
        for tbl_name, tbl_meta in schema.tables.items():
            tbl_lower = tbl_name.lower()
            self.table_graph.add_node(tbl_lower, type="table", name=tbl_name)
            self.full_graph.add_node(tbl_lower, type="table", name=tbl_name)

            for col_name, col_meta in tbl_meta.columns.items():
                col_id = f"{tbl_lower}.{col_name.lower()}"
                self.full_graph.add_node(
                    col_id,
                    type="column",
                    table=tbl_lower,
                    name=col_meta.name,
                    data_type=col_meta.data_type,
                    is_pk=col_meta.is_primary_key
                )
                self.full_graph.add_edge(tbl_lower, col_id, rel="has_column")

        # 2. Thêm cạnh khóa ngoại
        for fk in schema.foreign_keys:
            from_tbl = fk.from_table.lower()
            to_tbl = fk.to_table.lower()

            if from_tbl in self.table_graph and to_tbl in self.table_graph:
                self.table_graph.add_edge(
                    from_tbl,
                    to_tbl,
                    rel="foreign_key",
                    fk_from_table=from_tbl,
                    from_col=fk.from_column,
                    to_col=fk.to_column
                )

                # Nối các cột trong đồ thị đầy đủ
                from_col_id = f"{from_tbl}.{fk.from_column.lower()}"
                to_col_id = f"{to_tbl}.{fk.to_column.lower()}"
                if self.full_graph.has_node(from_col_id) and self.full_graph.has_node(to_col_id):
                    self.full_graph.add_edge(from_col_id, to_col_id, rel="references")

    def find_join_path(self, table_a: str, table_b: str) -> Optional[List[JoinPathStep]]:
        """
        Tìm đường nối ngắn nhất giữa hai bảng theo khóa ngoại.
        Hỗ trợ nối nhiều chặng (VD A -> B -> C).
        """
        src = table_a.lower()
        dst = table_b.lower()

        if src not in self.table_graph or dst not in self.table_graph:
            return None
        if src == dst:
            return []

        try:
            path_nodes = nx.shortest_path(self.table_graph, source=src, target=dst)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

        # Dựng các JoinPathStep dọc theo đường ngắn nhất
        join_steps: List[JoinPathStep] = []
        for i in range(len(path_nodes) - 1):
            curr_tbl = path_nodes[i]
            next_tbl = path_nodes[i + 1]

            edge_data_dict = self.table_graph.get_edge_data(curr_tbl, next_tbl)
            if not edge_data_dict:
                return None

            # Lấy cạnh khóa ngoại đầu tiên giữa hai bảng này
            first_edge = next(iter(edge_data_dict.values()))
            # Đồ thị vô hướng: nếu đi ngược cạnh khóa ngoại (từ bảng được tham chiếu
            # sang bảng tham chiếu) thì đổi chỗ hai cột để
            # from_column luôn thuộc về curr_tbl.
            if first_edge.get("fk_from_table", curr_tbl) == curr_tbl:
                from_col, to_col = first_edge["from_col"], first_edge["to_col"]
            else:
                from_col, to_col = first_edge["to_col"], first_edge["from_col"]

            # Giữ đúng chiều của bước nối
            join_steps.append(
                JoinPathStep(
                    from_table=curr_tbl,
                    from_column=from_col,
                    to_table=next_tbl,
                    to_column=to_col
                )
            )

        return join_steps

    def all_shortest_join_paths(self, table_a: str, table_b: str, limit: int = 10) -> List[List[JoinPathStep]]:
        """
        Mọi đường nối ngắn nhất giữa hai bảng, tách riêng từng cạnh khóa ngoại song song:
        superhero -> colour có 3 khóa ngoại (màu mắt / tóc / da) -> 3 đường một bước riêng.
        Dùng để phát hiện khi chỉ dựa vào schema thì không chọn được cách nối.
        """
        src, dst = table_a.lower(), table_b.lower()
        if src not in self.table_graph or dst not in self.table_graph or src == dst:
            return []
        try:
            node_paths = list(nx.all_shortest_paths(self.table_graph, source=src, target=dst))
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

        results: List[List[JoinPathStep]] = []
        for nodes in node_paths:
            partial: List[List[JoinPathStep]] = [[]]
            for curr_tbl, next_tbl in zip(nodes, nodes[1:]):
                options = []
                for edge in (self.table_graph.get_edge_data(curr_tbl, next_tbl) or {}).values():
                    if edge.get("fk_from_table", curr_tbl) == curr_tbl:
                        fc, tc = edge["from_col"], edge["to_col"]
                    else:
                        fc, tc = edge["to_col"], edge["from_col"]
                    options.append(JoinPathStep(from_table=curr_tbl, from_column=fc, to_table=next_tbl, to_column=tc))
                partial = [p + [o] for p in partial for o in options]
                if len(partial) > limit:
                    partial = partial[:limit]
            results.extend(partial)
            if len(results) >= limit:
                break
        return results[:limit]

    def find_connecting_join_tree(self, required_tables: List[str]) -> List[JoinPathStep]:
        """
        Tìm các bước nối liên kết mọi bảng cần thiết (xấp xỉ cây Steiner nhỏ nhất).
        """
        tables = [t.lower() for t in required_tables if t.lower() in self.table_graph]
        if len(tables) <= 1:
            return []

        connected_tables: Set[str] = {tables[0]}
        pending_tables: Set[str] = set(tables[1:])
        all_join_steps: List[JoinPathStep] = []

        while pending_tables:
            best_path: Optional[List[JoinPathStep]] = None
            best_target: Optional[str] = None

            for start in connected_tables:
                for target in pending_tables:
                    path = self.find_join_path(start, target)
                    if path is not None:
                        if best_path is None or len(path) < len(best_path):
                            best_path = path
                            best_target = target

            if not best_path or not best_target:
                break  # Đồ thị bị tách rời

            all_join_steps.extend(best_path)
            for step in best_path:
                connected_tables.add(step.from_table)
                connected_tables.add(step.to_table)
            pending_tables.discard(best_target)

        return all_join_steps

    def find_candidate_columns(
        self,
        fuzzy_col_name: str,
        table_name: Optional[str] = None,
        min_similarity: float = 0.5,
        top_k: int = 5
    ) -> List[Tuple[str, str, float]]:
        """
        Tìm các cột ứng viên gần giống một tên cột gõ sai hoặc mơ hồ.
        Trả về: danh sách (tên_bảng, tên_cột, độ_giống), sắp theo độ giống.
        """
        if not self.schema:
            return []

        query_col = fuzzy_col_name.lower().replace("_", "").replace(" ", "")
        candidates: List[Tuple[str, str, float]] = []

        tables_to_search = (
            [table_name.lower()]
            if table_name and self.schema.has_table(table_name)
            else list(self.schema.tables.keys())
        )

        for tbl_name in tables_to_search:
            tbl_meta = self.schema.get_table(tbl_name)
            if not tbl_meta:
                continue

            for col_meta in tbl_meta.columns.values():
                target_col = col_meta.name.lower().replace("_", "").replace(" ", "")
                score = SequenceMatcher(None, query_col, target_col).ratio()

                if score >= min_similarity:
                    candidates.append((tbl_meta.name, col_meta.name, round(score, 3)))

        # Sắp xếp giảm dần theo độ giống
        candidates.sort(key=lambda x: x[2], reverse=True)
        return candidates[:top_k]
