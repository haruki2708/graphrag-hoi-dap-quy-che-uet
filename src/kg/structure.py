"""Structure layer of the knowledge graph, built in memory from data/processed/qc3626.json (no Neo4j needed).

Run from the repo root:  python src/kg/structure.py

Writes data/processed/qc3626_graph.json: {"meta", "nodes", "edges"} (docs/schema_v1.md section 1):
- nodes: VanBan qc3626 + the named external VanBan (S5 = B), 9 Chuong, 51 Dieu, 179 Khoan, 262 Diem; the secondary
  label DonViNoiDung (S2 = B) on the 192 content units; doan_khong_so stays a property of the holding node;
- edges: THUOC (child -> parent, 501) and DAN_CHIEU (see src/kg/references.py).
Diem is its own node and never carries DonViNoiDung (S3 = A). CAN_CU is not built yet (needs the Decision page).
"""

import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.kg import references as refs  # noqa: E402

PROCESSED_JSON = REPO_ROOT / "data" / "processed" / "qc3626.json"
CONTENT_UNITS = REPO_ROOT / "data" / "processed" / "qc3626_content_units.jsonl"
GRAPH_PATH = REPO_ROOT / "data" / "processed" / "qc3626_graph.json"

LABEL_OF = {"chuong": "Chuong", "dieu": "Dieu", "khoan": "Khoan", "diem": "Diem"}
CONTENT_LABEL = "DonViNoiDung"
# Counts the parser and the schema promise for QĐ 3626 (docs/schema_v1.md; PLAN T3 "Xong khi")
EXPECTED = {
    "nodes": {"VanBan": 7, "Chuong": 9, "Dieu": 51, "Khoan": 179, "Diem": 262, CONTENT_LABEL: 192},
    "edges": {"THUOC": 501, "DAN_CHIEU_internal": 31, "DAN_CHIEU_external": 8},
}
# who_references() examples, derived by the project's reviewer from schema Appendix A (hand-check, T3 "Xong khi")
WHO_REFERENCES_EXAMPLES = {
    "qc3626.d22": {"qc3626.d29.k3.b", "qc3626.d46.k2"},
    "qc3626.d21.k1": {"qc3626.d21.k6", "qc3626.d23.k3.c"},
    "qc3626.d41.k2": {"qc3626.d39.k2", "qc3626.d41.k4"},
    "qc3626.d10.k2": {"qc3626.d10.k1", "qc3626.d50.k3.đ"},
    "qc3626.d36.k3": {"qc3626.d44.k2.đ"},
}


class StructureError(ValueError):
    pass


