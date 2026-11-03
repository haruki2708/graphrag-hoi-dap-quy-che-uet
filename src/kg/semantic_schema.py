"""Output format of the semantic-layer extraction (docs/schema_v1.md section 2) and its validator. Stdlib only.

What the LLM returns for one content unit (local ids, flat lists, no recursion):
    {"KhaiNiem": [...], "DoiTuong": [...], "HoatDong": [...], "NhomDieuKien": [...], "DieuKien": [...],
     "HeQua": [...], "edges": [{"type", "from", "to", "nguon", ...edge properties}]}
Every node has id (local, e.g. "n1"), ten, bi_danh, nguon (list of unit ids) plus the properties of its label in
schema 2.1. The condition tree is flat: parent -> child goes through GOM edges. NGUON edges are built by code from the
node's nguon list; the LLM never emits them. thu_tu (table 2.1a) is set by script (Q10), never by the LLM.

complete() only checks the top level of the reply, so every nested check lives in validate().
Errors make the unit "invalid"; warnings are recorded but do not reject the unit (soft checks: the type hints in
brackets in schema 2.2, e.g. "KhaiNiem (mức phân loại)", which the text sometimes legitimately bends).
"""

import hashlib
import json
from dataclasses import dataclass, field

from src.kg.semantic_ids import LOCAL_ID, MERGEABLE_LABELS, slug

KET_HOP = ("AND", "OR")
THANG_MUC = ("nam_dao_tao", "diem_chu", "hoc_luc", "hang_tot_nghiep")
# bac_ngoai_ngu: level 1-6 of the 6-level foreign language framework, compared directly (no thu_tu table);
# DieuKien only, never a KhaiNiem level (owner decision 03/11/2026)
THANG_DIEU_KIEN = ("thang_10", "thang_4", "tin_chi", "ty_le") + THANG_MUC + ("bac_ngoai_ngu",)


@dataclass(frozen=True)
class Prop:
    kind: str  # "string" | "number" | "boolean" | "string_list"
    required: bool = False
    enum: tuple[str, ...] | None = None
    nullable: bool = False


@dataclass(frozen=True)
class NodeSpec:
    label: str
    props: dict[str, Prop]


@dataclass(frozen=True)
class EdgeSpec:
    type: str
    from_labels: tuple[str, ...]
    to_labels: tuple[str, ...]
    props: dict[str, Prop] = field(default_factory=dict)  # besides nguon, which every edge has


COMMON_NODE_PROPS = {
    "id": Prop("string", required=True),
    "ten": Prop("string", required=True),
    "bi_danh": Prop("string_list", required=True),
    "nguon": Prop("string_list", required=True),
}

# Schema 2.1, label-specific properties. thu_tu is deliberately absent (script only).
NODE_SPECS = {s.label: s for s in (
    NodeSpec("KhaiNiem", {
        "loai": Prop("string", required=True, enum=("dai_luong", "loai_hoc_phan", "muc_phan_loai", "khac")),
        "dinh_nghia": Prop("string"),
        "gia_tri": Prop("number"),
        "thang": Prop("string", enum=THANG_MUC),
    }),
    NodeSpec("DoiTuong", {
        "loai": Prop("string", required=True,
                     enum=("nguoi", "chuc_danh", "don_vi", "chuong_trinh", "hinh_thuc", "van_bang")),
    }),
    NodeSpec("HoatDong", {"mo_ta": Prop("string")}),
    NodeSpec("NhomDieuKien", {
        "ket_hop": Prop("string", required=True, enum=KET_HOP),
        "mo_ta": Prop("string", required=True),
    }),
    NodeSpec("DieuKien", {
        "mo_ta": Prop("string", required=True),
        "min": Prop("number"), "max": Prop("number"),
        "min_incl": Prop("boolean"), "max_incl": Prop("boolean"),
        "don_vi": Prop("string"),
        "thang": Prop("string", enum=THANG_DIEU_KIEN),
        "toan_tu": Prop("string", enum=("la", "thuoc", "co")),
        "phu_dinh": Prop("boolean"),
        "so_voi": Prop("string"),
        "moc": Prop("string"),
    }),
    NodeSpec("HeQua", {
        "loai": Prop("string", required=True, enum=("trang_thai", "quyen_loi", "nghia_vu", "che_tai", "gioi_han")),
        "mo_ta": Prop("string"),
        "gia_tri": Prop("number"),
        "don_vi": Prop("string"),
    }),
)}
NODE_LABELS = tuple(NODE_SPECS)

