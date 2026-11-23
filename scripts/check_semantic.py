"""Check the semantic layer in Neo4j against data/processed/semantic/combined_v1.json (T4 PR 5b).

Run from the repo root, after  python src/kg/load_semantic.py :
    python scripts/check_semantic.py [--graph <json>]

Checks: node counts per semantic label and edge counts per type (incl. NGUON) equal the file's; the set of semantic
node ids and of (from, type, to) edges equals the file's; every semantic node has NGUON edges to exactly the units of
its nguon list (so 0 node without NGUON); every NGUON edge goes from a semantic node to a Dieu/Khoan/Diem; 0 semantic
edge without a nguon list, and each edge's nguon equals the file's; the structure layer still has 51 Dieu and its
T3 node and edge counts. Exit code 0 only when every check passes.
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.kg import load_semantic as ls  # noqa: E402
from src.kg import queries as q  # noqa: E402
from src.kg.load_neo4j import neo4j_driver  # noqa: E402
from src.kg.structure import EXPECTED  # noqa: E402


def _sample(items) -> str:
    items = sorted(items)
    return ", ".join(map(str, items[:5])) + (f" … (+{len(items) - 5})" if len(items) > 5 else "")


def run_checks(session, graph: dict) -> list[tuple[bool, str]]:
    results = []

    def check(ok: bool, message: str) -> None:
        results.append((ok, message))

    labels_param = {"labels": list(ls.SEMANTIC_LABELS)}
    types_param = {"types": list(ls.SEMANTIC_EDGE_TYPES)}

    # Counts per label / type
    for group, name, want, got in ls.compare_counts(ls.expected_counts(graph), ls.read_counts(session)):
        check(want == got, f"{'nút' if group == 'nodes' else 'cạnh'} {name}: Neo4j {got} (file {want})")

    # Same node ids and labels
    file_nodes = {n["id"]: n["labels"][0] for n in graph["nodes"]}
    db_nodes = {r["id"]: r["labels"] for r in session.run(q.SEMANTIC_NODE_IDS, labels_param)}
    only_db, only_file = set(db_nodes) - set(file_nodes), set(file_nodes) - set(db_nodes)
    check(not only_db and not only_file,
          f"tập id nút ngữ nghĩa trùng file (chỉ trong Neo4j: {len(only_db)}, chỉ trong file: {len(only_file)})"
          + (f": {_sample(only_db | only_file)}" if only_db or only_file else ""))
    bad_labels = [i for i, labels in db_nodes.items() if i in file_nodes and labels != [file_nodes[i]]]
    check(not bad_labels, f"nút có đúng một nhãn như file: {len(bad_labels)} sai" + (f" ({_sample(bad_labels)})" if bad_labels else ""))

    # Same semantic edges, same nguon
    file_edges = {(e["from"], e["type"], e["to"]): sorted(e["props"]["nguon"]) for e in graph["edges"]}
    db_edges = {(r["src"], r["type"], r["dst"]): sorted(r["nguon"] or []) for r in session.run(q.SEMANTIC_EDGES, types_param)}
    only_db, only_file = set(db_edges) - set(file_edges), set(file_edges) - set(db_edges)
    check(not only_db and not only_file,
          f"tập cạnh ngữ nghĩa (from, type, to) trùng file (chỉ trong Neo4j: {len(only_db)}, chỉ trong file: {len(only_file)})"
          + (f": {_sample(only_db | only_file)}" if only_db or only_file else ""))
    diff_nguon = [k for k in set(db_edges) & set(file_edges) if db_edges[k] != file_edges[k]]
    check(not diff_nguon, f"nguon của cạnh khớp file: {len(diff_nguon)} lệch" + (f" ({_sample(diff_nguon)})" if diff_nguon else ""))

    # NGUON
    without = [r["id"] for r in session.run(q.SEMANTIC_NODES_WITHOUT_NGUON, labels_param)]
    check(not without, f"nút ngữ nghĩa thiếu NGUON: {len(without)}" + (f" ({_sample(without)})" if without else ""))
    mismatch = [r["id"] for r in session.run(q.SEMANTIC_NGUON_MISMATCH, labels_param)]
    check(not mismatch, f"nút có NGUON khác danh sách nguon: {len(mismatch)}" + (f" ({_sample(mismatch)})" if mismatch else ""))
    bad_ends = [(r["src"], r["dst"]) for r in session.run(q.NGUON_BAD_ENDS, labels_param)]
    check(not bad_ends, f"cạnh NGUON sai đầu (không từ nút ngữ nghĩa, hoặc không tới Dieu/Khoan/Diem): {len(bad_ends)}")
    no_nguon = [(r["src"], r["type"], r["dst"]) for r in session.run(q.SEMANTIC_EDGES_WITHOUT_NGUON, types_param)]
    check(not no_nguon, f"cạnh ngữ nghĩa thiếu thuộc tính nguon: {len(no_nguon)}" + (f" ({_sample(no_nguon)})" if no_nguon else ""))

    # Structure layer untouched
    n_dieu = session.run(q.COUNT_DIEU, {}).single()["n"]
    check(n_dieu == EXPECTED["nodes"]["Dieu"], f"lớp cấu trúc: Dieu {n_dieu} (mong đợi {EXPECTED['nodes']['Dieu']})")
    all_labels = {r["label"]: r["n"] for r in session.run(q.COUNT_NODES_BY_LABEL, {})}
    for label, want in EXPECTED["nodes"].items():
        if label != "Dieu":
            check(all_labels.get(label, 0) == want, f"lớp cấu trúc: {label} {all_labels.get(label, 0)} (mong đợi {want})")
    all_types = {r["type"]: r["n"] for r in session.run(q.COUNT_EDGES_BY_TYPE, {})}
    want_dan_chieu = EXPECTED["edges"]["DAN_CHIEU_internal"] + EXPECTED["edges"]["DAN_CHIEU_external"]
    check(all_types.get("THUOC", 0) == EXPECTED["edges"]["THUOC"],
          f"lớp cấu trúc: THUOC {all_types.get('THUOC', 0)} (mong đợi {EXPECTED['edges']['THUOC']})")
    check(all_types.get("DAN_CHIEU", 0) == want_dan_chieu,
          f"lớp cấu trúc: DAN_CHIEU {all_types.get('DAN_CHIEU', 0)} (mong đợi {want_dan_chieu})")
    return results


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--graph", type=Path, default=ls.GRAPH_PATH)
    args = parser.parse_args(argv)
    graph = json.loads(args.graph.read_text(encoding="utf-8"))
    try:
        driver = neo4j_driver()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1
    with driver, driver.session() as session:
        results = run_checks(session, graph)
    for ok, message in results:
        print(f"{'ĐẠT ' if ok else 'LỖI '} {message}")
    passed = all(ok for ok, _ in results)
    print("KẾT QUẢ:", "ĐẠT" if passed else "KHÔNG ĐẠT")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
