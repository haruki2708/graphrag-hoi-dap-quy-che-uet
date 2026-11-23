"""Run test question 7 (schema 2.4: training year from d17.k2, then the academic-warning threshold of d44.k1.a).

Run from the repo root, after  python src/kg/load_semantic.py :
    python scripts/check_query7.py [--diagnose]

Two cases of a student of "chương trình đào tạo chất lượng cao" (the node's real ten; schema 2.4 writes
"CTĐT chất lượng cao"). A case passes when one returned row has the expected year, thu_tu, threshold and
consequence, and the sources include d17.k2 and d44.k1 (or a unit inside it). When a case fails (or with --diagnose)
the cumulative steps of queries.QUERY7_STEPS are run one by one, to show where the chain breaks: a node that is not
found, a missing edge, or a WHERE condition that drops every row. Nothing is written. Exit code 0 only if both pass.
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.kg import queries as q  # noqa: E402
from src.kg.load_neo4j import neo4j_driver  # noqa: E402

PROGRAMME = "chương trình đào tạo chất lượng cao"
CONSEQUENCE = "bị cảnh báo học vụ"
CASES = (
    {"tc": 50, "tbc": 1.35, "nam": "năm thứ hai", "thu_tu": 2, "nguong": 1.40},
    {"tc": 130, "tbc": 1.75, "nam": "năm thứ tư", "thu_tu": 4, "nguong": 1.80},
)
SOURCE_1 = "qc3626.d17.k2"
SOURCE_2 = "qc3626.d44.k1"
COLUMNS = ("nam", "thu_tu", "cn_min", "cn_max", "nguong", "he_qua", "giao_quyen", "giao_cho", "nguon_1", "nguon_2")


def _in_unit(unit_id: str | None, parent: str) -> bool:
    return unit_id is not None and (unit_id == parent or unit_id.startswith(parent + "."))


def judge(case: dict, rows: list[dict]) -> tuple[bool, list[str]]:
    """(passed, reasons it failed)."""
    if not rows:
        return False, ["truy vấn trả về 0 dòng"]
    hits = [r for r in rows if r["nam"] == case["nam"] and r["thu_tu"] == case["thu_tu"]
            and r["nguong"] is not None and abs(r["nguong"] - case["nguong"]) < 1e-9 and r["he_qua"] == CONSEQUENCE]
    reasons = []
    if not hits:
        reasons.append(f"không dòng nào có nam={case['nam']!r}, thu_tu={case['thu_tu']}, nguong={case['nguong']}, "
                       f"he_qua={CONSEQUENCE!r}")
    elif not any(r["nguon_1"] == SOURCE_1 for r in hits):
        reasons.append(f"nguồn bước 1 không có {SOURCE_1} (có: {sorted({r['nguon_1'] for r in hits})})")
    elif not any(_in_unit(r["nguon_2"], SOURCE_2) for r in hits):
        reasons.append(f"nguồn bước 2 không nằm trong {SOURCE_2} (có: {sorted({r['nguon_2'] for r in hits})})")
    return not reasons, reasons


def run_case(session, case: dict) -> list[dict]:
    params = {"tc": case["tc"], "tbc": case["tbc"], "ct": PROGRAMME}
    rows = [dict(r) for r in session.run(q.QUERY7, params)]
    return [dict(t) for t in dict.fromkeys(tuple((c, r[c]) for c in COLUMNS) for r in rows)]


def diagnose(session, case: dict) -> None:
    params = {"tc": case["tc"], "tbc": case["tbc"], "ct": PROGRAMME}
    print("  Chạy từng bước (tích lũy dần):")
    for name, query in q.QUERY7_STEPS:
        record = session.run(query, params).single()
        print(f"    {record['n']:>5}  {name}  | mẫu: {record['mau']}")


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--diagnose", action="store_true", help="run the step-by-step queries even when a case passes")
    args = parser.parse_args(argv)
    try:
        driver = neo4j_driver()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1
    passed_all = True
    with driver, driver.session() as session:
        for case in CASES:
            print(f"Ca tc={case['tc']}, tbc={case['tbc']}, ct={PROGRAMME!r} "
                  f"(mong đợi: {case['nam']}, ngưỡng {case['nguong']}, {CONSEQUENCE}, nguồn {SOURCE_1} + {SOURCE_2}…)")
            rows = run_case(session, case)
            print(f"  {len(rows)} dòng khác nhau:")
            for r in rows:
                print("   ", " | ".join(f"{c}={r[c]}" for c in COLUMNS))
            ok, reasons = judge(case, rows)
            print("  ĐẠT" if ok else "  KHÔNG ĐẠT: " + "; ".join(reasons))
            passed_all &= ok
            if not ok or args.diagnose:
                diagnose(session, case)
    print("KẾT QUẢ:", "ĐẠT" if passed_all else "KHÔNG ĐẠT")
    return 0 if passed_all else 1


if __name__ == "__main__":
    sys.exit(main())
