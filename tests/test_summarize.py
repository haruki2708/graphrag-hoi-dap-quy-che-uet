"""Unit tests for eval/summarize.py on fake rows (no files, no API).
Run from the repo root:  python -m unittest discover -s tests -t .
"""

import unittest

from eval import summarize as sm


def row(qid, run, label, qtype="single", origin="manual", delegated=False, recall=1.0, precision=1.0,
        retrieved=("u1", "u2"), r5=1.0, error=""):
    raw = {"id": qid, "run": str(run), "judge_label": label, "type": qtype, "origin": origin,
           "delegated": str(delegated), "citation_recall": "" if qtype == "out_of_scope" else str(recall),
           "citation_precision": "" if qtype == "out_of_scope" else str(precision),
           "recall@5": "" if qtype == "out_of_scope" else str(r5), "recall@10": "" if qtype == "out_of_scope" else "1.0",
           "mrr": "" if qtype == "out_of_scope" else "1.0", "retrieved_ids": " ".join(retrieved),
           "source_ids": "" if qtype == "out_of_scope" else "u1", "cited_ids": "u1", "coarse_citations": "",
           "invalid_citations": "", "input_tokens": "10", "output_tokens": "5", "latency_s": "2.0",
           "from_cache": "False", "error": error, "judge_error": "", "answer": "a"}
    return sm.parse_row(raw)


class SummarizeTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            row("q1", 0, "dung"), row("q1", 1, "dung"), row("q1", 2, "mot_phan"),
            row("q2", 0, "sai", qtype="multi", recall=0.0), row("q2", 1, "sai", qtype="multi", recall=0.0),
            row("q2", 2, "sai", qtype="multi", recall=0.0),
            row("q3", 0, "dung", qtype="out_of_scope"), row("q3", 1, "sai", qtype="out_of_scope"),
            row("q3", 2, "dung", qtype="out_of_scope"),
        ]

    def test_judge_score_mapping_and_spread(self):
        # run0: (1 + 0 + 1)/3, run1: (1 + 0 + 0)/3, run2: (0.5 + 0 + 1)/3
        s = sm.judge_score(self.rows)
        self.assertEqual((s["min"], s["max"]), (0.3333, 0.6667))
        self.assertAlmostEqual(s["mean"], round((2 / 3 + 1 / 3 + 0.5) / 3, 4), places=4)
        self.assertEqual(sm.LABEL_SCORE, {"dung": 1.0, "mot_phan": 0.5, "sai": 0.0})

    def test_label_distribution(self):
        dist = sm.label_distribution(self.rows)
        self.assertEqual(dist["dung"], {"mean": 1.3333, "min": 1, "max": 2})
        self.assertEqual(dist["sai"]["min"], 1)

    def test_unstable_questions(self):
        self.assertEqual(sm.unstable_questions(self.rows), ["q1", "q3"])

    def test_out_of_scope_excluded_from_citation_metrics_and_reported_alone(self):
        s = sm.summarise_system(self.rows, with_retrieval=True, cost_usd=0.01)
        self.assertEqual(s["groups"]["total"]["citation_recall"]["mean"], 0.5)   # q1 = 1, q2 = 0
        self.assertEqual(s["out_of_scope_dung_rate"]["mean"], round(2 / 3, 4))
        self.assertEqual(s["groups"]["type"]["multi"]["n_questions"], 1)
        self.assertEqual(s["groups"]["total"]["retrieval"]["n"], 2)

    def test_retrieval_must_match_across_runs(self):
        rows = [row("q1", 0, "dung"), row("q1", 1, "dung", retrieved=("u9",))]
        with self.assertRaises(AssertionError):
            sm.summarise_system(rows, with_retrieval=True, cost_usd=None)

    def test_failed_rows_left_out(self):
        rows = self.rows + [row("q4", 0, "", error="HTTP 503")]
        s = sm.summarise_system(rows, with_retrieval=False, cost_usd=None)
        self.assertEqual((s["rows"], s["failed_rows"]), (10, 1))
        self.assertEqual(s["groups"]["total"]["n_questions"], 3)

    def test_lowest(self):
        low = sm.lowest(self.rows, n=2)
        self.assertEqual([x["id"] for x in low], ["q2", "q3"])
        self.assertEqual(low[0]["labels"], ["sai", "sai", "sai"])


if __name__ == "__main__":
    unittest.main()
