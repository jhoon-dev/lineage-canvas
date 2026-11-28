import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd
from sqlalchemy import create_engine, create_mock_engine, text

from lineage_canvas import _quote_identifier, load_edges_from_database


class DatabaseLineageTests(unittest.TestCase):
    def test_oracle_ordinary_identifiers_remain_unquoted(self):
        engine = create_mock_engine("oracle://", lambda *args: None)
        self.assertEqual(_quote_identifier("metadata.table_lineage", engine), "metadata.table_lineage")
        self.assertEqual(_quote_identifier("source_table", engine), "source_table")
        self.assertEqual(_quote_identifier("METADATA.TABLE_LINEAGE", engine), '"METADATA"."TABLE_LINEAGE"')

    def test_reserved_mixed_case_and_numeric_identifiers_are_quoted_by_dialect(self):
        for url, quote in [("oracle://", '"'), ("postgresql://", '"'),
                           ("sqlite://", '"'), ("mysql://", '`'), ("mssql://", '[')]:
            with self.subTest(url=url):
                engine = create_mock_engine(url, lambda *args: None)
                close = "]" if quote == "[" else quote
                for name in ["select", "MixedCase", "123table"]:
                    self.assertEqual(_quote_identifier(name, engine), f"{quote}{name}{close}")

    def test_unsafe_and_empty_identifier_parts_are_rejected(self):
        engine = create_mock_engine("sqlite://", lambda *args: None)
        for name in ["", ".table", "schema.", "a..b", "a;DROP TABLE b", "a--comment", '"a"', "a b"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                _quote_identifier(name, engine)

    def test_oracle_loader_uses_portable_default_query(self):
        engine = MagicMock()
        engine.dialect = create_mock_engine("oracle://", lambda *args: None).dialect
        frame = pd.DataFrame([dict(source_table="a", target_table="b", relation_type="reference", edge_count=1)])
        with patch.dict(os.environ, {"DATABASE_URL": "oracle://"}, clear=True), \
             patch("lineage_canvas.create_engine", return_value=engine), \
             patch("lineage_canvas.pd.read_sql", return_value=frame) as read_sql:
            self.assertIs(load_edges_from_database(), frame)
        query = str(read_sql.call_args.args[0])
        self.assertIn("FROM table_lineage", query)
        self.assertIn("source_table AS source_table", query)
        self.assertNotIn('"source_table"', query)
        self.assertNotIn('"table_lineage"', query)
        engine.dispose.assert_called_once()

    def test_sqlite_loader_supports_defaults_and_schema_qualified_reserved_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = "sqlite:///" + (Path(tmp) / "lineage.db").as_posix()
            engine = create_engine(url)
            try:
                with engine.begin() as connection:
                    connection.execute(text("CREATE TABLE table_lineage (source_table TEXT, target_table TEXT, relation_type TEXT, edge_count REAL)"))
                    connection.execute(text("INSERT INTO table_lineage VALUES ('raw.a', 'mart.b', NULL, NULL), (NULL, 'skip', 'trigger', 2)"))
                    connection.execute(text('CREATE TABLE "select" ("from" TEXT, "to" TEXT, "Kind" TEXT, "Weight" REAL)'))
                    connection.execute(text('INSERT INTO "select" VALUES (\'raw.c\', \'mart.d\', \'trigger\', 3)'))
                with patch.dict(os.environ, {"DATABASE_URL": url}, clear=True):
                    self.assertEqual(load_edges_from_database().to_dict("records"), [
                        dict(source_table="raw.a", target_table="mart.b", relation_type="reference", edge_count=1),
                    ])
                settings = dict(DATABASE_URL=url, LINEAGE_TABLE="main.select", LINEAGE_SOURCE_COLUMN="from",
                                LINEAGE_TARGET_COLUMN="to", LINEAGE_RELATION_COLUMN="Kind", LINEAGE_METRIC_COLUMN="Weight")
                with patch.dict(os.environ, settings, clear=True):
                    self.assertEqual(load_edges_from_database().to_dict("records"), [
                        dict(source_table="raw.c", target_table="mart.d", relation_type="trigger", edge_count=3),
                    ])
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
