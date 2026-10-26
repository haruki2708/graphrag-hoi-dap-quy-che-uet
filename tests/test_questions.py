"""Unit tests for eval/convert_questions.py and eval/validate_questions.py, on fake data only
(never the real test split). Run from the repo root:  python -m unittest discover -s tests -t .
"""

import unittest

from eval import convert_questions as conv
from eval import validate_questions as val

UNITS = {
    "qc3626.d5.k2", "qc3626.d5.k2.g", "qc3626.d17.k2", "qc3626.d44", "qc3626.d44.k1", "qc3626.d44.k1.a",
    "qc3626.d46", "qc3626.d46.k1", "qc3626.c1", "qc3626.d1.k1", "qc3626.d2.k1", "qc3626.d3.k1", "qc3626.d4.k1",
}


def record(**overrides) -> dict:
    base = {
        "id": "dev-x1", "question": "câu hỏi giả", "answer": "đáp án giả", "source_ids": ["qc3626.d17.k2"],
        "type": "single", "split": "dev", "delegated": False, "origin": "manual", "ref_points": [],
    }
    base.update(overrides)
    return base


class SplitIdsTests(unittest.TestCase):
    def test_separators_and_empties(self):
        self.assertEqual(conv.split_ids("qc3626.d5.k2  qc3626.d17.k2"), ["qc3626.d5.k2", "qc3626.d17.k2"])
        self.assertEqual(conv.split_ids("qc3626.d5.k2\nqc3626.d17.k2;\n"), ["qc3626.d5.k2", "qc3626.d17.k2"])
        self.assertEqual(conv.split_ids("qc3626.d5.k2, ;qc3626.d17.k2"), ["qc3626.d5.k2", "qc3626.d17.k2"])

    def test_empty_values(self):
        self.assertEqual(conv.split_ids(None), [])
        self.assertEqual(conv.split_ids("  \n "), [])

    def test_prefix_added_when_missing(self):
        self.assertEqual(conv.split_ids("d43.k1 qc3626.d42.k5"), ["qc3626.d43.k1", "qc3626.d42.k5"])

    def test_point_with_vietnamese_letter(self):
        self.assertEqual(conv.split_ids("qc3626.d46.k1.đ"), ["qc3626.d46.k1.đ"])

    def test_parse_bool(self):
        self.assertIs(conv.parse_bool(True), True)
        self.assertIs(conv.parse_bool("x"), True)
        self.assertIs(conv.parse_bool(None), False)
        with self.assertRaises(ValueError):
            conv.parse_bool("maybe")

    def test_rows_without_question_are_skipped(self):
        header = list(conv.COLUMNS)
        rows = [header, ["t1", "single", True, "q?", "a.", "qc3626.d17.k2", "", "note"], ["t2", "single", False, "", "", "", "", ""]]
        records = conv.records_from_sheet(rows, "test")
        self.assertEqual([r["id"] for r in records], ["t1"])
        self.assertEqual(list(records[0]), list(conv.KEY_ORDER))
        self.assertNotIn("ghi_chu", records[0])
        self.assertEqual(records[0]["origin"], "manual")


class RecordRuleTests(unittest.TestCase):
    def errors(self, **overrides) -> list[str]:
        rec = record(**overrides)
        return val.check_record(rec, rec["split"], UNITS)

    def test_valid_record(self):
        self.assertEqual(self.errors(), [])

    def test_missing_key(self):
        rec = record()
        del rec["origin"]
        self.assertTrue(any("missing key" in e for e in val.check_record(rec, "dev", UNITS)))

    def test_bad_enums_and_types(self):
        self.assertTrue(self.errors(type="trick"))
        self.assertTrue(self.errors(origin="llm"))
        self.assertTrue(self.errors(delegated="yes"))

    def test_split_must_match_file(self):
        self.assertTrue(val.check_record(record(split="dev"), "test", UNITS))

    def test_unknown_unit(self):
        self.assertTrue(any("unknown unit" in e for e in self.errors(source_ids=["qc3626.d99.k1"])))
        self.assertTrue(any("unknown unit" in e for e in self.errors(ref_points=["qc3626.d99.k1.a"])))

    def test_point_and_chapter_not_allowed_in_sources(self):
        self.assertTrue(any("point-level" in e for e in self.errors(source_ids=["qc3626.d44.k1.a"])))
        self.assertTrue(any("chapter" in e for e in self.errors(source_ids=["qc3626.c1"])))

    def test_article_codes(self):
        self.assertEqual(self.errors(source_ids=["qc3626.d44"]), [])  # article with lead text: allowed
        self.assertTrue(any("not a :DonViNoiDung" in e for e in self.errors(source_ids=["qc3626.d46"])))

    def test_max_five_sources(self):
        six = ["qc3626.d1.k1", "qc3626.d2.k1", "qc3626.d3.k1", "qc3626.d4.k1", "qc3626.d5.k2", "qc3626.d17.k2"]
        self.assertTrue(any("max 5" in e for e in self.errors(source_ids=six)))

    def test_out_of_scope_iff_no_sources(self):
        self.assertEqual(self.errors(type="out_of_scope", source_ids=[]), [])
        self.assertTrue(self.errors(type="out_of_scope"))
        self.assertTrue(self.errors(source_ids=[]))

    def test_generated_not_allowed_in_test(self):
        self.assertTrue(any("generated question in test" in e
                            for e in self.errors(split="test", origin="generated")))


class CrossCheckTests(unittest.TestCase):
    def test_duplicate_ids_across_files(self):
        errors, _ = val.cross_checks({"test": [record(id="q1", split="test")], "dev": [record(id="q1")]})
        self.assertTrue(any("q1" in e for e in errors))

    def test_shared_sources_and_similar_questions(self):
        test = [record(id="t1", split="test", question="sinh viên bị cảnh báo học vụ khi nào",
                       source_ids=["qc3626.d44.k1"])]
        dev = [record(id="d1", question="sinh viên bị cảnh báo học vụ khi nào vậy", source_ids=["qc3626.d44.k1"]),
               record(id="d2", question="học phí tính thế nào", source_ids=["qc3626.d17.k2"])]
        errors, overlap = val.cross_checks({"test": test, "dev": dev})
        self.assertEqual(errors, [])
        self.assertEqual(overlap["shared_sources"], {"qc3626.d44.k1": {"test": ["t1"], "dev": ["d1"]}})
        self.assertEqual([(t, d) for t, d, _ in overlap["similar_pairs"]], [("t1", "d1")])

    def test_jaccard(self):
        self.assertEqual(val.jaccard("a b", "a b"), 1.0)
        self.assertEqual(val.jaccard("a b", "c d"), 0.0)


if __name__ == "__main__":
    unittest.main()
