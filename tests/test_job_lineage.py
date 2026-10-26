import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import pandas as pd

from job_lineage import (
    MAPPING_COLUMNS, build_job_lineage, export_job_lineage_canvas_html,
    load_job_mapping_csv, normalize_job_mapping,
)
from lineage_canvas import example_edges, export_lineage_canvas_html

ROOT = Path(__file__).resolve().parents[1]


def mapping(*rows):
    return pd.DataFrame(rows, columns=MAPPING_COLUMNS)


class JobLineageTests(unittest.TestCase):
    def test_many_tables_and_duplicates_count_unique_shared_tables(self):
        rows = []
        for i in range(5):
            rows.extend([("A", "Producer", f"t{i}", "WRITE"), ("B", "Consumer", f"t{i}", "READ")])
        rows.extend(rows[:4])
        data = build_job_lineage(mapping(*rows))
        self.assertEqual(data["edges"], [{"source": "A", "target": "B", "tables": [f"t{i}" for i in range(5)], "table_count": 5, "cyclic": False}])
        self.assertEqual(data["mapping_count"], 10)

    def test_legacy_matches_explicit_mapping_including_source_only_and_target_only(self):
        legacy = pd.DataFrame([
            {"source_table": "raw", "target_table": "stg", "job_name": "Load"},
            {"source_table": "stg", "target_table": "mart", "job_name": "Aggregate"},
            {"source_table": "", "target_table": "raw", "job_name": "Extract"},
            {"source_table": "mart", "target_table": "", "job_name": "Sink"},
        ])
        canonical = mapping(("Load", "Load", "raw", "READ"), ("Load", "Load", "stg", "WRITE"),
                            ("Aggregate", "Aggregate", "stg", "READ"), ("Aggregate", "Aggregate", "mart", "WRITE"),
                            ("Extract", "Extract", "raw", "WRITE"), ("Sink", "Sink", "mart", "READ"))
        self.assertEqual(build_job_lineage(legacy), build_job_lineage(canonical))

    def test_normalization_whitespace_case_and_distinct_ids_same_name(self):
        df = normalize_job_mapping(mapping((" A ", "同名", " t ", " write "), ("B", "同名", "t", "read")))
        self.assertEqual(df.iloc[0].to_dict(), dict(job_id="A", job_name="同名", table_name="t", relation_type="WRITE"))
        self.assertEqual(len(build_job_lineage(df)["edges"]), 1)

    def test_invalid_schema_rows_and_conflicting_names(self):
        cases = [pd.DataFrame({"hello": ["world"]}), mapping(("", "x", "t", "READ")),
                 mapping(("A", "x", "", "READ")), mapping(("A", "", "t", "READ")),
                 mapping(("A", "x", "t", "INVALID")), mapping(("A", "x", "t", None)),
                 mapping(("A", "x", "t", "READ"), ("A", "y", "u", "WRITE")),
                 pd.DataFrame([["a", "b"]], columns=["job_id", " job_id "]),
                 pd.DataFrame([{"source_table": "", "target_table": "", "job_name": "A"}])]
        for frame in cases:
            with self.subTest(frame=frame.to_dict()):
                with self.assertRaises(ValueError):
                    normalize_job_mapping(frame)

    def test_cycles_condense_to_levels_and_preserve_both_directions(self):
        df = mapping(("A", "A", "ab", "WRITE"), ("B", "B", "ab", "READ"),
                     ("B", "B", "bc", "WRITE"), ("C", "C", "bc", "READ"),
                     ("C", "C", "ca", "WRITE"), ("A", "A", "ca", "READ"),
                     ("C", "C", "out", "WRITE"), ("D", "D", "out", "READ"),
                     ("E", "E", "in", "WRITE"), ("A", "A", "in", "READ"))
        data = build_job_lineage(df)
        self.assertEqual(data["cycles"], [["A", "B", "C"]])
        self.assertEqual(sum(e["cyclic"] for e in data["edges"]), 3)
        jobs = {j["id"]: j for j in data["jobs"]}
        self.assertEqual([jobs[x]["level"] for x in ["E", "A", "B", "C", "D"]], [0, 1, 1, 1, 2])
        self.assertEqual(len({(j["level"], j["row"]) for j in jobs.values()}), len(jobs))

    def test_in_place_and_isolated_jobs_have_no_false_self_dependencies(self):
        data = build_job_lineage(mapping(("A", "A", "self", "READ"), ("A", "A", "self", "WRITE"),
                                         ("B", "B", "external", "READ"), ("C", "C", "unused", "WRITE")))
        self.assertEqual(data["edges"], [])
        self.assertEqual(data["cycles"], [])
        self.assertEqual(data["jobs"][0]["in_place_tables"], ["self"])
        self.assertEqual(len(data["jobs"]), 3)

    def test_multiple_writers_fan_out_to_readers(self):
        data = build_job_lineage(mapping(("A", "A", "t", "WRITE"), ("B", "B", "t", "WRITE"),
                                         ("C", "C", "t", "READ"), ("D", "D", "t", "READ")))
        self.assertEqual([(e["source"], e["target"]) for e in data["edges"]], [("A", "C"), ("A", "D"), ("B", "C"), ("B", "D")])
        self.assertEqual(data["multi_writer_tables"], [{"table": "t", "job_ids": ["A", "B"]}])

    def test_long_chain_and_large_cycle_do_not_recurse(self):
        rows = []
        for i in range(1500):
            rows.extend([(str(i), str(i), f"t{i}", "WRITE"), (str(i+1), str(i+1), f"t{i}", "READ")])
        data = build_job_lineage(mapping(*rows))
        self.assertEqual(max(j["level"] for j in data["jobs"]), 1500)
        rows.extend([("1500", "1500", "loop", "WRITE"), ("0", "0", "loop", "READ")])
        data = build_job_lineage(mapping(*rows))
        self.assertEqual(len(data["cycles"][0]), 1501)
        self.assertTrue(all(j["level"] == 0 for j in data["jobs"]))

    def test_empty_mapping_and_deterministic_input_order(self):
        self.assertEqual(build_job_lineage(mapping())["jobs"], [])
        source = load_job_mapping_csv(ROOT / "job_table_mapping.csv")
        self.assertEqual(build_job_lineage(source), build_job_lineage(source.sample(frac=1, random_state=123)))

    def test_csv_preserves_zero_padded_ids_literal_na_unicode_and_commas(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mapping.csv"
            path.write_text('\ufeffjob_id,job_name,table_name,relation_type\n001,"한글, Job",NA,READ\n002,NA,"table,comma",WRITE\n', encoding="utf-8")
            df = load_job_mapping_csv(path)
            self.assertEqual(df.iloc[0]["job_id"], "001")
            self.assertEqual(df.iloc[0]["table_name"], "NA")
            self.assertEqual(df.iloc[1]["job_name"], "NA")
            self.assertEqual(df.iloc[1]["table_name"], "table,comma")

    def test_export_escapes_payload_and_title_and_preserves_previous_file_on_error(self):
        dangerous = '</script><img src=x onerror=alert(1)>&"\u2028end'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "canvas.html"
            export_job_lineage_canvas_html(mapping(("001", dangerous, dangerous, "WRITE")), str(path), title=dangerous)
            content = path.read_text(encoding="utf-8")
            self.assertNotIn(dangerous, content)
            payload = json.loads(re.search(r"const PAYLOAD = (.*);", content).group(1))
            self.assertEqual(payload["data"]["jobs"][0]["name"], dangerous)
            self.assertIn("&lt;/script&gt;", content)
            with self.assertRaises(ValueError):
                export_job_lineage_canvas_html(mapping(), str(path), initial_job="missing")
            self.assertEqual(path.read_text(encoding="utf-8"), content)
            self.assertFalse(path.with_name(".canvas.html.tmp").exists())

    def test_existing_table_export_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "table.html"
            clean = export_lineage_canvas_html(example_edges(), output_html=str(path))
            self.assertGreater(len(clean), 0)
            self.assertIn("const PAYLOAD =", path.read_text(encoding="utf-8"))
            self.assertNotIn("__PAYLOAD__", path.read_text(encoding="utf-8"))

    def test_cli_default_job_input_and_legacy_compatibility_without_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "job.html"
            result = subprocess.run([sys.executable, str(ROOT / "lineage_canvas.py"), "--mode", "job", "--output", str(path)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("7 jobs, 5 edges", result.stdout)
            result = subprocess.run([sys.executable, str(ROOT / "lineage_canvas.py"), "--mode", "job", "--input", str(ROOT / "examples/legacy_job_lineage.csv"), "--output", str(path)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("3 jobs, 2 edges", result.stdout)


if __name__ == "__main__":
    unittest.main()
