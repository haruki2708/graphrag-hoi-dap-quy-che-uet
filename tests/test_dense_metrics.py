"""Unit tests for eval/retrieval_metrics.py on fake data (no model download, no GPU).
Run from the repo root:  python -m unittest discover -s tests -t .
"""

import json
import tempfile
import unittest
from pathlib import Path

from eval import retrieval_metrics as rm


def q(qid, sources, qtype="single", origin="manual", delegated=False, split="dev"):
    return {"id": qid, "question": "?", "answer": "!", "source_ids": sources, "type": qtype, "split": split,
            "delegated": delegated, "origin": origin, "ref_points": []}


class MetricTests(unittest.TestCase):
    def test_recall_hit_mrr(self):
        retrieved = ["u9", "u2", "u7", "u1", "u5", "u6", "u3", "u8", "u4", "u0"]
        self.assertEqual(rm.recall_at(retrieved, ["u1", "u3"], 5), 0.5)   # u1 at rank 4, u3 at rank 7
        self.assertEqual(rm.recall_at(retrieved, ["u1", "u3"], 10), 1.0)
        self.assertEqual(rm.hit_at(retrieved, ["u1", "u3"], 5), 1.0)
        self.assertEqual(rm.hit_at(retrieved, ["u3"], 5), 0.0)
        self.assertEqual(rm.first_hit_rank(retrieved, ["u3", "u1"]), 4)
        self.assertEqual(rm.reciprocal_rank(retrieved, ["u1"]), 0.25)
        self.assertEqual(rm.reciprocal_rank(retrieved, ["zz"]), 0.0)
        self.assertIsNone(rm.first_hit_rank(retrieved, ["zz"]))

    def test_mrr_only_counts_top10(self):
        retrieved = [f"u{i}" for i in range(20)]
        self.assertEqual(rm.reciprocal_rank(retrieved, ["u15"]), 0.0)

    def test_out_of_scope_excluded(self):
        rows = []
        for question, retrieved in [(q("a", ["u1"]), ["u1"]), (q("b", ["u2"]), ["u9", "u2"]),
                                    (q("c", [], qtype="out_of_scope"), ["u1"])]:
            rows.append({"question_id": question["id"], "origin": question["origin"], "type": question["type"],
                         "delegated": question["delegated"], **rm.score_question(question, retrieved)})
        result = rm.breakdown(rows)
        self.assertEqual(result["excluded_out_of_scope"], 1)
        self.assertEqual(result["total"]["n"], 2)
        self.assertEqual(result["total"]["mrr"], 0.75)  # (1 + 1/2) / 2
        self.assertEqual(result["total"]["hit@5"], 1.0)
        self.assertNotIn("out_of_scope", {k for k, v in result["type"].items() if v["n"]})

    def test_breakdown_groups(self):
        rows = [{"question_id": "a", "origin": "manual", "type": "multi", "delegated": True,
                 **rm.score_question(q("a", ["u1"]), ["u1"])},
                {"question_id": "b", "origin": "generated", "type": "multi", "delegated": False,
                 **rm.score_question(q("b", ["u2"]), ["x"])}]
        result = rm.breakdown(rows)
        self.assertEqual(result["origin"]["manual"]["recall@5"], 1.0)
        self.assertEqual(result["origin"]["generated"]["recall@5"], 0.0)
        self.assertEqual(result["delegated"]["true"]["n"], 1)
        self.assertEqual(result["type"]["multi"]["n"], 2)

    def test_compare_ranks(self):
        a = [{"question_id": "1", "scored": True, "first_hit_rank": 1},
             {"question_id": "2", "scored": True, "first_hit_rank": None},
             {"question_id": "3", "scored": True, "first_hit_rank": 3},
             {"question_id": "4", "scored": False, "first_hit_rank": None}]
        b = [{"question_id": "1", "scored": True, "first_hit_rank": 2},
             {"question_id": "2", "scored": True, "first_hit_rank": 5},
             {"question_id": "3", "scored": True, "first_hit_rank": 3},
             {"question_id": "4", "scored": False, "first_hit_rank": None}]
        self.assertEqual(rm.compare_ranks(a, b), {"a_better": 1, "a_worse": 1, "same": 1})


class DevOnlyTests(unittest.TestCase):
    def write(self, name: str, records: list[dict]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / name
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        return path

    def test_reads_dev_file(self):
        path = self.write("questions_v9_dev.jsonl", [q("a", ["u1"])])
        self.assertEqual(len(rm.load_dev_questions(path)), 1)

    def test_refuses_test_file(self):
        path = self.write("questions_v9_test.jsonl", [q("a", ["u1"], split="test")])
        with self.assertRaisesRegex(ValueError, "only the dev split"):
            rm.load_dev_questions(path)

    def test_asserts_split_dev(self):
        path = self.write("questions_v9_dev.jsonl", [q("a", ["u1"]), q("b", ["u2"], split="test")])
        with self.assertRaisesRegex(ValueError, "expected 'dev'"):
            rm.load_dev_questions(path)


if __name__ == "__main__":
    unittest.main()
