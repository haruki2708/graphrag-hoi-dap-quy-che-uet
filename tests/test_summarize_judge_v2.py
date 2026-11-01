"""Tests for eval/summarize.py --judge-prompt v1|v2 (no API; v2 inputs are small fixtures built in a temp dir).
Run from the repo root:  python -m unittest discover -s tests -t .
"""

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from eval import summarize as sm

DATE = "2026-10-27"
RESULTS = sm.REPO_ROOT / "eval" / "results"
HASH = "4e4d7b55a6b0ee15e5fdb043413cdc87696b69137ff953a3f1c82e82aeba2912"


def read_rows(path):
    with Path(path).open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def make_v2_fixture(tmp: Path, label="sai", hashes=None, prompt_version="v2", with_agreement=True) -> Path:
    """judged_v2 csv/json for both systems: the first 3 questions of the real v1 files, every label set to `label`."""
    hashes = hashes or {}
    for system in sm.SYSTEMS:
        rows = [r for r in read_rows(RESULTS / f"{DATE}_{system}_dev_judged.csv") if r["id"] in ("dev-m001", "dev-m017", "dev-m025")]
        for r in rows:
            r["judge_label"], r["judge_prompt"] = label, "v2"
        with (tmp / f"{DATE}_{system}_dev_judged_v2.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        (tmp / f"{DATE}_{system}_dev_judged_v2.json").write_text(json.dumps({
            "judge_prompt_version": prompt_version, "judge_prompt_sha256": hashes.get(system, HASH)}), encoding="utf-8")
        (tmp / f"{DATE}_{system}_dev.json").write_text(json.dumps({"cost_usd": 0.01}), encoding="utf-8")
    agreement = tmp / "agreement_v2.json"
    if with_agreement:
        agreement.write_text((RESULTS / "2026-11-01_judge_agreement_v2.json").read_text(encoding="utf-8"), encoding="utf-8")
    return agreement


class V1UnchangedTests(unittest.TestCase):
    def test_v1_summary_equals_the_committed_one(self):
        committed = json.loads((RESULTS / f"{DATE}_summary_dev.json").read_text(encoding="utf-8"))
        rebuilt = json.loads(json.dumps(sm.build_summary(DATE)))  # the same round trip the file went through
        self.assertEqual(rebuilt, committed)
        self.assertFalse(rebuilt["judge_calibrated"])
        for key in ("judge_prompt_version", "judge_prompt_sha256", "calibration"):
            self.assertNotIn(key, rebuilt)

    def test_v1_is_the_default_and_writes_the_old_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            for system in sm.SYSTEMS:
                for suffix in (".json", "_judged.csv"):
                    (Path(tmp) / f"{DATE}_{system}_dev{suffix}").write_bytes((RESULTS / f"{DATE}_{system}_dev{suffix}").read_bytes())
            with mock.patch.object(sm, "RESULTS_DIR", Path(tmp)), redirect_stdout(io.StringIO()):
                self.assertEqual(sm.main(["--date", DATE]), 0)
            written = json.loads((Path(tmp) / f"{DATE}_summary_dev.json").read_text(encoding="utf-8"))
            self.assertEqual(written, json.loads((RESULTS / f"{DATE}_summary_dev.json").read_text(encoding="utf-8")))
            self.assertFalse((Path(tmp) / f"{DATE}_summary_dev_judge_v2.json").exists())


