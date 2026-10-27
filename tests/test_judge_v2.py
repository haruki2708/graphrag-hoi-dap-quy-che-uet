"""Tests for the judge prompt v2, --manifest filtering and --judged overrides (no API: complete() is faked).
Run from the repo root:  python -m unittest discover -s tests -t .
"""

import csv
import hashlib
import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from eval import judge_agreement as ja
from eval import judge_answers as jd
from eval import retrieval_metrics as rm
from src.llm import core
from src.llm.config import load_config

RESULTS = jd.REPO_ROOT / "eval" / "results"
MANIFEST = jd.REPO_ROOT / "data" / "eval" / "calibration" / "judge_calib_v1_manifest.json"
V1_SHA256 = "c34e48574051c014fa25c7502455af7d276a062a8cfb70bc6f3d6f52d4479357"  # judge prompt used for every result so far

QUESTION = {"id": "dev-x", "question": "Câu hỏi?", "answer": "Đáp án.", "source_ids": ["qc3626.d43.k1", "qc3626.d16", "qc3626.d46.k1.đ"]}
OUT_OF_SCOPE = {"id": "dev-y", "question": "Ký túc xá mở cửa lúc mấy giờ?", "answer": "Không có trong quy chế.", "source_ids": []}
UNITS = {"qc3626.d43.k1": "Văn bản 43.1", "qc3626.d16": "Văn bản 16", "qc3626.d46.k1.đ": "Văn bản 46.1.đ"}


def read_rows(path):
    with Path(path).open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def legacy_message(question, answer, unit_text):
    """The v1 message exactly as eval/judge_answers.py built it before v2 existed."""
    sources = "\n\n".join(f"[{uid}]\n{unit_text[uid]}" for uid in question["source_ids"]) or jd.NO_SOURCE_TEXT
    return (f"CÂU HỎI:\n{question['question']}\n\nĐÁP ÁN MẪU:\n{question['answer']}\n\n"
            f"NGUYÊN VĂN QUY CHẾ:\n{sources}\n\nCÂU TRẢ LỜI CẦN CHẤM:\n{answer}")


class PromptTests(unittest.TestCase):
    def test_v1_is_unchanged_and_default(self):
        self.assertEqual(hashlib.sha256(jd.JUDGE_SYSTEM.encode()).hexdigest(), V1_SHA256)
        self.assertIs(jd.JUDGE_SYSTEMS["v1"], jd.JUDGE_SYSTEM)
        for question in (QUESTION, OUT_OF_SCOPE):
            expected = legacy_message(question, "Trả lời", UNITS)
            self.assertEqual(jd.judge_prompt(question, "Trả lời", UNITS), expected)
            self.assertEqual(jd.judge_prompt(question, "Trả lời", UNITS, "v1"), expected)

    def test_v2_is_v1_plus_the_rules(self):
        v1_lines, v2_lines = jd.JUDGE_SYSTEM.splitlines(), jd.JUDGE_SYSTEM_V2.splitlines()
        self.assertEqual([ln for ln in v2_lines if ln not in jd.GROUNDING_RULES.splitlines()], v1_lines)
        self.assertEqual(v2_lines[-1], v1_lines[-1])  # the output-format line stays last
        for definition in (ln for ln in v1_lines if ln.startswith('- "')):
            self.assertIn(definition, v2_lines)
        for fragment in ("KHÁC với căn cứ đúng", '"sai", kể cả khi kết luận đúng', 'tối đa "mot_phan"',
                         "không bị trừ", "câu ngoài phạm vi quy chế"):
            self.assertIn(fragment, jd.GROUNDING_RULES)
        self.assertEqual(jd.prompt_sha256("v1"), V1_SHA256)
        self.assertNotEqual(jd.prompt_sha256("v2"), V1_SHA256)

    def test_readable_source_conversion(self):
        self.assertEqual(jd.readable_source("qc3626.d43.k1"), "Điều 43 khoản 1")        # clause level
        self.assertEqual(jd.readable_source("qc3626.d16"), "Điều 16")                    # article level
        self.assertEqual(jd.readable_source("qc3626.d46.k1.đ"), "Điều 46 khoản 1 điểm đ")  # point
        with self.assertRaises(ValueError):
            jd.readable_source("qc3626.c1")

    def test_v2_message_has_the_grounds_line(self):
        message = jd.judge_prompt(QUESTION, "Trả lời", UNITS, "v2")
        self.assertIn("\n\nCăn cứ đúng: Điều 43 khoản 1; Điều 16; Điều 46 khoản 1 điểm đ\n\nNGUYÊN VĂN QUY CHẾ:", message)
        self.assertEqual(message.replace("Căn cứ đúng: Điều 43 khoản 1; Điều 16; Điều 46 khoản 1 điểm đ\n\n", ""),
                         legacy_message(QUESTION, "Trả lời", UNITS))
        # out_of_scope: no line, so the rules are skipped and the message equals v1's
        self.assertEqual(jd.judge_prompt(OUT_OF_SCOPE, "Trả lời", UNITS, "v2"), legacy_message(OUT_OF_SCOPE, "Trả lời", UNITS))

    def test_every_dev_source_id_is_convertible(self):
        for q in rm.load_dev_questions():
            for uid in q["source_ids"]:
                self.assertRegex(jd.readable_source(uid), r"^Điều \d+( khoản \d+)?$")  # dev ids are unit level

    def test_cache_key_differs_between_v1_and_v2(self):
        cfg = load_config().role("judge")
        keys = {}
        for version in ("v1", "v2"):
            for name, question in (("scoped", QUESTION), ("oos", OUT_OF_SCOPE)):
                messages = [{"role": "user", "content": jd.judge_prompt(question, "Trả lời", UNITS, version)}]
                keys[(version, name)] = core.cache_key(cfg, jd.JUDGE_SYSTEMS[version], messages, jd.VERDICT_SCHEMA, 0)
        self.assertEqual(len(set(keys.values())), 4)  # even the out_of_scope message (identical text) differs via the system prompt


