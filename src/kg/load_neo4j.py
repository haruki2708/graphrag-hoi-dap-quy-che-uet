"""Load data/processed/qc3626_graph.json into Neo4j (idempotent: MERGE on id) and build the vector index.

Run from the repo root, with Neo4j up (docker compose up -d) and NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD in .env:
    python src/kg/load_neo4j.py [--graph <json>] [--skip-embeddings]

Steps: unique-id constraint per label -> nodes -> edges -> embeddings of the DonViNoiDung units (bge-m3,
configs/retrieval.toml; src/retrieval/dense.py reuses data/cache/embeddings/ and needs CUDA) -> vector index
(1024 dimensions, cosine) on :DonViNoiDung(embedding). `doan_khong_so` (a list of maps) is stored as a JSON string.
The load functions take any object with run(query, parameters), so the tests use a recording fake session.
"""

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.kg import queries as q  # noqa: E402

GRAPH_PATH = REPO_ROOT / "data" / "processed" / "qc3626_graph.json"
BATCH_SIZE = 500
EMBEDDING_BATCH_SIZE = 32
CONTENT_LABEL = "DonViNoiDung"


def chunks(rows: list, size: int):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def check_label(label: str) -> str:
    """Labels and relationship types are put into the query text, so only known names are allowed."""
    if label not in q.NODE_LABELS and label not in ("THUOC", "DAN_CHIEU"):
        raise ValueError(f"unexpected label or type {label!r}")
    return label


def node_props(node: dict) -> dict:
    props = dict(node["props"])
    if "doan_khong_so" in props:
        props["doan_khong_so"] = json.dumps(props["doan_khong_so"], ensure_ascii=False)
    return props


def primary_label(node: dict) -> str:
    return node["labels"][0]


def node_statements(graph: dict) -> list[tuple[str, list[dict]]]:
    """One statement per distinct label set; the primary label is the first, the rest are added with SET."""
    groups: dict[tuple, list[dict]] = {}
    for node in graph["nodes"]:
        groups.setdefault(tuple(node["labels"]), []).append({"id": node["id"], "props": node_props(node)})
    out = []
    for labels, rows in groups.items():
        extra = "".join(f" SET n:{check_label(label)}" for label in labels[1:])
        out.append((q.MERGE_NODES.format(label=check_label(labels[0]), extra_labels=extra), rows))
    return out


def edge_statements(graph: dict) -> list[tuple[str, list[dict]]]:
    """One statement per (type, source label, target label), so every MATCH can use the id constraint."""
    label_of = {n["id"]: primary_label(n) for n in graph["nodes"]}
    groups: dict[tuple, list[dict]] = {}
    for e in graph["edges"]:
        key = (e["type"], label_of[e["from"]], label_of[e["to"]])
        groups.setdefault(key, []).append({"src": e["from"], "dst": e["to"], "props": e["props"]})
    return [(q.MERGE_EDGES.format(type=check_label(t), from_label=check_label(a), to_label=check_label(b)), rows)
            for (t, a, b), rows in groups.items()]


def create_constraints(session) -> list[str]:
    statements = [q.CREATE_CONSTRAINT.format(name=f"{label.lower()}_id_unique", label=check_label(label))
                  for label in q.NODE_LABELS]
    for statement in statements:
        session.run(statement, {})
    return statements


def load_structure(session, graph: dict) -> dict:
    """Constraints, nodes and edges. Returns what was sent (statement and row counts)."""
    constraints = create_constraints(session)
    sent_nodes = sent_edges = 0
    for query, rows in node_statements(graph):
        for batch in chunks(rows, BATCH_SIZE):
            session.run(query, {"rows": batch})
        sent_nodes += len(rows)
    for query, rows in edge_statements(graph):
        for batch in chunks(rows, BATCH_SIZE):
            session.run(query, {"rows": batch})
        sent_edges += len(rows)
    return {"constraints": len(constraints), "nodes_sent": sent_nodes, "edges_sent": sent_edges}


def load_embeddings(session, graph: dict, unit_ids: list[str], matrix) -> int:
    """Set n.embedding on the DonViNoiDung nodes; ids must match the graph's DonViNoiDung nodes exactly."""
    wanted = {n["id"] for n in graph["nodes"] if CONTENT_LABEL in n["labels"]}
    if set(unit_ids) != wanted or len(unit_ids) != len(wanted):
        raise ValueError("embedding ids differ from the DonViNoiDung nodes of the graph")
    if matrix.shape != (len(unit_ids), q.EMBEDDING_DIM):
        raise ValueError(f"embedding matrix is {matrix.shape}, expected ({len(unit_ids)}, {q.EMBEDDING_DIM})")
    rows = [{"id": uid, "embedding": [float(x) for x in vector]} for uid, vector in zip(unit_ids, matrix)]
    for batch in chunks(rows, EMBEDDING_BATCH_SIZE):
        session.run(q.SET_EMBEDDINGS, {"rows": batch})
    return len(rows)


def create_vector_index(session) -> None:
    session.run(q.CREATE_VECTOR_INDEX, {})
    session.run(q.AWAIT_VECTOR_INDEX, {})


def neo4j_driver():
    from dotenv import load_dotenv
    from neo4j import GraphDatabase

    load_dotenv(REPO_ROOT / ".env")
    missing = [v for v in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD") if not os.getenv(v)]
    if missing:
        raise RuntimeError(f"Thiếu biến trong .env: {', '.join(missing)}")
    return GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    skip_embeddings = "--skip-embeddings" in argv
    args = [a for a in argv if a != "--skip-embeddings"]
    graph_path = GRAPH_PATH
    if "--graph" in args:
        i = args.index("--graph")
        graph_path = Path(args[i + 1])
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    try:
        driver = neo4j_driver()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1
    with driver, driver.session() as session:
        summary = load_structure(session, graph)
        print(f"Đã gửi: {summary['constraints']} constraint, {summary['nodes_sent']} nút, {summary['edges_sent']} cạnh")
        if skip_embeddings:
            print("Bỏ qua embedding và vector index (--skip-embeddings).")
            return 0
        from src.retrieval.dense import DenseIndex

        index = DenseIndex.from_config()
        n = load_embeddings(session, graph, index.unit_ids, index.matrix)
        create_vector_index(session)
        print(f"Đã ghi {n} embedding (from_cache={index.from_cache}) và tạo vector index {q.VECTOR_INDEX_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
