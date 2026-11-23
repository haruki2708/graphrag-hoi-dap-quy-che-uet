"""Load the semantic layer (data/processed/semantic/combined_v1.json) into Neo4j (T4 PR 5b; schema section 2).

Run from the repo root, after  python src/kg/load_neo4j.py  (the structure layer must already be in Neo4j):
    python src/kg/load_semantic.py [--graph <json>] [--reset-semantic]

Order of work:
1. Checks that write nothing. File: known labels and edge types, edge ends exist, every node and edge has a nguon
   list of unit ids, no two edges share (from, type, to). Neo4j: 51 Dieu, and every nguon id of a node exists as a
   Dieu / Khoan / Diem (label from the id shape). Any failure stops the script before the first write.
2. --reset-semantic only: DETACH DELETE of the nodes that carry a semantic label; the structure layer is untouched.
3. One unique-id constraint per semantic label (same template as load_neo4j.py).
4. Nodes: MERGE on id with their label, SET n += props.
5. Semantic edges: MERGE on (from, type, to), SET r += props.
6. NGUON edges from each node to every unit of its nguon list.
7. Counts per label and per edge type read back from Neo4j and printed next to the file's counts.
Lists stay lists; a nested value (a map, or a list holding maps or lists) becomes a JSON string, like doan_khong_so.
A property whose value is null is not stored (Neo4j has no null properties): `boi_canh: null` means "no boi_canh".
MERGE everywhere, so running twice gives the same counts. The functions take any object with run(query, parameters)
returning rows, so the tests use a fake session.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.kg import queries as q  # noqa: E402
from src.kg import semantic_schema as ss  # noqa: E402
from src.kg.load_neo4j import BATCH_SIZE, check_label, chunks, neo4j_driver  # noqa: E402
from src.kg.structure import EXPECTED as STRUCTURE_EXPECTED  # noqa: E402

GRAPH_PATH = REPO_ROOT / "data" / "processed" / "semantic" / "combined_v1.json"
SEMANTIC_LABELS = ss.NODE_LABELS
SEMANTIC_EDGE_TYPES = ss.EDGE_TYPES
EXPECTED_DIEU = STRUCTURE_EXPECTED["nodes"]["Dieu"]
# Unit id shape -> structure label: qc3626.d17 / qc3626.d17.k2 / qc3626.d44.k1.a
UNIT_ID_PATTERNS = (
    ("Dieu", re.compile(r"^qc3626\.d\d+$")),
    ("Khoan", re.compile(r"^qc3626\.d\d+\.k\d+$")),
    ("Diem", re.compile(r"^qc3626\.d\d+\.k\d+\.[^.\s]+$")),
)


class LoadError(Exception):
    """Input or database state that the loader refuses to load (message lists the problems)."""


def check_name(name: str) -> str:
    """Labels and types are put into the query text: semantic names, NGUON, or what load_neo4j.check_label accepts."""
    if name in SEMANTIC_LABELS or name in SEMANTIC_EDGE_TYPES or name == q.SOURCE_EDGE:
        return name
    return check_label(name)


def unit_label(unit_id: str) -> str | None:
    """Structure label of a nguon id from its shape, or None if it is not a Dieu / Khoan / Diem id."""
    for label, pattern in UNIT_ID_PATTERNS:
        if pattern.match(unit_id):
            return label
    return None


def prop_value(value):
    if isinstance(value, dict) or (isinstance(value, list) and any(isinstance(x, (dict, list)) for x in value)):
        return json.dumps(value, ensure_ascii=False)
    return value


def plain_props(props: dict) -> dict:
    return {key: prop_value(value) for key, value in props.items()}


def _nguon(item: dict) -> list:
    value = item["props"].get("nguon")
    return value if isinstance(value, list) else []


# --- File checks ------------------------------------------------------------------------------------------------

def duplicate_triples(graph: dict) -> list[tuple[str, str, str]]:
    """(from, type, to) that appear on more than one edge; MERGE on that key would fold them into one."""
    counts = Counter((e["from"], e["type"], e["to"]) for e in graph["edges"])
    return sorted(key for key, n in counts.items() if n > 1)


def validate_file(graph: dict) -> list[str]:
    errors = []
    labels: dict[str, str] = {}
    for node in graph["nodes"]:
        gid = node["id"]
        if len(node["labels"]) != 1 or node["labels"][0] not in SEMANTIC_LABELS:
            errors.append(f"{gid}: labels {node['labels']} (expected exactly one of {list(SEMANTIC_LABELS)})")
        if gid in labels:
            errors.append(f"{gid}: duplicate node id")
        labels[gid] = node["labels"][0]
        nguon = _nguon(node)
        if not nguon:
            errors.append(f"{gid}: node has no nguon list")
        errors += [f"{gid}: nguon {u!r} is not a Dieu/Khoan/Diem id" for u in nguon if unit_label(u) is None]
    for e in graph["edges"]:
        where = f"edge {e['type']} {e['from']} -> {e['to']}"
        if e["type"] not in SEMANTIC_EDGE_TYPES:
            errors.append(f"{where}: unknown edge type")
        errors += [f"{where}: {end} node is not in the file" for end in ("from", "to") if e[end] not in labels]
        nguon = _nguon(e)
        if not nguon:
            errors.append(f"{where}: edge has no nguon list")
        errors += [f"{where}: nguon {u!r} is not a Dieu/Khoan/Diem id" for u in nguon if unit_label(u) is None]
    return errors


def expected_counts(graph: dict) -> dict:
    """What Neo4j should hold for this file: nodes per label, edges per type (NGUON = one per node and unit)."""
    edges = Counter(e["type"] for e in graph["edges"])
    edges[q.SOURCE_EDGE] = sum(len(set(_nguon(n))) for n in graph["nodes"])
    return {"nodes": dict(Counter(n["labels"][0] for n in graph["nodes"])), "edges": dict(edges)}


def nguon_targets(graph: dict) -> dict[str, list[str]]:
    """Every unit id named by a node's nguon, grouped by structure label."""
    out: dict[str, set] = {}
    for node in graph["nodes"]:
        for unit in _nguon(node):
            out.setdefault(unit_label(unit), set()).add(unit)
    return {label: sorted(ids) for label, ids in out.items()}