def load_units(path: Path = PROCESSED_JSON) -> tuple[dict, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["document"], data["units"]


def load_content_units(path: Path = CONTENT_UNITS) -> dict[str, dict]:
    with path.open(encoding="utf-8") as f:
        return {u["id"]: u for u in map(json.loads, (line for line in f if line.strip()))}


def expected_content_ids(units: list[dict]) -> set[str]:
    """Schema S2 = B derived from the structure alone: every Khoan, and every Dieu that has lead text, a child
    that is not a Khoan, or article-level doan_khong_so."""
    ids = set()
    children = {}
    for u in units:
        children.setdefault(u["parent_id"], []).append(u)
    for u in units:
        if u["type"] == "khoan":
            ids.add(u["id"])
        elif u["type"] == "dieu":
            lead = u["own_text"][len(f"Điều {u['number']}. {u['title']}"):].strip()
            non_khoan = [c for c in children.get(u["id"], []) if c["type"] != "khoan"]
            if lead or non_khoan or u.get("doan_khong_so"):
                ids.add(u["id"])
    return ids


def van_ban_node(document: dict, units: list[dict]) -> dict:
    match = re.search(r"Quyết định số (\S+) ngày (\d+) tháng (\d+) năm (\d+)", document["issued_by"])
    if not match or "Đại học Quốc gia Hà Nội" not in document["issued_by"]:
        raise StructureError(f"cannot read the decision number/date from issued_by: {document['issued_by']!r}")
    so_hieu, day, month, year = match.groups()
    d51 = next(u for u in units if u["id"] == f"{document['id']}.d51")
    from_year = re.search(r"khóa tuyển sinh từ năm (\d{4})", d51["own_text"])
    if not from_year:
        raise StructureError("d51 no longer says which intake the regulation applies from")
    return {"id": document["id"], "labels": ["VanBan"], "props": {
        "id": document["id"], "title": document["title"], "issued_by": document["issued_by"],
        "source_file": document["source_file"], "so_hieu": so_hieu,
        "ngay_ban_hanh": f"{int(year):04d}-{int(month):02d}-{int(day):02d}", "cap_ban_hanh": "dhqghn",
        "ap_dung_tu_khoa": int(from_year.group(1)), "trong_pham_vi": True}}


def build_graph(processed_json: Path = PROCESSED_JSON, content_units_path: Path = CONTENT_UNITS) -> dict:
    document, units = load_units(processed_json)
    content = load_content_units(content_units_path)
    content_ids = expected_content_ids(units)
    if set(content) != content_ids:
        raise StructureError(f"content units file differs from the structure rule: extra {sorted(set(content) - content_ids)}, "
                             f"missing {sorted(content_ids - set(content))}")
    ids = {u["id"] for u in units}
    nodes = [van_ban_node(document, units)]
    edges = []
    for u in units:
        props = {k: v for k, v in u.items() if k not in ("type", "parent_id") and v is not None}
        labels = [LABEL_OF[u["type"]]]
        if u["id"] in content_ids:
            labels.append(CONTENT_LABEL)
            unit = content[u["id"]]
            props.update(unit_text=unit["text"], unit_heading=unit["heading"],
                         unit_page_start=unit["page_start"], unit_page_end=unit["page_end"])
        nodes.append({"id": u["id"], "labels": labels, "props": props})
        if u["parent_id"] not in ids and u["parent_id"] != document["id"]:
            raise StructureError(f"{u['id']}: parent {u['parent_id']} does not exist")
        edges.append({"type": "THUOC", "from": u["id"], "to": u["parent_id"], "props": {}})

    references = refs.find_references(units)
    seen = set()
    for ref in references:
        if ref.kind != "internal":
            continue
        for target in ref.targets:
            if (ref.source, target) not in seen:  # one edge per (source, target) pair
                seen.add((ref.source, target))
                edges.append({"type": "DAN_CHIEU", "from": ref.source, "to": target, "props": {"text": ref.text}})
    mentions = refs.find_external_mentions(units)
    used_docs = {m.document_id for m in mentions}
    for doc in refs.EXTERNAL_DOCUMENTS:
        if doc.id in used_docs:
            nodes.append({"id": doc.id, "labels": ["VanBan"], "props": {"id": doc.id, "title": doc.title, "trong_pham_vi": False}})
    for m in mentions:
        edges.append({"type": "DAN_CHIEU", "from": m.source, "to": m.document_id, "props": {"text": m.text}})
    unresolved = refs.unresolved_external_phrases(references)
    if unresolved:
        raise StructureError("phrases pointing to a document that is not in EXTERNAL_DOCUMENTS: "
                             + "; ".join(f"{r.source}: {r.text!r}" for r in unresolved))

    graph = {"meta": {
        "document": document["id"], "schema": "docs/schema_v1.md",
        "sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (processed_json, content_units_path)},
    }, "nodes": nodes, "edges": edges}
    graph["meta"]["counts"] = count_graph(graph)
    return graph


def count_graph(graph: dict) -> dict:
    nodes = Counter(label for n in graph["nodes"] for label in n["labels"])
    internal = sum(1 for e in graph["edges"] if e["type"] == "DAN_CHIEU" and e["to"].startswith(f"{graph['meta']['document']}."))
    types = Counter(e["type"] for e in graph["edges"])
    return {"nodes": dict(nodes), "edges": {"THUOC": types["THUOC"], "DAN_CHIEU_internal": internal,
                                            "DAN_CHIEU_external": types["DAN_CHIEU"] - internal}}


def validate_graph(graph: dict, expected: dict = EXPECTED) -> list[str]:
    """Problems found (empty = fine): counts vs EXPECTED, dangling edge ends, DonViNoiDung on a Diem."""
    problems = []
    counts = count_graph(graph)
    for group in ("nodes", "edges"):
        for key, want in expected[group].items():
            if counts[group].get(key, 0) != want:
                problems.append(f"{group} {key}: {counts[group].get(key, 0)}, expected {want}")
    ids = {n["id"] for n in graph["nodes"]}
    problems += [f"edge {e['type']} {e['from']} -> {e['to']}: unknown end" for e in graph["edges"]
                 if e["from"] not in ids or e["to"] not in ids]
    problems += [f"{n['id']}: Diem must not be DonViNoiDung (S3 = A)" for n in graph["nodes"]
                 if "Diem" in n["labels"] and CONTENT_LABEL in n["labels"]]
    return problems


def who_references(graph: dict, target_id: str) -> set[str]:
    """Ids of the units that hold a DAN_CHIEU reference to exactly this target."""
    return {e["from"] for e in graph["edges"] if e["type"] == "DAN_CHIEU" and e["to"] == target_id}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    try:
        graph = build_graph()
    except (StructureError, refs.UnresolvedReference) as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 1
    problems = validate_graph(graph)
    counts = graph["meta"]["counts"]
    print("Nút theo nhãn:", ", ".join(f"{k} {v}" for k, v in counts["nodes"].items()))
    print("Cạnh:", ", ".join(f"{k} {v}" for k, v in counts["edges"].items()))
    failed = False
    for target, want in WHO_REFERENCES_EXAMPLES.items():
        got = who_references(graph, target)
        failed |= got != want
        print(f"  who_references({target.replace('qc3626.', '')}) = {sorted(s.replace('qc3626.', '') for s in got)} "
              f"{'khớp' if got == want else 'KHÁC mong đợi ' + str(sorted(want))}")
    if problems or failed:
        print("LỖI:\n" + "\n".join(f"  - {p}" for p in problems), file=sys.stderr)
        return 1
    GRAPH_PATH.write_text(json.dumps(graph, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Ghi {GRAPH_PATH.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
