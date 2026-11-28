from pathlib import Path
import tempfile
import unittest

import pandas as pd

from lineage_canvas import export_lineage_canvas_html

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, "Install requirements-dev.txt and run: python -m playwright install chromium")
class TableLineageBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browser = cls.playwright.chromium.launch()
        cls.addClassCleanup(cls.browser.close)
        cls.output = tempfile.TemporaryDirectory(prefix="lineage-browser-tests-")
        cls.addClassCleanup(cls.output.cleanup)

    def setUp(self):
        self.page = self.browser.new_page(viewport={"width": 1440, "height": 1000})
        self.addCleanup(self.page.close)
        self.errors, self.remote_requests = [], []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.on("request", lambda request: self.remote_requests.append(request.url)
                     if request.url.startswith(("http:", "https:")) else None)

    def tearDown(self):
        self.assertEqual(self.errors, [], "Browser runtime errors")
        self.assertEqual(self.remote_requests, [], "Standalone pages must work offline")

    def open_graph(self, rows, initial_table="root", batch=80):
        path = Path(self.output.name) / "table.html"
        frame = pd.DataFrame(rows, columns=["source_table", "target_table", "relation_type", "edge_count"])
        export_lineage_canvas_html(frame, str(path), initial_table=initial_table, max_neighbors_per_click=batch)
        self.page.goto(path.as_uri())
        self.page.locator(".node").first.wait_for()

    def activate_root(self):
        self.page.locator('.node[data-table="root"]').press("Enter")

    def edge_count(self):
        return self.page.locator("#viewport .link").count()

    def test_enabling_direction_after_search_restores_neighbors(self):
        for direction, row, opposite in [
            ("childrenToggle", ("root", "child", "reference", 1), "parentsToggle"),
            ("parentsToggle", ("parent", "root", "trigger", 1), "childrenToggle"),
        ]:
            with self.subTest(direction=direction):
                self.open_graph([row])
                self.page.locator(f"#{direction}").click()
                self.page.locator("#search").fill("root")
                self.page.locator("#search").press("Enter")
                self.assertEqual(self.edge_count(), 0)
                self.page.locator(f"#{direction}").click()
                self.assertEqual(self.edge_count(), 1)
                self.page.locator(f"#{opposite}").click()
                self.activate_root()
                self.assertEqual(self.edge_count(), 0, "Disabled directions must not block collapse")

    def test_search_with_all_filters_off_restores_expansion_when_enabled(self):
        self.open_graph([("root", "child", "reference", 1)])
        for label in ["parentsToggle", "childrenToggle", "referenceToggle", "triggerToggle"]:
            self.page.locator(f"#{label}").click()
        self.page.locator("#search").fill("root")
        self.page.locator("#search").press("Enter")
        self.page.locator("#childrenToggle").click()
        self.page.locator("#referenceToggle").click()
        self.assertEqual(self.edge_count(), 1)

    def test_click_loads_neighbors_beyond_default_batch_then_collapses(self):
        self.open_graph([("root", f"child{i:03}", "reference", 1) for i in range(81)])
        self.assertEqual(self.edge_count(), 80)
        self.page.locator('.node[data-table="root"]').click()
        self.page.wait_for_function("document.querySelectorAll('#viewport .link').length === 81")
        self.assertEqual(self.edge_count(), 81)
        self.assertEqual(self.page.locator('.node[data-table="child080"]').count(), 1)
        self.activate_root()
        self.assertEqual(self.page.locator(".node").count(), 1)
        self.activate_root()
        self.assertEqual(self.edge_count(), 80, "Collapsing must reset the loaded batch")

    def test_hidden_direction_does_not_prevent_collapsing_loaded_neighbors(self):
        self.open_graph([("parent", "root", "reference", 1), ("root", "child", "reference", 1)])
        self.page.locator("#childrenToggle").click()
        self.assertEqual(self.edge_count(), 1)
        self.activate_root()
        self.assertEqual(self.edge_count(), 0)
        self.activate_root()
        self.assertEqual(self.edge_count(), 1)
        self.page.locator("#childrenToggle").click()
        self.assertEqual(self.edge_count(), 2)

    def test_batches_work_in_both_directions_and_relation_types(self):
        rows = []
        for relation in ["reference", "trigger"]:
            for i in range(5):
                rows.extend([(f"{relation}_parent{i}", "root", relation, 1),
                             ("root", f"{relation}_child{i}", relation, 1)])
        self.open_graph(rows, batch=2)
        self.assertEqual(self.edge_count(), 8)
        self.activate_root()
        self.assertEqual(self.edge_count(), 16)
        self.activate_root()
        self.assertEqual(self.edge_count(), 20)
        self.assertEqual(self.page.locator(".node").count(), 21)
        self.activate_root()
        self.assertEqual(self.edge_count(), 0)

    def test_filters_preserve_loaded_batches_without_loading_more(self):
        self.open_graph([("root", f"child{i}", "reference", 1) for i in range(7)], batch=2)
        self.activate_root()
        self.assertEqual(self.edge_count(), 4)
        for label in ["childrenToggle", "referenceToggle", "parentsToggle", "triggerToggle"]:
            self.page.locator(f"#{label}").click()
            self.page.locator(f"#{label}").click()
            self.assertEqual(self.edge_count(), 4, "Rebuilding must preserve the loaded batch")
        self.page.locator("#collapseBtn").click()
        self.page.locator("#childrenToggle").click()
        self.page.locator("#childrenToggle").click()
        self.assertEqual(self.edge_count(), 0, "Filters must respect an explicitly collapsed graph")
        self.activate_root()
        self.assertEqual(self.edge_count(), 2)

    def test_expand_upstream_loads_more_and_retains_loaded_ancestors(self):
        rows = [(f"parent{i}", "root", "reference", 1) for i in range(5)]
        rows.append(("grandparent", "parent4", "reference", 1))
        self.open_graph(rows, batch=2)
        self.assertEqual(self.edge_count(), 2)
        self.page.locator("#expandUpBtn").click()
        self.assertEqual(self.edge_count(), 4)
        self.page.locator("#expandUpBtn").click()
        self.assertEqual(self.edge_count(), 6)
        self.assertEqual(self.page.locator('.node[data-table="grandparent"]').count(), 1)
        self.page.locator("#referenceToggle").click()
        self.page.locator("#referenceToggle").click()
        self.assertEqual(self.edge_count(), 6)

    def test_fit_large_graph_keeps_all_nodes_inside_desktop_and_mobile_canvas(self):
        self.open_graph([("root", f"child{i:03}", "reference", 1) for i in range(80)])
        for width, height in [(1440, 1000), (390, 844)]:
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": height})
                self.page.locator("#fitBtn").click()
                result = self.page.evaluate("""() => {
                    const canvas = document.getElementById('chart').getBoundingClientRect();
                    const outside = [...document.querySelectorAll('#viewport .node')].filter(node => {
                        const box = node.getBoundingClientRect();
                        return box.left < canvas.left - 1 || box.top < canvas.top - 1
                            || box.right > canvas.right + 1 || box.bottom > canvas.bottom + 1;
                    }).length;
                    return {outside, scale: view.scale};
                }""")
                self.assertEqual(result["outside"], 0)
                self.assertLess(result["scale"], 0.15)
                self.page.locator("#zoomInBtn").click()
                zoomed = self.page.evaluate("view.scale")
                self.assertAlmostEqual(zoomed / result["scale"], 1.18)
                self.page.locator("#zoomOutBtn").click()
                self.assertAlmostEqual(self.page.evaluate("view.scale"), result["scale"])
        self.page.locator("#collapseBtn").click()
        self.page.locator("#fitBtn").click()
        for _ in range(20):
            self.page.locator("#zoomOutBtn").click()
        self.assertAlmostEqual(self.page.evaluate("view.scale"), 0.15)

    def test_visible_xlsx_contains_every_loaded_edge(self):
        self.open_graph([("root", f"child{i}", "reference", i + 1) for i in range(5)], batch=2)
        self.activate_root()
        self.activate_root()
        workbook = self.page.evaluate("""() => {
            let result;
            const write = XLSX.writeFile;
            XLSX.writeFile = (wb, filename) => {
                result = {filename, bytes: XLSX.write(wb, {type: 'array', bookType: 'xlsx'}).byteLength,
                    edges: XLSX.utils.sheet_to_json(wb.Sheets.Visible_Edges)};
            };
            try { exportVisibleExcel(); } finally { XLSX.writeFile = write; }
            return result;
        }""")
        self.assertTrue(workbook["filename"].endswith(".xlsx"))
        self.assertGreater(workbook["bytes"], 0)
        self.assertEqual(len(workbook["edges"]), 5)
        self.assertEqual({edge["target_table"] for edge in workbook["edges"]}, {f"child{i}" for i in range(5)})


if __name__ == "__main__":
    unittest.main()