# --- Neo4j checks (read only) -----------------------------------------------------------------------------------

def count_dieu(session) -> int:
    return session.run(q.COUNT_DIEU, {}).single()["n"]


def missing_targets(session, graph: dict) -> list[str]:
    """nguon ids of the nodes that are not in Neo4j with the label their shape implies."""
    missing = []
    for label, ids in nguon_targets(graph).items():
        found = {r["id"] for r in session.run(q.FIND_UNITS.format(label=check_name(label)), {"ids": ids})}
        missing += [i for i in ids if i not in found]
    return sorted(missing)


def preflight(session, graph: dict) -> None:
    """Every check that must pass before anything is written; raises LoadError."""
    errors = validate_file(graph)
    if errors:
        raise LoadError(f"file không hợp lệ ({len(errors)} lỗi):\n  " + "\n  ".join(errors[:30]))
    dups = duplicate_triples(graph)
    if dups:
        raise LoadError(f"{len(dups)} bộ (from, type, to) xuất hiện ở nhiều cạnh; MERGE theo khóa này sẽ gộp chúng. "
                        "Cần chủ dự án chọn khóa cạnh trước khi nạp:\n  "
                        + "\n  ".join(f"{a} -[{t}]-> {b}" for a, t, b in dups[:30]))
    n_dieu = count_dieu(session)
    if n_dieu != EXPECTED_DIEU:
        raise LoadError(f"lớp cấu trúc chưa nạp: Neo4j có {n_dieu} nút :Dieu (cần {EXPECTED_DIEU}). "
                        "Chạy trước: python src/kg/load_neo4j.py")
    missing = missing_targets(session, graph)
    if missing:
        raise LoadError(f"{len(missing)} mã nguon không có trong Neo4j (Dieu/Khoan/Diem), không ghi gì:\n  "
                        + "\n  ".join(missing[:30]))


# --- Statements -------------------------------------------------------------------------------------------------

def node_statements(graph: dict) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = {}
    for node in graph["nodes"]:
        groups.setdefault(node["labels"][0], []).append({"id": node["id"], "props": plain_props(node["props"])})
    return [(q.MERGE_NODES.format(label=check_name(label), extra_labels=""), rows) for label, rows in groups.items()]


def edge_statements(graph: dict) -> list[tuple[str, list[dict]]]:
    """One statement per (type, source label, target label), so every MATCH can use the id constraint."""
    label_of = {n["id"]: n["labels"][0] for n in graph["nodes"]}
    groups: dict[tuple, list[dict]] = {}
    for e in graph["edges"]:
        key = (e["type"], label_of[e["from"]], label_of[e["to"]])
        groups.setdefault(key, []).append({"src": e["from"], "dst": e["to"], "props": plain_props(e["props"])})
    return [(q.MERGE_EDGES.format(type=check_name(t), from_label=check_name(a), to_label=check_name(b)), rows)
            for (t, a, b), rows in groups.items()]


def nguon_statements(graph: dict) -> list[tuple[str, list[dict]]]:
    """NGUON edges, one statement per (semantic label, unit label)."""
    groups: dict[tuple, list[dict]] = {}
    for node in graph["nodes"]:
        for unit in dict.fromkeys(_nguon(node)):
            key = (node["labels"][0], unit_label(unit))
            groups.setdefault(key, []).append({"src": node["id"], "dst": unit, "props": {}})
    return [(q.MERGE_EDGES.format(type=check_name(q.SOURCE_EDGE), from_label=check_name(a), to_label=check_name(b)),
             rows) for (a, b), rows in groups.items()]