# Schema 2.2: the 9 edge types the LLM may emit (NGUON is built by code).
EDGE_SPECS = {s.type: s for s in (
    EdgeSpec("GOM", ("NhomDieuKien",), ("DieuKien", "NhomDieuKien")),
    EdgeSpec("DAN_DEN", ("NhomDieuKien",), ("HeQua", "KhaiNiem")),
    EdgeSpec("YEU_CAU", ("HoatDong",), ("NhomDieuKien",)),
    EdgeSpec("XET", ("DieuKien",), ("KhaiNiem", "HeQua")),
    EdgeSpec("AP_DUNG_CHO", ("NhomDieuKien", "HeQua", "HoatDong"), ("DoiTuong",)),
    EdgeSpec("LA_MOT", ("KhaiNiem", "DoiTuong"), ("KhaiNiem", "DoiTuong")),  # same label at both ends, checked below
    EdgeSpec("TINH_VAO", ("KhaiNiem",), ("KhaiNiem",),
             {"co": Prop("boolean", required=True), "boi_canh": Prop("string", nullable=True)}),
    EdgeSpec("DUNG_DE", ("KhaiNiem",), ("HoatDong",), {"co": Prop("boolean", required=True)}),
    EdgeSpec("GIAO_QUY_DINH", ("HoatDong", "NhomDieuKien", "KhaiNiem"), ("DoiTuong",),
             {"noi_dung": Prop("string", required=True)}),
)}
EDGE_TYPES = tuple(EDGE_SPECS)
EDGE_BASE_PROPS = {
    "type": Prop("string", required=True, enum=EDGE_TYPES),
    "from": Prop("string", required=True),
    "to": Prop("string", required=True),
    "nguon": Prop("string", required=True),
}
EDGE_OPTIONAL_PROPS = {"co": Prop("boolean"), "boi_canh": Prop("string", nullable=True), "noi_dung": Prop("string")}

# Soft (warning) checks from the bracketed hints in schema 2.2: (edge type, end, allowed KhaiNiem/HeQua loai)
SOFT_LOAI = {
    ("DAN_DEN", "to", "KhaiNiem"): ("muc_phan_loai",),
    ("XET", "to", "KhaiNiem"): ("dai_luong", "muc_phan_loai"),
    ("XET", "to", "HeQua"): ("trang_thai",),
    ("TINH_VAO", "from", "KhaiNiem"): ("loai_hoc_phan",),
    ("TINH_VAO", "to", "KhaiNiem"): ("dai_luong",),
    ("DUNG_DE", "from", "KhaiNiem"): ("dai_luong",),
}
SCRIPT_ONLY_PROPS = ("thu_tu",)


# --- JSON Schema ------------------------------------------------------------------------------------------------

def _prop_schema(p: Prop) -> dict:
    if p.kind == "string_list":
        out = {"type": "array", "items": {"type": "string"}}
    else:
        out = {"type": [p.kind, "null"] if p.nullable else p.kind}
    if p.enum:
        out["enum"] = list(p.enum)
    return out


def _object_schema(props: dict[str, Prop]) -> dict:
    return {
        "type": "object",
        "properties": {name: _prop_schema(p) for name, p in props.items()},
        "required": [name for name, p in props.items() if p.required],
        "additionalProperties": False,
    }


