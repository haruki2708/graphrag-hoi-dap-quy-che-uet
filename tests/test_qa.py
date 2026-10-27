"""Tests for src/qa (citation extraction, systems) and eval/run_eval.py with a fake LLM (no API calls).
Run from the repo root:  python -m unittest discover -s tests -t .
"""

import io
import json
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from eval import run_eval
from src.llm import LLMResult
from src.qa import citations as cit
from src.qa import systems
from src.qa.answer import SYSTEM_PROMPT

_UNIT_RECORDS = [json.loads(line) for line in
                 (Path(__file__).resolve().parents[1] / "data" / "processed" / "qc3626_content_units.jsonl")
                 .read_text(encoding="utf-8").splitlines() if line.strip()]
UNITS = {u["id"] for u in _UNIT_RECORDS}
ARTICLES = {u["dieu_id"] for u in _UNIT_RECORDS}


def ids(*short: str) -> list[str]:
    return [f"qc3626.{s}" for s in short]


class CitationTests(unittest.TestCase):
    def check(self, text: str, cited: list[str], invalid: list[str] | None = None, coarse: list[str] | None = None):
        got = cit.citations(text, UNITS, ARTICLES)
        self.assertEqual(got, (ids(*cited), ids(*(coarse or [])), ids(*(invalid or []))), text)

    def test_forms(self):
        self.check("Theo Điều 44 khoản 1 điểm a, sinh viên bị cảnh báo.", ["d44.k1"])
        self.check("Căn cứ khoản 2 Điều 17.", ["d17.k2"])
        self.check("Xem điểm a khoản 1 Điều 44.", ["d44.k1"])
        self.check("Theo Điều 44, khoản 2, điểm c.", ["d44.k2"])
        self.check("khoản 2, 3 Điều 10 quy định…", ["d10.k2", "d10.k3"])
        self.check("Điều 44 khoản 1 và 2.", ["d44.k1", "d44.k2"])

    def test_article_units(self):
        self.check("Điều 16 quy định học kỳ.", ["d16"])
        self.check("Theo Điều 35, sinh viên tài năng phải…", ["d35"])  # article-level doan_khong_so unit
        self.check("Điều 44 quy định xử lý học vụ.", ["d44"])           # article with lead text

    def test_point_d_with_stroke(self):
        self.check("Điều 46 khoản 1 điểm đ: ngoại ngữ.", ["d46.k1"])

    def test_coarse_whole_article(self):
        self.check("Theo Điều 46.", [], coarse=["d46"])               # article with clauses, no lead text
        self.check("Điều 46 và Điều 44 khoản 1.", ["d44.k1"], coarse=["d46"])
        self.assertEqual(len(ARTICLES), 51)

    def test_invalid_only_for_nonexistent_ids(self):
        self.check("Điều 16 khoản 2.", [], ["d16.k2"])                # clause does not exist
        self.check("Điều 99 khoản 1.", [], ["d99.k1"])
        self.check("Theo Điều 99.", [], ["d99"])                      # article does not exist

    def test_raw_codes_and_dedup(self):
        self.check("(mã qc3626.d44.k1.a) và Điều 44 khoản 1.", ["d44.k1"])

    def test_point_list_does_not_swallow_next_article(self):
        self.check("Theo Điều 44 khoản 1 điểm a và Điều 51.", ["d44.k1", "d51"])
        self.check("điểm a, b khoản 2 Điều 6 và Điều 16", ["d6.k2", "d16"])

    def test_order_and_mix(self):
        self.check("Điều 17 khoản 2; điểm a khoản 1 Điều 44; Điều 51.", ["d17.k2", "d44.k1", "d51"])


def fake_complete(role, system, messages, json_schema=None, run=0, use_cache=True, params_override=None):
    assert role == "answer" and system == SYSTEM_PROMPT
    text = "Theo Điều 44 khoản 1 điểm a và Điều 46."
    return LLMResult(text=text, parsed=None, role=role, provider="fake", model_requested="m", model_served="m",
                     run=run, finish_reason="end_turn", input_tokens=10, output_tokens=5, latency_s=0.1, attempts=1,
                     called_at="2026-10-27T00:00:00+07:00", from_cache=False, structured_output=False,
                     truncated=False, params=params_override or {"effort": "medium"})


