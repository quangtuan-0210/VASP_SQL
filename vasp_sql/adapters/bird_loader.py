"""
Loader for BIRD benchmark datasets and associated SQLite databases.
"""

import json
import os
from typing import Dict, List, Optional
import yaml

from vasp_sql.adapters.models import EvaluationSample
from vasp_sql.core.schema import SchemaMetadata


class BirdDatasetLoader:
    """
    Manages loading and querying samples from BIRD benchmark datasets.
    """

    def __init__(self, config_path: str = "configs/bird_config.yaml"):
        self.config_path = config_path
        self.config = self._load_config()

        self.dev_json_path = self.config["benchmark"]["dev_json_path"]
        self.databases_dir = self.config["benchmark"]["databases_dir"]
        self.tables_json_path = self.config["benchmark"].get("dev_tables_path")

        self.samples: List[EvaluationSample] = []
        self._schema_cache: Dict[str, SchemaMetadata] = {}

        self._load_dataset()

    def _load_config(self) -> dict:
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")
        with open(self.config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _load_dataset(self) -> None:
        if not os.path.exists(self.dev_json_path):
            raise FileNotFoundError(f"BIRD dev.json not found at: {self.dev_json_path}")

        with open(self.dev_json_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        self.samples = []
        for idx, item in enumerate(raw_data):
            # BIRD JSON structure:
            # question_id, db_id, question, evidence, SQL, difficulty
            sample = EvaluationSample(
                question_id=item.get("question_id", idx),
                db_id=item.get("db_id", ""),
                question=item.get("question", ""),
                evidence=item.get("evidence"),
                golden_sql=item.get("SQL"),
                difficulty=item.get("difficulty", "simple")
            )
            self.samples.append(sample)

    def __len__(self) -> int:
        return len(self.samples)

    def get_sample(self, index: int) -> EvaluationSample:
        return self.samples[index]

    def get_sample_by_id(self, question_id: int) -> Optional[EvaluationSample]:
        for s in self.samples:
            if s.question_id == question_id:
                return s
        return None

    def filter_samples(
        self,
        db_id: Optional[str] = None,
        difficulty: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[EvaluationSample]:
        """
        Filter samples by database or difficulty level.
        """
        res = self.samples
        if db_id:
            res = [s for s in res if s.db_id.lower() == db_id.lower()]
        if difficulty:
            res = [s for s in res if s.difficulty.lower() == difficulty.lower()]
        if limit:
            res = res[:limit]
        return res

    def get_db_path(self, db_id: str) -> str:
        """
        Resolve absolute path to the .sqlite database file for a given db_id.
        """
        db_file = os.path.join(self.databases_dir, db_id, f"{db_id}.sqlite")
        if not os.path.exists(db_file):
            # Check alternative naming if any
            alt_file = os.path.join(self.databases_dir, db_id, f"{db_id}.db")
            if os.path.exists(alt_file):
                return alt_file
        return db_file

    def get_schema(self, db_id: str) -> SchemaMetadata:
        """
        Retrieve cached or freshly introspected SchemaMetadata for the given db_id.
        """
        db_id_key = db_id.lower()
        if db_id_key in self._schema_cache:
            return self._schema_cache[db_id_key]

        db_path = self.get_db_path(db_id)
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"Database file does not exist: {db_path}")

        schema = SchemaMetadata.from_sqlite_db(db_path, db_id=db_id)
        self._schema_cache[db_id_key] = schema
        return schema