class ManifestSelectionTests(unittest.TestCase):
    def test_manifest_selects_exactly_the_30_rows(self):
        selection = jd.load_selection(MANIFEST)
        self.assertEqual(len(selection), 30)
        kept = 0
        for system, expected in (("llm_only", 18), ("rag_baseline", 12)):
            path = RESULTS / f"2026-10-27_{system}_dev.csv"
            self.assertEqual(jd.system_of(path), system)
            rows = jd.select_rows(read_rows(path), system, selection)
            self.assertEqual(len(rows), expected)
            self.assertEqual({(system, r["id"], int(r["run"])) for r in rows}, {k for k in selection if k[0] == system})
            kept += len(rows)
        self.assertEqual(kept, 30)

    def test_missing_manifest_row_is_an_error(self):
        selection = jd.load_selection(MANIFEST)
        with self.assertRaises(ValueError):
            jd.select_rows([], "llm_only", selection)

    def test_output_names_never_hit_the_v1_files(self):
        path = RESULTS / "2026-10-27_llm_only_dev.csv"
        self.assertEqual([p.name for p in jd.output_paths(path, "v1", False)],
                         ["2026-10-27_llm_only_dev_judged.csv", "2026-10-27_llm_only_dev_judged.json"])
        self.assertEqual([p.name for p in jd.output_paths(path, "v2", True)],
                         ["2026-10-27_llm_only_dev_judged_v2_calib.csv", "2026-10-27_llm_only_dev_judged_v2_calib.json"])
        self.assertEqual(jd.output_paths(path, "v2", False)[0].name, "2026-10-27_llm_only_dev_judged_v2.csv")

    def test_system_name_must_come_from_the_file_name(self):
        with self.assertRaises(ValueError):
            jd.system_of(Path("results.csv"))


class FakeResult:
    parsed = {"label": "dung", "reason": "ok"}
    from_cache = False
    called_at = "2026-10-27T20:00:00+07:00"
    model_served = "z-ai/glm-5.3-flash"
    model_requested = "z-ai/glm-5.3-flash"


