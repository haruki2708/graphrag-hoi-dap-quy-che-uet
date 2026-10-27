"""Unit tests for eval/judge_agreement.py on fake rows (no API, no real sheet).
Run from the repo root:  python -m unittest discover -s tests -t .
"""

import unittest

from eval import judge_agreement as ja

# Hand-computed example (rows = human, columns = GLM, order sai, mot_phan, dung):
#   sai      3 1 0
#   mot_phan 0 2 1
#   dung     0 0 3
# n = 10, agreement 8/10, row sums 4/3/3, column sums 3/3/4.
#   kappa          = (0.8 - 0.33) / (1 - 0.33)  = 47/67
#   weighted kappa = 1 - 0.1 / 0.46              = 18/23   (linear weights 0, 0.5, 1)
PAIRS = ([("sai", "sai")] * 3 + [("sai", "mot_phan")] + [("mot_phan", "mot_phan")] * 2 + [("mot_phan", "dung")]
         + [("dung", "dung")] * 3)

QUESTIONS = {"q1": {"id": "q1", "type": "single", "question": "Câu hỏi 1?"},
             "q2": {"id": "q2", "type": "multi", "question": "Câu hỏi 2?"}}
MANIFEST = {"samples": {"1": {"system": "rag_baseline", "id": "q1", "run": 0}, "2": {"system": "llm_only", "id": "q2", "run": 1}}}
JUDGED = {("rag_baseline", "q1", 0): {"answer": "Trả lời 1", "judge_label": "dung", "judge_reason": "ok", "judge_model": "glm"},
          ("llm_only", "q2", 1): {"answer": "Trả lời 2", "judge_label": "sai", "judge_reason": "lệch", "judge_model": "glm"}}


def sheet_row(stt, nhan, question=None, answer=None, note=None):
    q = {"1": "Câu hỏi 1?", "2": "Câu hỏi 2?"}[str(stt)] if question is None else question
    a = {"1": "Trả lời 1", "2": "Trả lời 2"}[str(stt)] if answer is None else answer
    return {"stt": str(stt), "cau_hoi": q, "cau_tra_loi": a, "nhan": nhan, "ghi_chu": note}


class KappaTests(unittest.TestCase):
    def test_kappa_matches_hand_calculation(self):
        matrix = ja.confusion(PAIRS)
        self.assertEqual(matrix, [[3, 1, 0], [0, 2, 1], [0, 0, 3]])
        self.assertAlmostEqual(ja.cohen_kappa(matrix), 47 / 67)
        self.assertAlmostEqual(ja.cohen_kappa(matrix, weighted=True), 18 / 23)

    def test_summary_numbers(self):
        s = ja.summarize_pairs(PAIRS)
        self.assertEqual((s["n"], s["agree"]), (10, 8))
        self.assertEqual(s["binary_agree"], 9)  # only (mot_phan, dung) crosses the dung / not-dung line
        self.assertEqual((s["glm_more_lenient"], s["glm_stricter"]), (2, 0))  # both disagreements put GLM one step higher

    def test_kappa_undefined_when_one_label_only(self):
        self.assertIsNone(ja.cohen_kappa(ja.confusion([("dung", "dung")] * 4)))
        self.assertIsNone(ja.cohen_kappa(ja.confusion([])))

    def test_thresholds_for_30_samples(self):
        report = ja.threshold_report(26, 30)
        self.assertEqual((report["85%"]["pass"], report["85%"]["needed"]), (True, 26))
        self.assertEqual((report["90%"]["pass"], report["90%"]["needed"]), (False, 27))
        self.assertFalse(ja.threshold_report(25, 30)["85%"]["pass"])
        self.assertTrue(ja.threshold_report(27, 30)["90%"]["pass"])


class JoinTests(unittest.TestCase):
    def join(self, rows):
        return ja.join_samples(rows, MANIFEST, JUDGED, QUESTIONS)

    def test_join_follows_manifest(self):
        samples = self.join([sheet_row(2, " Sai ", note="sai thật"), sheet_row(1, "dung")])
        self.assertEqual([s["stt"] for s in samples], [1, 2])
        first, second = samples
        self.assertEqual((first["system"], first["id"], first["run"], first["glm"], first["human"]), ("rag_baseline", "q1", 0, "dung", "dung"))
        self.assertEqual((second["system"], second["id"], second["run"], second["glm"], second["human"]), ("llm_only", "q2", 1, "sai", "sai"))
        self.assertEqual(second["human_note"], "sai thật")
        result = ja.analyze(samples)
        self.assertEqual(result["overall"]["agree"], 2)
        self.assertEqual(result["mismatches"], [])

    def test_mismatch_list_has_reason_and_note(self):
        samples = self.join([sheet_row(1, "mot_phan", note="thiếu ngưỡng"), sheet_row(2, "sai")])
        (m,) = ja.analyze(samples)["mismatches"]
        self.assertEqual((m["stt"], m["human"], m["glm"], m["direction"]), (1, "mot_phan", "dung", "glm_more_lenient"))
        self.assertEqual((m["glm_reason"], m["human_note"]), ("ok", "thiếu ngưỡng"))

    def test_blank_or_misspelt_label_is_reported_by_stt(self):
        for bad, expect in ((None, "để trống"), ("", "để trống"), ("  ", "để trống"), ("đúng", "không hợp lệ"), ("mot phan", "không hợp lệ")):
            with self.assertRaises(ja.SheetError) as ctx:
                self.join([sheet_row(1, bad), sheet_row(2, "sai")])
            self.assertEqual([p[0] for p in ctx.exception.problems], [1])
            self.assertIn(expect, ctx.exception.problems[0][1])

    def test_all_problems_are_listed_together(self):
        with self.assertRaises(ja.SheetError) as ctx:
            self.join([sheet_row(1, None), sheet_row(2, "xai")])
        self.assertEqual([p[0] for p in ctx.exception.problems], [1, 2])

    def test_sheet_not_matching_manifest_is_rejected(self):
        with self.assertRaises(ja.SheetError) as ctx:  # text swapped between rows: wrong manifest or shuffled sheet
            self.join([sheet_row(1, "dung", answer="Trả lời 2"), sheet_row(2, "sai")])
        self.assertIn("không khớp manifest", ctx.exception.problems[0][1])
        with self.assertRaises(ja.SheetError) as ctx:  # row missing
            self.join([sheet_row(1, "dung")])
        self.assertEqual(ctx.exception.problems, [(2, "có trong manifest nhưng thiếu trong phiếu")])
        with self.assertRaises(ja.SheetError) as ctx:  # unknown stt
            self.join([sheet_row(1, "dung"), sheet_row(2, "sai"), {**sheet_row(1, "dung"), "stt": "7"}])
        self.assertEqual(ctx.exception.problems[0][0], 7)

    def test_whitespace_differences_in_text_are_ignored(self):
        samples = self.join([sheet_row(1, "dung", answer="Trả  lời\r\n1"), sheet_row(2, "sai")])
        self.assertEqual(len(samples), 2)


if __name__ == "__main__":
    unittest.main()
