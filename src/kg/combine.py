"""Combine every semantic fragment into one graph (T4 PR 5a; schema Q6 = A + B).

Run from the repo root:
    python src/kg/combine.py [--out data/processed/semantic/combined_v1.json]

Inputs (read only, no LLM call):
- data/processed/semantic/extract/<unit>.json: one record per content unit of the Q9 scope. The run-0 record is used
  when its status is "ok"; only when it is not, <unit>.run1.json is used. Every other run-1 file is ignored. All
  units must end up "ok", otherwise the script stops.
- data/processed/semantic/bang/*.json: the six classification-table fragments (nguon_loai = bang).
- data/manual/gop_thuc_the_v1.csv (optional): the entity merges approved by the project owner (columns label, id_giu,
  id_bo, quyet_dinh, ghi_chu). Only rows with quyet_dinh = gop are applied. This file is the ONLY thing that merges
  two different ids; the candidates in gop_ung_vien_v1.csv are suggestions (src/kg/merge_candidates.py).

Output: one {"meta", "nodes", "edges"} graph like qc3626_graph.json. Nodes with the same id are joined: nguon, bi_danh
and nguon_loai become lists; when a property differs, the value from a table wins, else the first one seen, and every
difference is listed in meta.conflicts. Edges that are equal except for nguon are joined (nguon becomes a list).
NGUON edges to Khoan/Diem/Dieu are not created here: the nguon lists carry them for the Neo4j loader.
"""

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.kg import extract as ex  # noqa: E402
from src.kg import semantic_ids as sid  # noqa: E402
from src.kg import semantic_schema as ss  # noqa: E402

SEMANTIC_DIR = REPO_ROOT / "data" / "processed" / "semantic"
EXTRACT_DIR = SEMANTIC_DIR / "extract"
BANG_DIR = SEMANTIC_DIR / "bang"
OUT_PATH = SEMANTIC_DIR / "combined_v1.json"
MERGE_LIST = REPO_ROOT / "data" / "manual" / "gop_thuc_the_v1.csv"
MERGE_FIELDS = ("label", "id_giu", "id_bo", "quyet_dinh", "ghi_chu")
EXTRACT_ROLE = "extract"
# Node keys that are not copied as plain properties
CORE_KEYS = ("id", "ten", "bi_danh", "nguon", "nguon_loai")


class CombineError(Exception):
    """Input data that the script refuses to combine (message lists every problem found)."""


