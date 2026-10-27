"""Tests for the judge calibration sheet builder, on the committed dev data (no API).
Run from the repo root:  python -m unittest discover -s tests -t .
"""

import tempfile
import unittest
from collections import Counter
from pathlib import Path

from eval import build_judge_calibration as bc
from eval import judge_agreement as ja
from eval.convert_questions import read_xlsx


class CalibrationBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.picks, cls.judged, cls.questions = bc.select_samples()

    def label(self, p):
        return next(r for r in self.judged[p["system"]][p["id"]] if int(r["run"]) == p["run"])["judge_label"]

    def test_selection_is_deterministic(self):
        again, _, _ = bc.select_samples()
        self.assertEqual(again, self.picks)

    def test_sample_counts_and_one_row_per_system_and_question(self):
        self.assertEqual(Counter(p["system"] for p in self.picks), {"rag_baseline": 12, "llm_only": 18})
        self.assertEqual(len({(p["system"], p["id"]) for p in self.picks}), 30)
        self.assertEqual(sorted(p["stt"] for p in self.picks), list(range(1, 31)))

    def test_rag_sample_rules(self):
        rag = [p for p in self.picks if p["system"] == "rag_baseline"]
        by_label = Counter(self.label(p) for p in rag)
        self.assertEqual(by_label, {"dung": 10, "mot_phan": 2})
        self.assertEqual({p["id"] for p in rag if self.label(p) == "mot_phan"}, {"dev-s04", "dev-s05"})
        types = {self.questions[p["id"]]["type"] for p in rag}
        self.assertTrue({"multi", "conditional", "out_of_scope"} <= types)
        self.assertTrue(any(self.questions[p["id"]]["delegated"] for p in rag))

    def test_llm_sample_rules(self):
        llm = [p for p in self.picks if p["system"] == "llm_only"]
        types = Counter(self.questions[p["id"]]["type"] for p in llm)
        self.assertEqual(types["out_of_scope"], 2)
        self.assertTrue({"single", "multi", "conditional"} <= set(types))
        by_label = Counter(self.label(p) for p in llm)
        self.assertEqual(by_label, {"dung": 3, "mot_phan": 8, "sai": 7})  # only s01 is a non-out_of_scope "dung"
        unstable = {qid for qid, rows in self.judged["llm_only"].items() if len({r["judge_label"] for r in rows}) > 1}
        self.assertLessEqual(unstable, {p["id"] for p in llm})  # every label-flipping question is in
        self.assertTrue(all(p["run"] == 0 for p in llm))

    def test_manifest_has_no_judge_label(self):
        manifest = bc.manifest_dict(self.picks)
        self.assertEqual(set(manifest["samples"]), {str(i) for i in range(1, 31)})
        for entry in manifest["samples"].values():
            self.assertEqual(set(entry), {"system", "id", "run", "reason"})
            self.assertNotIn("judge", entry["reason"].lower())
        self.assertFalse(any(key.startswith("judge") or key == "label" for key in manifest))

    def test_sheet_roundtrip_and_blindness(self):
        rows = bc.sheet_rows(self.picks, self.judged, self.questions)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sheet.xlsx"
            bc.write_xlsx(path, bc.build_sheets(rows))
            sheets = read_xlsx(path)
        self.assertEqual(list(sheets), ["huong_dan", "cham"])
        header, body = sheets["cham"][0], sheets["cham"][1:]
        self.assertEqual(tuple(header), bc.SHEET_COLUMNS)
        self.assertEqual(len(body), 30)
        for forbidden in ("he", "system", "run", "judge", "glm", "label", "ly_do", "reason"):
            self.assertNotIn(forbidden, header)
        for line in body:
            self.assertTrue(all(v in (None, "") for v in line[6:]), "nhan and ghi_chu must start empty")
        self.assertEqual(sum(1 for line in body if line[4] == bc.NO_SOURCE_TEXT), 3)  # 1 rag + 2 llm out_of_scope
        guide = "\n".join(str(r[0] or "") for r in sheets["huong_dan"])
        for definition in (ln for ln in bc.JUDGE_SYSTEM.splitlines() if ln.startswith('- "')):
            self.assertIn(definition, guide)

    def test_filled_sheet_flows_through_agreement(self):
        """End to end on the real manifest: a fake graded sheet (human = GLM label except 3 shifted) is accepted."""
        manifest = bc.manifest_dict(self.picks)
        rows = bc.sheet_rows(self.picks, self.judged, self.questions)
        by_stt = {p["stt"]: p for p in self.picks}
        shift = {"dung": "mot_phan", "mot_phan": "sai", "sai": "mot_phan"}
        for i, row in enumerate(rows):
            glm = self.label(by_stt[row[0]])
            row[6] = shift[glm] if i < 3 else glm
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "filled.xlsx"
            bc.write_xlsx(path, bc.build_sheets(rows))
            questions = {q["id"]: q for q in bc.rm.load_dev_questions()}
            judged = {(system, qid, int(r["run"])): r for system, by_id in self.judged.items() for qid, rows_ in by_id.items()
                      for r in rows_}
            samples = ja.join_samples(ja.read_graded_sheet(path), manifest, judged, questions)
        result = ja.analyze(samples)
        self.assertEqual((result["overall"]["n"], result["overall"]["agree"]), (30, 27))
        self.assertEqual(len(result["mismatches"]), 3)
        self.assertTrue(result["thresholds_on_3_label_agreement"]["90%"]["pass"])


if __name__ == "__main__":
    unittest.main()