def create_constraints(session) -> list[str]:
    statements = [q.CREATE_CONSTRAINT.format(name=f"{label.lower()}_id_unique", label=check_name(label))
                  for label in SEMANTIC_LABELS]
    for statement in statements:
        session.run(statement, {})
    return statements


def reset_semantic(session) -> int:
    """DETACH DELETE every node that carries a semantic label (and so its edges). Returns the number deleted."""
    return session.run(q.RESET_SEMANTIC, {"labels": list(SEMANTIC_LABELS)}).single()["n"]


def _send(session, statements: list[tuple[str, list[dict]]]) -> int:
    sent = 0
    for query, rows in statements:
        for batch in chunks(rows, BATCH_SIZE):
            session.run(query, {"rows": batch})
        sent += len(rows)
    return sent


def load_semantic(session, graph: dict, reset: bool = False) -> dict:
    """Preflight, optional reset, constraints, nodes, semantic edges, NGUON edges. Returns what was sent."""
    preflight(session, graph)
    deleted = reset_semantic(session) if reset else None
    constraints = create_constraints(session)
    return {"deleted": deleted, "constraints": len(constraints),
            "nodes_sent": _send(session, node_statements(graph)),
            "edges_sent": _send(session, edge_statements(graph)),
            "nguon_sent": _send(session, nguon_statements(graph))}


# --- Counts read back -------------------------------------------------------------------------------------------

def read_counts(session) -> dict:
    """Semantic node counts per label and semantic/NGUON edge counts per type, as stored in Neo4j."""
    labels = {r["label"]: r["n"] for r in session.run(q.COUNT_NODES_BY_LABEL, {})}
    types = {r["type"]: r["n"] for r in session.run(q.COUNT_EDGES_BY_TYPE, {})}
    return {"nodes": {k: labels[k] for k in SEMANTIC_LABELS if labels.get(k)},
            "edges": {k: types[k] for k in (*SEMANTIC_EDGE_TYPES, q.SOURCE_EDGE) if types.get(k)}}


def compare_counts(expected: dict, got: dict) -> list[tuple[str, str, int, int]]:
    """Rows (group, name, file, neo4j) for every label and type in either side, file order first."""
    rows = []
    for group, order in (("nodes", SEMANTIC_LABELS), ("edges", (*SEMANTIC_EDGE_TYPES, q.SOURCE_EDGE))):
        for name in order:
            if name in expected[group] or name in got[group]:
                rows.append((group, name, expected[group].get(name, 0), got[group].get(name, 0)))
    return rows


def print_counts(rows: list[tuple[str, str, int, int]]) -> bool:
    print(f"{'':<6}{'Nhãn / loại cạnh':<16}{'file':>8}{'Neo4j':>8}")
    totals = {"nodes": [0, 0], "edges": [0, 0]}
    for group, name, want, got in rows:
        print(f"{'nút' if group == 'nodes' else 'cạnh':<6}{name:<16}{want:>8}{got:>8}  {'ĐẠT' if want == got else 'LỆCH'}")
        totals[group][0] += want
        totals[group][1] += got
    for group, word in (("nodes", "TỔNG NÚT"), ("edges", "TỔNG CẠNH")):
        print(f"{'':<6}{word:<16}{totals[group][0]:>8}{totals[group][1]:>8}")
    return all(want == got for _, _, want, got in rows)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--graph", type=Path, default=GRAPH_PATH)
    parser.add_argument("--reset-semantic", action="store_true",
                        help="DETACH DELETE the semantic-label nodes first (structure layer untouched)")
    args = parser.parse_args(argv)
    graph = json.loads(args.graph.read_text(encoding="utf-8"))
    try:
        driver = neo4j_driver()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1
    with driver, driver.session() as session:
        try:
            summary = load_semantic(session, graph, reset=args.reset_semantic)
        except LoadError as e:
            print(f"LỖI: {e}", file=sys.stderr)
            return 1
        if summary["deleted"] is not None:
            print(f"--reset-semantic: đã xóa {summary['deleted']} nút ngữ nghĩa (kèm cạnh của chúng)")
        print(f"Đã gửi: {summary['constraints']} constraint, {summary['nodes_sent']} nút, "
              f"{summary['edges_sent']} cạnh ngữ nghĩa, {summary['nguon_sent']} cạnh NGUON "
              f"| cạnh trùng (from, type, to): 0, khóa cạnh = (from, type, to)")
        ok = print_counts(compare_counts(expected_counts(graph), read_counts(session)))
    print("KẾT QUẢ:", "ĐẠT (Neo4j khớp file)" if ok else "LỆCH so với file (chạy scripts/check_semantic.py để xem chi tiết)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