class FakeIndex:
    top_k = 2
    units = [{"id": u, "heading": "H", "text": "T"} for u in ids("d44.k1", "d17.k2", "d16")]

    def search(self, query, k):
        return [(u["id"], 1.0) for u in self.units][:k]


def question(qid="dev-x", sources=("d44.k1", "d17.k2"), qtype="conditional", split="dev"):
    return {"id": qid, "question": "q?", "answer": "a", "source_ids": ids(*sources), "type": qtype, "split": split,
            "delegated": False, "origin": "manual", "ref_points": []}


class RunEvalTests(unittest.TestCase):
    def test_refuses_test_split(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(run_eval.main(["--system", "llm_only", "--split", "test"]), 2)

    def test_refuses_to_overwrite_without_flag(self):
        import tempfile
        from datetime import datetime
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            stem = f"{datetime.now(run_eval.VN_TZ):%Y-%m-%d}_llm_only_dev"
            (Path(tmp) / f"{stem}.csv").write_text("x", encoding="utf-8")
            err = io.StringIO()
            with mock.patch.object(run_eval, "RESULTS_DIR", Path(tmp)), redirect_stderr(err):
                self.assertEqual(run_eval.main(["--system", "llm_only", "--ids", "dev-m001"]), 2)
            self.assertIn("--overwrite", err.getvalue())
            self.assertEqual((Path(tmp) / f"{stem}.csv").read_text(encoding="utf-8"), "x")

    def test_judge_refuses_to_overwrite_without_flag(self):
        import tempfile

        from eval import judge_answers

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "2026-01-01_llm_only_dev.csv"
            source.write_text("id\n", encoding="utf-8")
            (Path(tmp) / "2026-01-01_llm_only_dev_judged.csv").write_text("keep", encoding="utf-8")
            with redirect_stderr(io.StringIO()):
                self.assertEqual(judge_answers.main([str(source)]), 2)
            self.assertEqual((Path(tmp) / "2026-01-01_llm_only_dev_judged.csv").read_text(encoding="utf-8"), "keep")

    def test_rejects_non_dev_question(self):
        with self.assertRaises(ValueError):
            run_eval.evaluate("llm_only", [question(split="test")], 1, None, UNITS, ARTICLES, complete_fn=fake_complete)

    def test_llm_only_rows(self):
        rows = run_eval.evaluate("llm_only", [question()], 2, "low", UNITS, ARTICLES, complete_fn=fake_complete)
        self.assertEqual([r["run"] for r in rows], [0, 1])
        row = rows[0]
        self.assertEqual(row["cited_ids"], ids("d44.k1"))
        self.assertEqual(row["coarse_citations"], ids("d46"))       # "Điều 46": whole article, not invalid
        self.assertEqual(row["invalid_citations"], [])
        # coarse citations are left out: precision 1/1 (not 1/2), recall 1/2
        self.assertEqual((row["citation_recall"], row["citation_precision"]), (0.5, 1.0))
        self.assertIsNone(row["retrieved_ids"])
        self.assertIsNone(row["recall@5"])
        self.assertEqual(row["effort"], "low")

    def test_rag_rows_and_context(self):
        seen = {}

        def capture(role, system, messages, **kwargs):
            seen["content"] = messages[0]["content"]
            return fake_complete(role, system, messages, **kwargs)

        rows = run_eval.evaluate("rag_baseline", [question()], 1, None, UNITS, ARTICLES, complete_fn=capture, index=FakeIndex())
        self.assertEqual(rows[0]["retrieved_ids"], ids("d44.k1", "d17.k2", "d16"))
        self.assertEqual((rows[0]["recall@5"], rows[0]["mrr"]), (1.0, 1.0))
        self.assertIn("[H] (mã qc3626.d17.k2)", seen["content"])
        self.assertNotIn("qc3626.d16", seen["content"])  # only top_k = 2 units go into the prompt

    def test_out_of_scope_blank_metrics(self):
        rows = run_eval.evaluate("llm_only", [question(sources=(), qtype="out_of_scope")], 1, None, UNITS, ARTICLES,
                                 complete_fn=fake_complete)
        self.assertEqual((rows[0]["citation_recall"], rows[0]["citation_precision"]), (None, None))

    def test_same_prompt_for_both_systems(self):
        self.assertIs(systems.SYSTEM_PROMPT, SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
