"""Check the structure layer in Neo4j against the T3 "Xong khi" criteria (run on the machine that runs Docker).

Run from the repo root, after  python src/kg/load_neo4j.py :
    python scripts/check_structure.py

Checks: 51 Dieu; node counts per label (incl. 192 DonViNoiDung); THUOC 501; DAN_CHIEU 31 internal + 8 external;
the 5 "which units reference X" examples (src/kg/structure.py WHO_REFERENCES_EXAMPLES); 192 embeddings;
the vector index is ONLINE. Exit code 0 only when every check passes.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.kg import queries as q  # noqa: E402
from src.kg.load_neo4j import neo4j_driver  # noqa: E402
from src.kg.structure import EXPECTED, WHO_REFERENCES_EXAMPLES  # noqa: E402


def run_checks(session) -> list[tuple[bool, str]]:
    results = []

    def check(ok: bool, message: str) -> None:
        results.append((ok, message))

    n_dieu = session.run(q.COUNT_DIEU).single()["n"]
    check(n_dieu == 51, f"Dieu: {n_dieu} (mong đợi 51)")
    labels = {r["label"]: r["n"] for r in session.run(q.COUNT_NODES_BY_LABEL)}
    for label, want in EXPECTED["nodes"].items():
        check(labels.get(label, 0) == want, f"nút {label}: {labels.get(label, 0)} (mong đợi {want})")
    types = {r["type"]: r["n"] for r in session.run(q.COUNT_EDGES_BY_TYPE)}
    internal = session.run(q.COUNT_DAN_CHIEU_INTERNAL).single()["n"]
    external = types.get("DAN_CHIEU", 0) - internal
    check(types.get("THUOC", 0) == EXPECTED["edges"]["THUOC"], f"THUOC: {types.get('THUOC', 0)} (mong đợi {EXPECTED['edges']['THUOC']})")
    check(internal == EXPECTED["edges"]["DAN_CHIEU_internal"], f"DAN_CHIEU nội bộ: {internal} (mong đợi {EXPECTED['edges']['DAN_CHIEU_internal']})")
    check(external == EXPECTED["edges"]["DAN_CHIEU_external"], f"DAN_CHIEU ra ngoài: {external} (mong đợi {EXPECTED['edges']['DAN_CHIEU_external']})")
    for target, want in WHO_REFERENCES_EXAMPLES.items():
        got = {r["source"] for r in session.run(q.WHO_REFERENCES, {"target": target})}
        check(got == want, f"ai dẫn chiếu {target}: {sorted(got)} (mong đợi {sorted(want)})")
    embedded = session.run(q.COUNT_EMBEDDED).single()["n"]
    check(embedded == EXPECTED["nodes"]["DonViNoiDung"], f"DonViNoiDung có embedding: {embedded} (mong đợi {EXPECTED['nodes']['DonViNoiDung']})")
    indexes = {r["name"]: r["state"] for r in session.run(q.SHOW_VECTOR_INDEXES)}
    check(indexes.get(q.VECTOR_INDEX_NAME) == "ONLINE", f"vector index {q.VECTOR_INDEX_NAME}: {indexes.get(q.VECTOR_INDEX_NAME, 'không có')}")
    return results


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        driver = neo4j_driver()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1
    with driver, driver.session() as session:
        results = run_checks(session)
    for ok, message in results:
        print(f"{'ĐẠT ' if ok else 'LỖI '} {message}")
    passed = all(ok for ok, _ in results)
    print("KẾT QUẢ:", "ĐẠT" if passed else "KHÔNG ĐẠT")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