class MainWithFakeJudgeTests(unittest.TestCase):
    def run_main(self, argv, tmp):
        calls = []

        def fake_complete(role, system, messages, json_schema=None, run=0, **kwargs):
            calls.append((role, system, messages[0]["content"]))
            return FakeResult()

        with mock.patch.object(jd, "complete", fake_complete), mock.patch.object(jd, "MAX_JUDGE_CALLS_PER_MIN", 1e9), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return jd.main(argv), calls

    def test_v2_run_on_the_manifest_rows_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "2026-10-27_llm_only_dev.csv"
            shutil.copy(RESULTS / source.name, source)
            code, calls = self.run_main([str(source), "--judge-prompt", "v2", "--manifest", str(MANIFEST)], tmp)
            self.assertEqual(code, 0)
            self.assertEqual(len(calls), 18)
            self.assertTrue(all(c[0] == "judge" and c[1] == jd.JUDGE_SYSTEM_V2 for c in calls))
            self.assertTrue(any("Căn cứ đúng:" in c[2] for c in calls))
            rows = read_rows(Path(tmp) / "2026-10-27_llm_only_dev_judged_v2_calib.csv")
            self.assertEqual(len(rows), 18)
            self.assertEqual({r["judge_prompt"] for r in rows}, {"v2"})
            meta = json.loads((Path(tmp) / "2026-10-27_llm_only_dev_judged_v2_calib.json").read_text(encoding="utf-8"))
            self.assertEqual((meta["judge_prompt_version"], meta["judge_prompt_sha256"]), ("v2", jd.prompt_sha256("v2")))
            self.assertEqual(meta["judge_system_prompt"], jd.JUDGE_SYSTEM_V2)
            self.assertEqual(meta["manifest"], MANIFEST.name)
            self.assertFalse((Path(tmp) / "2026-10-27_llm_only_dev_judged.csv").exists())  # v1 names untouched

    def test_v1_default_run_keeps_the_old_output_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "2026-10-27_rag_baseline_dev.csv"
            rows = read_rows(RESULTS / source.name)[:2]
            with source.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            code, calls = self.run_main([str(source)], tmp)
            self.assertEqual(code, 0)
            self.assertTrue(all(c[1] == jd.JUDGE_SYSTEM for c in calls))
            self.assertTrue(all("Căn cứ đúng" not in c[2] for c in calls))
            out = read_rows(Path(tmp) / "2026-10-27_rag_baseline_dev_judged.csv")
            self.assertNotIn("judge_prompt", out[0])
            meta = json.loads((Path(tmp) / "2026-10-27_rag_baseline_dev_judged.json").read_text(encoding="utf-8"))
            self.assertNotIn("judge_prompt_version", meta)

    def test_bad_options_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_main(["x_dev.csv", "--judge-prompt", "v3"], tmp)[0], 2)
            self.assertEqual(self.run_main(["x_dev.csv", "--manifest"], tmp)[0], 2)


class AgreementJudgedOverrideTests(unittest.TestCase):
    MANIFEST = {"sources": {"llm_only": {"file": "unused"}, "rag_baseline": {"file": "unused"}}}

    def write_csv(self, tmp, name, rows):
        path = Path(tmp) / name
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_override_paths_and_prompt_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            llm = self.write_csv(tmp, "a.csv", [{"id": "q1", "run": "0", "answer": "x", "judge_label": "sai", "judge_prompt": "v2"}])
            rag = self.write_csv(tmp, "b.csv", [{"id": "q2", "run": "0", "answer": "y", "judge_label": "dung", "judge_prompt": "v2"}])
            judged = ja.load_judged(self.MANIFEST, {"llm_only": llm, "rag_baseline": rag})
        self.assertEqual({k: v["judge_label"] for k, v in judged.items()}, {("llm_only", "q1", 0): "sai", ("rag_baseline", "q2", 0): "dung"})

    def test_overrides_must_cover_every_system(self):
        with self.assertRaises(ValueError):
            ja.load_judged(self.MANIFEST, {"llm_only": Path("a.csv")})

    def test_join_reports_the_prompt_version(self):
        manifest = {"samples": {"1": {"system": "llm_only", "id": "q1", "run": 0}}}
        judged = {("llm_only", "q1", 0): {"answer": "x", "judge_label": "sai", "judge_reason": "r", "judge_model": "m", "judge_prompt": "v2"}}
        rows = [{"stt": "1", "cau_hoi": "Q?", "cau_tra_loi": "x", "nhan": "sai", "ghi_chu": None}]
        (sample,) = ja.join_samples(rows, manifest, judged, {"q1": {"id": "q1", "type": "single", "question": "Q?"}})
        self.assertEqual(sample["glm_prompt"], "v2")
        judged[("llm_only", "q1", 0)].pop("judge_prompt")  # a v1 csv has no such column
        (sample,) = ja.join_samples(rows, manifest, judged, {"q1": {"id": "q1", "type": "single", "question": "Q?"}})
        self.assertEqual(sample["glm_prompt"], "v1")

    def test_cli_rejects_partial_judged_and_bad_pairs(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(ja.main(["sheet.xlsx", "--judged", "no_equals_sign"]), 2)
            self.assertEqual(ja.main(["sheet.xlsx", "--judged", "llm_only=a.csv"]), 2)


if __name__ == "__main__":
    unittest.main()