def json_schema() -> dict:
    """JSON Schema passed to complete(json_schema=...). No recursion, no numeric/string constraints, and
    additionalProperties false on every object (what the structured-output docs list as supported)."""
    properties = {label: {"type": "array", "items": _object_schema({**COMMON_NODE_PROPS, **spec.props})}
                  for label, spec in NODE_SPECS.items()}
    properties["edges"] = {"type": "array", "items": _object_schema({**EDGE_BASE_PROPS, **EDGE_OPTIONAL_PROPS})}
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def schema_sha256(schema: dict | None = None) -> str:
    blob = json.dumps(schema or json_schema(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --- Validator --------------------------------------------------------------------------------------------------

@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _type_ok(value, p: Prop) -> bool:
    if value is None:
        return p.nullable
    if p.kind == "string":
        return isinstance(value, str)
    if p.kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if p.kind == "boolean":
        return isinstance(value, bool)
    if p.kind == "string_list":
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    raise ValueError(p.kind)


def _check_props(where: str, obj: dict, props: dict[str, Prop], report: Report) -> None:
    for key in obj:
        if key in SCRIPT_ONLY_PROPS:
            report.errors.append(f"{where}: has {key!r}, which is set by script (Q10, table 2.1a), not by the LLM")
        elif key not in props:
            report.errors.append(f"{where}: unknown property {key!r}")
    for name, p in props.items():
        if name not in obj:
            if p.required:
                report.errors.append(f"{where}: missing required property {name!r}")
            continue
        value = obj[name]
        if not _type_ok(value, p):
            report.errors.append(f"{where}: {name!r} must be {p.kind}{' or null' if p.nullable else ''}, got {value!r}")
        elif p.enum and value is not None and value not in p.enum:
            report.errors.append(f"{where}: {name!r} = {value!r} not in {list(p.enum)}")


def _check_nguon(where: str, code, allowed: set[str], existing: set[str], report: Report) -> None:
    if not isinstance(code, str):
        return  # type error already reported
    if code not in existing:
        report.errors.append(f"{where}: nguon {code!r} does not exist in qc3626.json")
    elif code not in allowed:
        report.errors.append(f"{where}: nguon {code!r} is outside the unit being extracted (allowed: {sorted(allowed)})")


def validate(output, allowed_nguon: set[str], existing_ids: set[str]) -> Report:
    """Check one LLM reply against schema 2.1/2.2 and the source rules.

    allowed_nguon: the unit being extracted and its Diem children (a Dieu-lead unit: the Dieu id only).
    existing_ids: every unit id in qc3626.json.
    """
    report = Report()
    if not isinstance(output, dict):
        report.errors.append("reply is not a JSON object")
        return report
    expected_keys = (*NODE_LABELS, "edges")
    for key in output:
        if key not in expected_keys:
            report.errors.append(f"top level: unknown key {key!r}")
    for key in expected_keys:
        if key not in output:
            report.errors.append(f"top level: missing key {key!r}")
        elif not isinstance(output[key], list):
            report.errors.append(f"top level: {key!r} must be a list")

    nodes: dict[str, tuple[str, dict]] = {}  # local id -> (label, node)
    for label in NODE_LABELS:
        for i, node in enumerate(output.get(label) if isinstance(output.get(label), list) else []):
            where = f"{label}[{i}]"
            if not isinstance(node, dict):
                report.errors.append(f"{where}: not an object")
                continue
            node_id = node.get("id")
            if isinstance(node_id, str):
                where = f"{label} {node_id!r}"
            _check_props(where, node, {**COMMON_NODE_PROPS, **NODE_SPECS[label].props}, report)
            if isinstance(node_id, str):
                if not LOCAL_ID.match(node_id):
                    report.errors.append(f"{where}: id must match {LOCAL_ID.pattern}")
                if node_id in nodes:
                    report.errors.append(f"{where}: duplicate id (also a {nodes[node_id][0]})")
                else:
                    nodes[node_id] = (label, node)
            if isinstance(node.get("ten"), str) and not node["ten"].strip():
                report.errors.append(f"{where}: empty ten")
            elif isinstance(node.get("ten"), str) and label in MERGEABLE_LABELS and not slug(node["ten"]):
                report.errors.append(f"{where}: ten {node['ten']!r} gives an empty id slug")
            nguon = node.get("nguon")
            if isinstance(nguon, list):
                if not nguon:
                    report.errors.append(f"{where}: nguon is empty")
                for code in nguon:
                    _check_nguon(where, code, allowed_nguon, existing_ids, report)
            _check_node_values(where, label, node, report)

    gom_children: dict[str, list[str]] = {}
    gom_parents: dict[str, list[str]] = {}
    seen_edges = set()
    edges = output.get("edges") if isinstance(output.get("edges"), list) else []
    for i, edge in enumerate(edges):
        where = f"edges[{i}]"
        if not isinstance(edge, dict):
            report.errors.append(f"{where}: not an object")
            continue
        etype = edge.get("type")
        spec = EDGE_SPECS.get(etype)
        if spec is not None:
            where = f"edges[{i}] {etype} {edge.get('from')!r}->{edge.get('to')!r}"
            _check_props(where, edge, {**EDGE_BASE_PROPS, **spec.props}, report)
        else:
            _check_props(where, edge, {**EDGE_BASE_PROPS, **EDGE_OPTIONAL_PROPS}, report)
        _check_nguon(where, edge.get("nguon"), allowed_nguon, existing_ids, report)
        src, dst = edge.get("from"), edge.get("to")
        ends_ok = True
        for end, ref in (("from", src), ("to", dst)):
            if isinstance(ref, str) and ref not in nodes:
                report.errors.append(f"{where}: {end} {ref!r} is not a node id")
                ends_ok = False
        if spec is None or not ends_ok or not isinstance(src, str) or not isinstance(dst, str):
            continue
        src_label, src_node = nodes[src]
        dst_label, dst_node = nodes[dst]
        if src == dst:
            report.errors.append(f"{where}: edge from a node to itself")
        if src_label not in spec.from_labels or dst_label not in spec.to_labels:
            report.errors.append(f"{where}: {src_label} -> {dst_label} is not allowed for {etype} "
                                 f"({'|'.join(spec.from_labels)} -> {'|'.join(spec.to_labels)})")
        elif etype == "LA_MOT" and src_label != dst_label:
            report.errors.append(f"{where}: LA_MOT must link two nodes of the same label, got {src_label} -> {dst_label}")
        for (soft_type, end, label), loai in SOFT_LOAI.items():
            node_label, node = (src_label, src_node) if end == "from" else (dst_label, dst_node)
            if soft_type == etype and node_label == label and node.get("loai") not in loai:
                report.warnings.append(f"{where}: {end} {label} has loai {node.get('loai')!r}; schema 2.2 expects {list(loai)}")
        if etype == "GIAO_QUY_DINH" and isinstance(edge.get("noi_dung"), str) and not edge["noi_dung"].strip():
            report.errors.append(f"{where}: empty noi_dung")
        if etype == "TINH_VAO":
            ctx = edge.get("boi_canh")
            if ctx is not None and isinstance(ctx, str) and (ctx not in nodes or nodes[ctx][0] != "HoatDong"):
                report.errors.append(f"{where}: boi_canh {ctx!r} must be the id of a HoatDong in this reply, or null")
        if etype == "GOM":
            gom_children.setdefault(src, []).append(dst)
            gom_parents.setdefault(dst, []).append(src)
        key = json.dumps(edge, sort_keys=True, ensure_ascii=False)
        if key in seen_edges:
            report.warnings.append(f"{where}: duplicate edge")
        seen_edges.add(key)

    for node_id, (label, _) in nodes.items():
        if label == "NhomDieuKien" and not gom_children.get(node_id):
            report.errors.append(f"NhomDieuKien {node_id!r}: has no GOM edge (a group needs at least one member)")
        if label == "DieuKien" and not gom_parents.get(node_id):
            report.warnings.append(f"DieuKien {node_id!r}: not inside any NhomDieuKien (no incoming GOM)")
    for cycle in _gom_cycles(gom_children):
        report.errors.append(f"GOM cycle: {' -> '.join(cycle)}")
    return report


def _check_node_values(where: str, label: str, node: dict, report: Report) -> None:
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
    if label == "DieuKien":
        lo, hi = node.get("min"), node.get("max")
        if num(lo) and num(hi) and lo > hi:
            report.errors.append(f"{where}: min {lo} > max {hi}")
        for bound in ("min", "max"):
            incl = f"{bound}_incl"
            if num(node.get(bound)) and not isinstance(node.get(incl), bool):
                report.errors.append(f"{where}: {bound} is set, so {incl} must be true or false")
            if incl in node and bound not in node:
                report.warnings.append(f"{where}: {incl} without {bound}")
    if label == "KhaiNiem" and "thang" in node and node.get("loai") != "muc_phan_loai":
        report.warnings.append(f"{where}: thang is set but loai is {node.get('loai')!r} (schema 2.1: only levels have thang)")


def _gom_cycles(children: dict[str, list[str]]) -> list[list[str]]:
    """Each GOM cycle found by depth-first search, as a list of ids that starts and ends on the same node."""
    cycles, state = [], {}  # state: 1 = on the current path, 2 = done

    def visit(node: str, path: list[str]) -> None:
        state[node] = 1
        for child in children.get(node, []):
            if state.get(child) == 1:
                cycles.append(path[path.index(child):] + [child])
            elif state.get(child) is None:
                visit(child, path + [child])
        state[node] = 2

    for start in sorted(children):
        if state.get(start) is None:
            visit(start, [start])
    return cycles
