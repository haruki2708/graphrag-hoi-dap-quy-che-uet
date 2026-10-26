"""Validate the question-set JSONL files (PLAN section 6; docs/schema_v1.md section 3).

Run from the repo root:
    python eval/validate_questions.py                                  # both v1 files
    python eval/validate_questions.py data/eval/questions_v1_dev.jsonl  # one file

Checks every record, then cross-file rules (unique ids, no generated question in test, test-dev
overlap). Prints counts, ids and unit codes only - never question or answer text (test-split rule).
Exits with code 1 if any error is found.
"""

import json
import re
import sys
from collections import Counter
from itertools import product
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROCESSED_JSON = REPO_ROOT / "data" / "processed" / "qc3626.json"
DEFAULT_FILES = [REPO_ROOT / "data" / "eval" / f"questions_v1_{s}.jsonl" for s in ("test", "dev")]

REQUIRED_KEYS = ("id", "question", "answer", "source_ids", "type", "split", "delegated", "origin", "ref_points")
TYPES = ("single", "multi", "conditional", "out_of_scope")
ORIGINS = ("manual", "generated")
MAX_SOURCES = 5
# Articles that are :DonViNoiDung units (S2 = B): articles with no clauses or with lead text, plus d35
ARTICLE_UNITS = frozenset(f"qc3626.d{n}" for n in (11, 12, 16, 19, 22, 30, 31, 35, 40, 42, 44, 49, 51))
JACCARD_THRESHOLD = 0.3

RE_POINT = re.compile(r"^qc3626\.d\d+\.k\d+\.[a-zđ]$")
RE_CLAUSE = re.compile(r"^qc3626\.d\d+\.k\d+$")
RE_ARTICLE = re.compile(r"^qc3626\.d\d+$")
RE_CHAPTER = re.compile(r"^qc3626\.c\d+$")


def load_unit_ids(path: Path = PROCESSED_JSON) -> set[str]:
    return {u["id"] for u in json.loads(path.read_text(encoding="utf-8"))["units"]}


def expected_split(path: Path) -> str | None:
    match = re.search(r"_(test|dev)\.jsonl$", path.name)
    return match.group(1) if match else None


def check_record(rec: dict, split: str | None, unit_ids: set[str]) -> list[str]:
    """Errors for one record; messages carry ids and codes only."""
    qid = rec.get("id", "?")
    errors = [f"{qid}: missing key {k!r}" for k in REQUIRED_KEYS if k not in rec]
    if errors:
        return errors
    if not str(rec["id"]).strip():
        errors.append("record with empty id")
    if not str(rec["question"]).strip() or not str(rec["answer"]).strip():
        errors.append(f"{qid}: empty question or answer")
    if rec["type"] not in TYPES:
        errors.append(f"{qid}: type {rec['type']!r} not in {TYPES}")
    if split is not None and rec["split"] != split:
        errors.append(f"{qid}: split {rec['split']!r} does not match file ({split})")
    if rec["origin"] not in ORIGINS:
        errors.append(f"{qid}: origin {rec['origin']!r} not in {ORIGINS}")
    if not isinstance(rec["delegated"], bool):
        errors.append(f"{qid}: delegated must be a bool")
    if rec["split"] == "test" and rec["origin"] == "generated":
        errors.append(f"{qid}: generated question in test")

    sources, points = rec["source_ids"], rec["ref_points"]
    if not isinstance(sources, list) or not isinstance(points, list):
        return errors + [f"{qid}: source_ids and ref_points must be lists"]
    for code in sources + points:
        if code not in unit_ids:
            errors.append(f"{qid}: unknown unit {code}")
    for code in sources:
        if RE_POINT.match(code):
            errors.append(f"{qid}: source_ids has point-level code {code} (use the clause)")
        elif RE_CHAPTER.match(code):
            errors.append(f"{qid}: source_ids has chapter code {code}")
        elif RE_ARTICLE.match(code) and code not in ARTICLE_UNITS:
            errors.append(f"{qid}: source_ids has article {code}, which is not a :DonViNoiDung unit")
    if len(sources) != len(set(sources)):
        errors.append(f"{qid}: duplicate codes in source_ids")
    if len(sources) > MAX_SOURCES:
        errors.append(f"{qid}: {len(sources)} source_ids (max {MAX_SOURCES})")
    if rec["type"] == "out_of_scope" and sources:
        errors.append(f"{qid}: out_of_scope question must have no source_ids")
    if rec["type"] != "out_of_scope" and not sources:
        errors.append(f"{qid}: needs at least one source_id")
    return errors


def tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def jaccard(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0


def cross_checks(by_split: dict[str, list[dict]]) -> tuple[list[str], dict]:
    """Unique ids across files, plus test-dev overlap (shared source codes, similar questions)."""
    errors = []
    id_counts = Counter(r["id"] for records in by_split.values() for r in records)
    errors += [f"{qid}: id used {n} times" for qid, n in sorted(id_counts.items()) if n > 1]

    overlap = {"shared_sources": {}, "similar_pairs": []}
    test, dev = by_split.get("test", []), by_split.get("dev", [])
    if test and dev:
        users = {}
        for r in test + dev:
            for code in r["source_ids"]:
                users.setdefault(code, {"test": [], "dev": []})[r["split"]].append(r["id"])
        overlap["shared_sources"] = {c: u for c, u in sorted(users.items()) if u["test"] and u["dev"]}
        for t, d in product(test, dev):
            score = jaccard(t["question"], d["question"])
            if score > JACCARD_THRESHOLD:
                overlap["similar_pairs"].append((t["id"], d["id"], round(score, 2)))
    return errors, overlap


def summary_lines(by_split: dict[str, list[dict]]) -> list[str]:
    lines = ["split | " + " | ".join(TYPES) + " | tổng | delegated | " + " | ".join(ORIGINS)]
    for split, records in by_split.items():
        types = Counter(r["type"] for r in records)
        origins = Counter(r["origin"] for r in records)
        delegated = sum(r["delegated"] is True for r in records)
        lines.append(f"{split} | " + " | ".join(str(types[t]) for t in TYPES) + f" | {len(records)} | {delegated} | "
                     + " | ".join(str(origins[o]) for o in ORIGINS))
        if "manual" in origins and "generated" in origins:
            manual = [r for r in records if r["origin"] == "manual"]
            mt = Counter(r["type"] for r in manual)
            lines.append(f"  {split} (manual) | " + " | ".join(str(mt[t]) for t in TYPES)
                         + f" | {len(manual)} | {sum(r['delegated'] is True for r in manual)}")
    return lines


def validate(files: list[Path], unit_ids: set[str]) -> tuple[list[str], dict, dict[str, list[dict]]]:
    errors, by_split = [], {}
    for path in files:
        split = expected_split(path)
        records = []
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                errors.append(f"{path.name}:{lineno}: invalid JSON ({e.msg})")
                continue
            if not isinstance(rec, dict):
                errors.append(f"{path.name}:{lineno}: not a JSON object")
                continue
            errors += [f"{path.name}: {msg}" for msg in check_record(rec, split, unit_ids)]
            records.append(rec)
        by_split[split or path.stem] = records
    cross_errors, overlap = cross_checks(by_split)
    return errors + cross_errors, overlap, by_split


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    files = [Path(a) for a in argv] or DEFAULT_FILES
    errors, overlap, by_split = validate(files, load_unit_ids())

    print("\n".join(summary_lines(by_split)))
    print()
    if "test" in by_split and "dev" in by_split:
        shared = overlap["shared_sources"]
        print(f"Mã source_ids dùng chung test–dev: {len(shared)}")
        for code, users in shared.items():
            print(f"  {code}: test {users['test']} | dev {users['dev']}")
        pairs = overlap["similar_pairs"]
        print(f"Cặp câu hỏi test–dev có Jaccard > {JACCARD_THRESHOLD}: {len(pairs)}")
        for t, d, score in pairs:
            print(f"  {t} ~ {d}: {score}")
        print()
    print(f"Lỗi: {len(errors)}")
    for msg in errors:
        print(f"  - {msg}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