class V2Tests(unittest.TestCase):
    def test_v2_reads_the_judged_v2_files_and_records_the_judge(self):
        with tempfile.TemporaryDirectory() as tmp:
            agreement = make_v2_fixture(Path(tmp), label="sai")
            summary = sm.build_summary(DATE, "v2", Path(tmp), agreement)
        self.assertEqual(summary["judge_prompt_version"], "v2")
        self.assertEqual(summary["judge_prompt_sha256"], HASH)
        self.assertTrue(summary["judge_calibrated"])
        cal = summary["calibration"]
        self.assertEqual((cal["n"], cal["agree"], cal["agreement"], cal["kappa"]), (30, 28, 0.9333, 0.8983))
        self.assertEqual(cal["agreement_file"], str(agreement))  # outside the repo: shown as given
        self.assertIn("lạc quan", cal["note"])
        self.assertIn("kiểm thêm khoảng 10 mẫu mới ở T5", cal["note"])
        # all labels were set to "sai" in the v2 fixture, so the scores prove the v2 file was read (v1 has "dung")
        for system in sm.SYSTEMS:
            self.assertEqual(summary["systems"][system]["groups"]["total"]["judge_score"]["mean"], 0.0)

    def test_agreement_path_inside_the_repo_is_recorded_relative(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_v2_fixture(Path(tmp))
            summary = sm.build_summary(DATE, "v2", Path(tmp), sm.AGREEMENT_V2)
        self.assertEqual(summary["calibration"]["agreement_file"], "eval/results/2026-11-01_judge_agreement_v2.json")

    def test_main_writes_the_v2_file_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            agreement = make_v2_fixture(Path(tmp))
            out = io.StringIO()
            with mock.patch.object(sm, "RESULTS_DIR", Path(tmp)), redirect_stdout(out):
                self.assertEqual(sm.main(["--date", DATE, "--judge-prompt", "v2", "--agreement", str(agreement)]), 0)
            self.assertTrue((Path(tmp) / f"{DATE}_summary_dev_judge_v2.json").exists())
            self.assertFalse((Path(tmp) / f"{DATE}_summary_dev.json").exists())
            self.assertIn("prompt v2", out.getvalue())

    def test_missing_v2_csv_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            agreement = make_v2_fixture(Path(tmp))
            (Path(tmp) / f"{DATE}_rag_baseline_dev_judged_v2.csv").unlink()
            with self.assertRaises(sm.MissingInput) as ctx:
                sm.build_summary(DATE, "v2", Path(tmp), agreement)
            self.assertIn(f"{DATE}_rag_baseline_dev_judged_v2.csv", str(ctx.exception))
            self.assertIn("--judge-prompt v2", str(ctx.exception))
            err = io.StringIO()
            with mock.patch.object(sm, "RESULTS_DIR", Path(tmp)), redirect_stderr(err):
                self.assertEqual(sm.main(["--date", DATE, "--judge-prompt", "v2", "--agreement", str(agreement)]), 2)
            self.assertIn("LỖI: thiếu file", err.getvalue())
            self.assertFalse((Path(tmp) / f"{DATE}_summary_dev_judge_v2.json").exists())

    def test_v2_never_falls_back_to_the_v1_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            agreement = make_v2_fixture(Path(tmp))
            (Path(tmp) / f"{DATE}_llm_only_dev_judged_v2.csv").unlink()
            (Path(tmp) / f"{DATE}_llm_only_dev_judged.csv").write_bytes((RESULTS / f"{DATE}_llm_only_dev_judged.csv").read_bytes())
            with self.assertRaises(sm.MissingInput):
                sm.build_summary(DATE, "v2", Path(tmp), agreement)

    def test_other_inconsistencies_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            agreement = make_v2_fixture(Path(tmp), hashes={"rag_baseline": "0" * 64})
            with self.assertRaisesRegex(sm.MissingInput, "hash prompt"):
                sm.build_summary(DATE, "v2", Path(tmp), agreement)
        with tempfile.TemporaryDirectory() as tmp:
            agreement = make_v2_fixture(Path(tmp), prompt_version="v1")
            with self.assertRaisesRegex(sm.MissingInput, "không phải kết quả chấm prompt v2"):
                sm.build_summary(DATE, "v2", Path(tmp), agreement)
        with tempfile.TemporaryDirectory() as tmp:
            make_v2_fixture(Path(tmp), with_agreement=False)
            with self.assertRaisesRegex(sm.MissingInput, "thiếu file hiệu chỉnh"):
                sm.build_summary(DATE, "v2", Path(tmp), Path(tmp) / "agreement_v2.json")
        with tempfile.TemporaryDirectory() as tmp:  # an agreement file of the v1 prompt cannot back a v2 summary
            make_v2_fixture(Path(tmp))
            v1_agreement = RESULTS / "2026-10-27_judge_agreement_v1.json"
            with self.assertRaisesRegex(sm.MissingInput, "không phải hiệu chỉnh của prompt v2"):
                sm.build_summary(DATE, "v2", Path(tmp), v1_agreement)


if __name__ == "__main__":
    unittest.main()