def _dumps(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _unique(items) -> list:
    return list(dict.fromkeys(items))


def _as_list(value) -> list:
    return list(value) if isinstance(value, (list, tuple)) else [value]


# --- Choosing the records ---------------------------------------------------------------------------------------

def _read_record(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def select_records(unit_ids: list[str], extract_dir: Path = EXTRACT_DIR, role: str = EXTRACT_ROLE) -> tuple[list[dict], dict]:
    """The record to use for each unit: run 0 if ok, else run 1 if ok, else stop. Returns (items, summary).

    items: [{"source": "extract:<unit>", "kind": "trich_xuat", "run": 0|1, "fragment": {...}}] in unit_ids order.
    Run-1 files of units whose run 0 is ok are never opened for use.
    """
    items, runs, failed = [], {}, {}
    for unit_id in unit_ids:
        notes = []
        for run in (0, 1):
            record = _read_record(ex.record_path(extract_dir.parent, extract_dir.name, unit_id, run))
            if record is None:
                notes.append(f"run {run}: no file")
            elif record.get("status") == "ok" and record.get("fragment"):
                runs[unit_id] = run
                items.append({"source": f"extract:{unit_id}", "kind": "trich_xuat", "run": run,
                              "fragment": record["fragment"]})
                break
            else:
                notes.append(f"run {run}: status {record.get('status')!r}")
        else:
            failed[unit_id] = "; ".join(notes)
    if failed:
        raise CombineError(f"{len(failed)}/{len(unit_ids)} units have no ok record (run 0, then run 1):\n  "
                           + "\n  ".join(f"{u}: {why}" for u, why in failed.items()))
    by_run = Counter(runs.values())
    summary = {"n_units": len(unit_ids), "from_run0": by_run[0], "from_run1": by_run[1],
               "run1_units": [u for u, r in runs.items() if r == 1]}
    return items, summary


def load_table_fragments(bang_dir: Path = BANG_DIR) -> list[dict]:
    items = []
    for path in sorted(bang_dir.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("status") != "ok" or not record.get("fragment"):
            raise CombineError(f"{path.name}: table record is not ok (status {record.get('status')!r})")
        fragment = record["fragment"]
        if fragment["meta"].get("nguon_loai") != "bang":
            raise CombineError(f"{path.name}: nguon_loai is {fragment['meta'].get('nguon_loai')!r}, expected 'bang'")
        items.append({"source": f"bang:{record['unit']}", "kind": "bang", "run": 0, "fragment": fragment})
    return items


# --- Combining fragments ----------------------------------------------------------------------------------------

def _choose(cands: list[tuple]) -> tuple[tuple, list[tuple]]:
    """cands: [(value, source, is_bang)] in the order seen. The first table value wins, else the first one seen.
    Returns (chosen, the candidates whose value differs from the chosen one, one per distinct value)."""
    chosen = next((c for c in cands if c[2]), cands[0])
    distinct = {}
    for c in cands:
        distinct.setdefault(_dumps(c[0]), c)
    return chosen, [c for key, c in distinct.items() if key != _dumps(chosen[0])]


def _conflict(gid: str, prop: str, chosen: tuple, dropped: list[tuple], phase: str = "combine") -> dict:
    return {"id": gid, "property": prop, "kept": chosen[0], "kept_from": chosen[1],
            "dropped": [{"value": d[0], "from": d[1]} for d in dropped], "phase": phase}


class _Node:
    def __init__(self, gid: str, label: str):
        self.gid, self.label = gid, label
        self.ten, self.bi_danh, self.nguon, self.nguon_loai = [], [], [], []
        self.props: dict[str, list[tuple]] = {}

    def add(self, props: dict, source: str, is_bang: bool) -> None:
        self.ten.append((props["ten"], source, is_bang))
        self.bi_danh += props.get("bi_danh", [])
        self.nguon += _as_list(props["nguon"])
        self.nguon_loai += _as_list(props.get("nguon_loai", []))
        for key, value in props.items():
            if key not in CORE_KEYS:
                self.props.setdefault(key, []).append((value, source, is_bang))

    def finish(self, conflicts: list[dict]) -> dict:
        ten, _ = _choose(self.ten)
        aliases = [t[0] for t in self.ten if t[0] != ten[0]] + self.bi_danh
        out = {"id": self.gid, "ten": ten[0], "bi_danh": [a for a in _unique(aliases) if a != ten[0]],
               "nguon_loai": _unique(self.nguon_loai), "nguon": _unique(self.nguon)}
        for key, cands in self.props.items():
            chosen, dropped = _choose(cands)
            out[key] = chosen[0]
            if dropped:
                conflicts.append(_conflict(self.gid, key, chosen, dropped))
        return {"id": self.gid, "labels": [self.label], "props": out}


def _edge_key(edge: dict) -> str:
    props = {k: v for k, v in edge["props"].items() if k != "nguon"}
    return _dumps([edge["type"], edge["from"], edge["to"], props])


def merge_edges(edges: list[dict]) -> list[dict]:
    """Join edges that are equal except for nguon; nguon becomes a list (order of first appearance)."""
    joined: dict[str, dict] = {}
    for edge in edges:
        key = _edge_key(edge)
        if key not in joined:
            props = {k: v for k, v in edge["props"].items() if k != "nguon"}
            joined[key] = {"type": edge["type"], "from": edge["from"], "to": edge["to"],
                           "props": {**props, "nguon": []}}
        joined[key]["props"]["nguon"] = _unique(joined[key]["props"]["nguon"] + _as_list(edge["props"]["nguon"]))
    return list(joined.values())


def combine_fragments(items: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Join the fragments. Tables are read first, so a table value wins over an LLM value and the order of the rest is
    that of `items`. Returns (nodes, edges, conflicts)."""
    ordered = sorted(items, key=lambda i: i["kind"] != "bang")  # stable: tables first
    accs: dict[str, _Node] = {}
    scoped_owner: dict[str, str] = {}
    all_edges = []
    for item in ordered:
        source, is_bang = item["source"], item["kind"] == "bang"
        for node in item["fragment"]["nodes"]:
            gid, label = node["id"], node["labels"][0]
            if label in sid.UNIT_SCOPED_LABELS:
                if gid in scoped_owner and scoped_owner[gid] != source:
                    raise CombineError(f"{label} id {gid} appears in {scoped_owner[gid]} and {source}: "
                                       "unit-scoped ids must be unique (was a unit loaded twice?)")
                scoped_owner[gid] = source
            acc = accs.setdefault(gid, _Node(gid, label))
            if acc.label != label:
                raise CombineError(f"{gid}: label {label} in {source} but {acc.label} before")
            acc.add(node["props"], source, is_bang)
        all_edges += item["fragment"]["edges"]
    conflicts: list[dict] = []
    nodes = [acc.finish(conflicts) for acc in accs.values()]
    return nodes, merge_edges(all_edges), conflicts


# --- Validation and statistics ----------------------------------------------------------------------------------

def validate_graph(nodes: list[dict], edges: list[dict], existing_ids: set[str] | None = None) -> tuple[list[str], list[str]]:
    """Errors and warnings of a combined graph (schema 2.2 label pairs, ends exist, every node and edge has nguon)."""
    errors, warnings = [], []
    labels: dict[str, str] = {}
    for node in nodes:
        gid, label = node["id"], node["labels"][0]
        if gid in labels:
            errors.append(f"duplicate node id {gid}")
        labels[gid] = label
        if label not in ss.NODE_LABELS:
            errors.append(f"{gid}: unknown label {label}")
        nguon = node["props"].get("nguon")
        if not nguon or not isinstance(nguon, list):
            errors.append(f"{gid}: node has no nguon")
        elif existing_ids is not None:
            errors += [f"{gid}: nguon {n} is not a unit of qc3626.json" for n in nguon if n not in existing_ids]
        if label in sid.MERGEABLE_LABELS:
            expected = f"sem.{label}.{sid.slug(node['props']['ten'])}"
            if expected != gid:
                warnings.append(f"{gid}: id does not match the slug of ten {node['props']['ten']!r} ({expected})")
    for e in edges:
        where = f"edge {e['type']} {e['from']} -> {e['to']}"
        spec = ss.EDGE_SPECS.get(e["type"])
        if spec is None:
            errors.append(f"{where}: unknown edge type")
            continue
        missing = [end for end in ("from", "to") if e[end] not in labels]
        for end in missing:
            errors.append(f"{where}: {end} node does not exist")
        nguon = e["props"].get("nguon")
        if not nguon or not isinstance(nguon, list):
            errors.append(f"{where}: edge has no nguon")
        elif existing_ids is not None:
            errors += [f"{where}: nguon {n} is not a unit of qc3626.json" for n in nguon if n not in existing_ids]
        for name, prop in spec.props.items():
            if prop.required and name not in e["props"]:
                errors.append(f"{where}: missing required property {name}")
        if missing:
            continue
        src_label, dst_label = labels[e["from"]], labels[e["to"]]
        if src_label not in spec.from_labels or dst_label not in spec.to_labels:
            errors.append(f"{where}: {src_label} -> {dst_label} is not allowed for {e['type']}")
        elif e["type"] == "LA_MOT" and src_label != dst_label:
            errors.append(f"{where}: LA_MOT must join two nodes of the same label")
        if e["type"] == "TINH_VAO" and e["props"].get("boi_canh") is not None:
            if labels.get(e["props"]["boi_canh"]) != "HoatDong":
                errors.append(f"{where}: boi_canh {e['props']['boi_canh']} is not an existing HoatDong")
        if e["from"] == e["to"]:
            errors.append(f"{where}: edge from a node to itself")
    return errors, warnings


def count_graph(nodes: list[dict], edges: list[dict]) -> dict:
    by_label = Counter(n["labels"][0] for n in nodes)
    by_type = Counter(e["type"] for e in edges)
    return {"nodes": {k: by_label[k] for k in ss.NODE_LABELS if by_label[k]}, "n_nodes": len(nodes),
            "edges": {k: by_type[k] for k in ss.EDGE_TYPES if by_type[k]}, "n_edges": len(edges)}


# --- Approved entity merges -------------------------------------------------------------------------------------

def read_merge_list(path: Path = MERGE_LIST) -> list[dict]:
    """Rows of the owner's merge list (utf-8-sig: Excel adds a BOM). A missing file means no merges."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return []
        absent = [c for c in MERGE_FIELDS if c not in reader.fieldnames]
        if absent:
            raise CombineError(f"{path.name}: missing column(s) {absent} (expected {list(MERGE_FIELDS)})")
        return [{k: (row.get(k) or "").strip() for k in MERGE_FIELDS} for row in reader]


def apply_merges(nodes: list[dict], edges: list[dict], rows: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """Apply the rows with quyet_dinh = gop. Returns (nodes, edges, info); info has merges_applied, conflicts,
    self_loops_dropped, duplicate_edges_joined and skipped (rows not applied, by quyet_dinh)."""
    skipped = Counter(r["quyet_dinh"].lower() or "(trống)" for r in rows if r["quyet_dinh"].lower() != "gop")
    todo = [r for r in rows if r["quyet_dinh"].lower() == "gop"]
    info = {"merges_applied": [], "conflicts": [], "self_loops_dropped": [], "duplicate_edges_joined": 0,
            "skipped": dict(skipped)}
    if not todo:
        return nodes, edges, info
    by_id = {n["id"]: n for n in nodes}
    errors, bo_to_giu = [], {}
    for r in todo:
        where = f"{r['id_bo']} -> {r['id_giu']}"
        giu, bo = by_id.get(r["id_giu"]), by_id.get(r["id_bo"])
        if giu is None or bo is None:
            errors.append(f"{where}: id not found ({'id_giu' if giu is None else 'id_bo'})")
            continue
        if giu["labels"][0] != bo["labels"][0]:
            errors.append(f"{where}: different labels {bo['labels'][0]} / {giu['labels'][0]}")
        elif r["label"] != giu["labels"][0]:
            errors.append(f"{where}: column label {r['label']!r} but the nodes are {giu['labels'][0]}")
        if giu["labels"][0] not in sid.MERGEABLE_LABELS:
            errors.append(f"{where}: label {giu['labels'][0]} is not mergeable ({list(sid.MERGEABLE_LABELS)})")
        if r["id_giu"] == r["id_bo"]:
            errors.append(f"{where}: id_giu equals id_bo")
        if r["id_bo"] in bo_to_giu:
            errors.append(f"{r['id_bo']}: listed twice as id_bo")
        bo_to_giu[r["id_bo"]] = r["id_giu"]
    chains = sorted(set(bo_to_giu) & set(bo_to_giu.values()))
    errors += [f"{i}: is both an id_bo and an id_giu (chain); rewrite the list so id_bo points at the final id_giu"
               for i in chains]
    if errors:
        raise CombineError("merge list rejected:\n  " + "\n  ".join(errors))

    conflicts = info["conflicts"]
    for bo_id, giu_id in bo_to_giu.items():
        giu, bo = by_id[giu_id]["props"], by_id[bo_id]["props"]
        before = {"giu_nguon": len(giu["nguon"]), "bo_nguon": len(bo["nguon"])}
        giu["bi_danh"] = [a for a in _unique(giu["bi_danh"] + [bo["ten"]] + bo["bi_danh"]) if a != giu["ten"]]
        giu["nguon"] = _unique(giu["nguon"] + bo["nguon"])
        giu["nguon_loai"] = _unique(giu["nguon_loai"] + bo["nguon_loai"])
        for key, value in bo.items():
            if key in CORE_KEYS:
                continue
            if key not in giu:
                giu[key] = value
            elif _dumps(giu[key]) != _dumps(value):
                conflicts.append(_conflict(giu_id, key, (giu[key], giu_id, False), [(value, bo_id, False)], "merge"))
        info["merges_applied"].append({"label": by_id[giu_id]["labels"][0], "id_giu": giu_id, "id_bo": bo_id,
                                       "ten_giu": giu["ten"], "ten_bo": bo["ten"], **before})

    def remap(i):
        return bo_to_giu.get(i, i)

    kept_edges = []
    for e in edges:
        props = dict(e["props"])
        if e["type"] == "TINH_VAO" and props.get("boi_canh") is not None:
            props["boi_canh"] = remap(props["boi_canh"])
        new = {"type": e["type"], "from": remap(e["from"]), "to": remap(e["to"]), "props": props}
        if new["from"] == new["to"]:
            info["self_loops_dropped"].append({"type": e["type"], "node": new["from"], "nguon": props["nguon"],
                                               "was": f"{e['from']} -> {e['to']}"})
            continue
        kept_edges.append(new)
    joined = merge_edges(kept_edges)
    info["duplicate_edges_joined"] = len(kept_edges) - len(joined)
    return [n for n in nodes if n["id"] not in bo_to_giu], joined, info


# --- Output -----------------------------------------------------------------------------------------------------

def build_graph(extract_dir: Path = EXTRACT_DIR, bang_dir: Path = BANG_DIR, merge_list: Path = MERGE_LIST,
                unit_ids: list[str] | None = None, existing_ids: set[str] | None = None) -> tuple[dict, dict]:
    """The combined graph and a report {before, after, errors, warnings}. Nothing is written."""
    unit_ids = unit_ids if unit_ids is not None else [u["id"] for u in ex.select_units(ex.SCOPE_ARTICLES)]
    llm_items, summary = select_records(unit_ids, extract_dir)
    table_items = load_table_fragments(bang_dir)
    nodes, edges, conflicts = combine_fragments(table_items + llm_items)
    before = count_graph(nodes, edges)
    errors, warnings = validate_graph(nodes, edges, existing_ids)
    if errors:
        raise CombineError("combined graph is invalid before merges:\n  " + "\n  ".join(errors[:30])
                           + (f"\n  ... and {len(errors) - 30} more" if len(errors) > 30 else ""))
    rows = read_merge_list(merge_list)
    nodes, edges, info = apply_merges(nodes, edges, rows)
    after = count_graph(nodes, edges)
    errors, more_warnings = validate_graph(nodes, edges, existing_ids)
    if errors:
        raise CombineError("combined graph is invalid after merges:\n  " + "\n  ".join(errors[:30]))
    order = {label: i for i, label in enumerate(ss.NODE_LABELS)}
    nodes = sorted(nodes, key=lambda n: (order[n["labels"][0]], n["id"]))
    edges = sorted(edges, key=lambda e: (e["type"], e["from"], e["to"], _dumps(e["props"])))
    meta = {
        "document": ex.DOC_ID, "schema": "docs/schema_v1.md", "version": "combined_v1",
        "inputs": {**summary, "n_table_fragments": len(table_items),
                   "table_units": [i["source"].split(":", 1)[1] for i in table_items]},
        "merge_list": {"path": merge_list.relative_to(REPO_ROOT).as_posix() if merge_list.is_relative_to(REPO_ROOT)
                       else merge_list.as_posix(), "exists": merge_list.exists(), "n_rows": len(rows),
                       "sha256": hashlib.sha256(merge_list.read_bytes()).hexdigest() if merge_list.exists() else None,
                       "skipped_rows": info["skipped"]},
        "counts": {"before_merges": before, "after_merges": after},
        "conflicts": conflicts + info["conflicts"],
        "merges_applied": info["merges_applied"],
        "merge_side_effects": {"self_loops_dropped": info["self_loops_dropped"],
                               "duplicate_edges_joined": info["duplicate_edges_joined"]},
        "warnings": more_warnings,
    }
    return {"meta": meta, "nodes": nodes, "edges": edges}, {"before": before, "after": after, "info": info}


def write_graph(path: Path, graph: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(graph, ensure_ascii=False, indent=1) + "\n")


def print_stats(before: dict, after: dict) -> None:
    labels = sorted(set(before["nodes"]) | set(after["nodes"]))
    types = sorted(set(before["edges"]) | set(after["edges"]))
    print(f"{'Nút':<16}{'trước gộp':>10}{'sau gộp':>10}")
    for label in labels:
        print(f"{label:<16}{before['nodes'].get(label, 0):>10}{after['nodes'].get(label, 0):>10}")
    print(f"{'TỔNG NÚT':<16}{before['n_nodes']:>10}{after['n_nodes']:>10}")
    print(f"{'Cạnh':<16}{'trước gộp':>10}{'sau gộp':>10}")
    for t in types:
        print(f"{t:<16}{before['edges'].get(t, 0):>10}{after['edges'].get(t, 0):>10}")
    print(f"{'TỔNG CẠNH':<16}{before['n_edges']:>10}{after['n_edges']:>10}")


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    parser.add_argument("--merge-list", type=Path, default=MERGE_LIST)
    args = parser.parse_args(argv)
    existing_ids = set(ex.load_regulation()[0])
    try:
        graph, report = build_graph(merge_list=args.merge_list, existing_ids=existing_ids)
    except CombineError as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 1
    meta = graph["meta"]
    inputs = meta["inputs"]
    print(f"Đơn vị LLM: {inputs['n_units']} ok / {inputs['n_units']} | lấy từ run 0: {inputs['from_run0']}, "
          f"từ run 1: {inputs['from_run1']} ({', '.join(inputs['run1_units']) or '-'}) | "
          f"fragment bảng: {inputs['n_table_fragments']}")
    print_stats(report["before"], report["after"])
    n_conf = Counter(c["phase"] for c in meta["conflicts"])
    print(f"Xung đột thuộc tính: {len(meta['conflicts'])} (ghép: {n_conf['combine']}, gộp tay: {n_conf['merge']}) "
          f"| danh sách gộp: {meta['merge_list']['n_rows']} dòng, áp dụng {len(meta['merges_applied'])}")
    if meta["merge_list"]["skipped_rows"]:
        print(f"  dòng không áp dụng (quyet_dinh): {meta['merge_list']['skipped_rows']}")
    side = meta["merge_side_effects"]
    if meta["merges_applied"]:
        print(f"  cạnh tự trỏ bỏ: {len(side['self_loops_dropped'])}, cạnh trùng gộp: {side['duplicate_edges_joined']}")
    print(f"Cảnh báo (id khác slug của ten): {len(meta['warnings'])}")
    write_graph(args.out, graph)
    print(f"Ghi {args.out.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
