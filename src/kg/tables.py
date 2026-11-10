"""The six classification tables and the thu_tu table as semantic-layer fragments (T4 PR 1b; schema Q10 = B).

Run from the repo root:
    python src/kg/tables.py            # build, check, write data/processed/semantic/bang/<unit>.json + bang_kiem_tay.csv
    python src/kg/tables.py --check    # build and check everything, write nothing; exit 1 on any problem

Input: data/manual/bang_phan_loai_v1.json, typed by hand: every row keeps its numbers as strings exactly as the
regulation prints them, plus the verbatim text of the row. This script never reads numbers from the PDF; it only
turns that file into graph fragments (the owner checks the file and the CSV 100%).

Representation, all inside the existing schema (no new label, edge type or property):
- levels (d17.k2 years, d41.k2/d43.k1 letter grades, d45.k2 learning level, d47.k5 graduation rank) are
  KhaiNiem {loai: muc_phan_loai, thang, thu_tu}, LA_MOT to the quantity of their scale; d43.k1 adds gia_tri;
- a range row is NhomDieuKien AND -GOM-> DieuKien {min/max/min_incl/max_incl, thang} -XET-> quantity, with
  DAN_DEN to the level (pattern A of schema 2.3); AP_DUNG_CHO to the program for d17.k2; a blank cell ("-") gives no row;
- d43.k1 has no ranges: the rows are gia_tri on the letter levels;
- d32.k2 (bonus points) is not an ordered scale: per row NhomDieuKien AND -GOM-> DieuKien {toan_tu: la, mo_ta: prize
  level and rank} -XET-> KhaiNiem "giải thưởng nghiên cứu khoa học của sinh viên", and DAN_DEN to a HeQua
  {loai: quyen_loi, gia_tri, don_vi: điểm}.
Nodes are built in the LLM reply format (local ids), checked by semantic_schema.validate(nguon_loai="bang") and turned
into a fragment by extract.build_fragment(nguon_loai="bang"), so global ids come from semantic_ids and a level named
like an LLM node ("năm thứ hai") merges with it. Local ids of unit-scoped nodes start with "bang_" so they never
collide with the ids the LLM gives to the rules it extracts from the same unit.
"""

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.kg import extract as ex  # noqa: E402
from src.kg import semantic_ids as sid  # noqa: E402
from src.kg import semantic_schema as ss  # noqa: E402

TABLES_PATH = REPO_ROOT / "data" / "manual" / "bang_phan_loai_v1.json"
OUT_DIR = REPO_ROOT / "data" / "processed" / "semantic" / "bang"
CHECK_CSV = REPO_ROOT / "data" / "processed" / "semantic" / "bang_kiem_tay.csv"
RECORD_FORMAT = 1
NGUON_LOAI = "bang"
LOCAL_PREFIX = "bang_"
# Schema Q10 = B: rows of each table (d17.k2 14, d32.k2 7, d41.k2 10, d43.k1 9, d45.k2 5, d47.k5 4)
EXPECTED_ROWS = {f"qc3626.{u}": n for u, n in (("d17.k2", 14), ("d32.k2", 7), ("d41.k2", 10), ("d43.k1", 9),
                                                ("d45.k2", 5), ("d47.k5", 4))}
DANG_NUMBERS = {"duoi": 1, "tu_den": 2, "tu": 1, "bang": 1, "gia_tri": 1}
KIEU = ("khoang_theo_muc", "gia_tri_muc", "diem_thuong")
LABEL_LETTER = {"KhaiNiem": "k", "DoiTuong": "o", "HoatDong": "h", "HeQua": "q", "NhomDieuKien": "g", "DieuKien": "c"}
CSV_FIELDS = ["unit", "nguon", "chuong_trinh", "thang", "muc", "thu_tu", "khoang_hoac_gia_tri", "nguyen_van",
              "id_nut_dich", "id_nhom", "verdict", "note"]


# --- Input ------------------------------------------------------------------------------------------------------

def load_tables(path: Path = TABLES_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_number(text: str) -> int | float:
    """"2,50" -> 2.5, "40" -> 40: the regulation writes decimals with a comma."""
    return float(text.replace(",", ".")) if "," in text else int(text)


def bounds(row: dict) -> dict:
    """min/max/min_incl/max_incl of a row, from its `dang` and the typed number strings."""
    numbers = [parse_number(s) for s in row["so"]]
    if row["dang"] == "duoi":
        return {"max": numbers[0], "max_incl": False}
    if row["dang"] == "tu_den":
        return {"min": numbers[0], "min_incl": True, "max": numbers[1], "max_incl": True}
    if row["dang"] == "tu":
        return {"min": numbers[0], "min_incl": True}
    raise ValueError(f"dang {row['dang']!r} has no range")


def level_index(data: dict) -> dict[str, tuple[str, dict]]:
    """level name -> (scale key, entry) for the four scales of table 2.1a."""
    return {m["ten"]: (key, m) for key, scale in data["thang"].items() for m in scale["muc"]}


# --- Building one table -----------------------------------------------------------------------------------------

class Builder:
    """Collects nodes and edges in the LLM reply format. Mergeable nodes are shared by (label, ten)."""

    def __init__(self):
        self.nodes = {label: [] for label in ss.NODE_LABELS}
        self.edges = []
        self._counters = dict.fromkeys(ss.NODE_LABELS, 0)
        self._shared: dict[tuple[str, str], dict] = {}
        self._edge_keys = set()

    def _new(self, label: str, ten: str, nguon: str, bi_danh, props: dict) -> dict:
        self._counters[label] += 1
        node = {"id": f"{LOCAL_PREFIX}{LABEL_LETTER[label]}{self._counters[label]:02d}", "ten": ten,
                "bi_danh": list(bi_danh), "nguon": [nguon], **props}
        self.nodes[label].append(node)
        return node

    def shared(self, label: str, ten: str, nguon: str, bi_danh=(), **props) -> str:
        """A node that merges across rows (and later across units); returns its local id."""
        key = (label, ten)
        if key not in self._shared:
            self._shared[key] = self._new(label, ten, nguon, bi_danh, props)
        elif nguon not in self._shared[key]["nguon"]:
            self._shared[key]["nguon"].append(nguon)
        return self._shared[key]["id"]

    def scoped(self, label: str, ten: str, nguon: str, **props) -> str:
        """A NhomDieuKien or DieuKien: one per use, never merged."""
        return self._new(label, ten, nguon, (), props)["id"]

    def edge(self, etype: str, src: str, dst: str, nguon: str) -> None:
        key = (etype, src, dst, nguon)
        if key not in self._edge_keys:
            self._edge_keys.add(key)
            self.edges.append({"type": etype, "from": src, "to": dst, "nguon": nguon})

    def output(self) -> dict:
        return {**self.nodes, "edges": self.edges}


def _level(b: Builder, data: dict, levels: dict, muc: str, nguon: str, **extra) -> str:
    """The KhaiNiem of a level, with LA_MOT to the quantity of its scale. A level of no scale (P) stands alone."""
    if muc in levels:
        scale_key, entry = levels[muc]
        node = b.shared("KhaiNiem", muc, nguon, entry.get("bi_danh", ()), loai="muc_phan_loai", thang=scale_key,
                        thu_tu=entry["thu_tu"], **extra)
        scale = b.shared("KhaiNiem", data["thang"][scale_key]["dai_luong"], nguon, loai="dai_luong")
        b.edge("LA_MOT", node, scale, nguon)
        return node
    other = next((m for m in data.get("muc_khac", []) if m["ten"] == muc), None)
    if other is None:
        raise ValueError(f"level {muc!r} is in neither thang nor muc_khac")
    return b.shared("KhaiNiem", muc, nguon, other.get("bi_danh", ()), loai="muc_phan_loai", **extra)


def _group_text(row: dict) -> str:
    text = f"{row['muc']} khi {row['dieu_kien']}"
    if row.get("chuong_trinh"):
        text += f" ({row['chuong_trinh']})"
    if row.get("pham_vi"):
        text += f"; áp dụng cho {row['pham_vi']}"
    return text


def build_table(table: dict, data: dict) -> tuple[dict, list[dict]]:
    """(reply-format output, per-row refs {"nhom", "muc"} as local ids) for one table."""
    if table["kieu"] not in KIEU:
        raise ValueError(f"{table['unit']}: unknown kieu {table['kieu']!r}")
    b, levels, refs = Builder(), level_index(data), []
    for row in table["dong"]:
        src = row["nguon"]
        if table["kieu"] == "gia_tri_muc":
            level = _level(b, data, levels, row["muc"], src, gia_tri=parse_number(row["so"][0]))
            refs.append({"nhom": None, "muc": level})
            continue
        if table["kieu"] == "khoang_theo_muc":
            group = b.scoped("NhomDieuKien", row["muc"], src, ket_hop="AND", mo_ta=_group_text(row))
            cond = b.scoped("DieuKien", row["dieu_kien"], src, mo_ta=row["dieu_kien"], **bounds(row),
                            don_vi=table["don_vi"], thang=table["thang_dieu_kien"])
            b.edge("GOM", group, cond, src)
            b.edge("XET", cond, b.shared("KhaiNiem", table["luong"], src, loai="dai_luong"), src)
            level = _level(b, data, levels, row["muc"], src)
            b.edge("DAN_DEN", group, level, src)
            if row.get("chuong_trinh"):
                b.edge("AP_DUNG_CHO", group, b.shared("DoiTuong", row["chuong_trinh"], src, loai="chuong_trinh"), src)
            if row.get("loai_tru"):  # d45.k2 "Yếu": "chưa thuộc trường hợp bị buộc thôi học"
                excl = row["loai_tru"]
                not_cond = b.scoped("DieuKien", excl["dieu_kien"], src, mo_ta=excl["dieu_kien"], toan_tu="la",
                                    phu_dinh=True)
                b.edge("GOM", group, not_cond, src)
                b.edge("XET", not_cond, b.shared("HeQua", excl["he_qua"], src, loai="trang_thai"), src)
        else:  # diem_thuong (d32.k2): a prize level and rank gives a fixed bonus
            prefix = "Đạt giải thưởng "
            if not row["dieu_kien"].startswith(prefix):
                raise ValueError(f"{src}: dieu_kien does not start with {prefix!r}")
            text = f"{row['dieu_kien']}: {row['hang']}"
            group = b.scoped("NhomDieuKien", row["hang"], src, ket_hop="AND", mo_ta=text)
            cond = b.scoped("DieuKien", row["hang"], src, mo_ta=text, toan_tu="la")
            b.edge("GOM", group, cond, src)
            b.edge("XET", cond, b.shared("KhaiNiem", table["luong"], src, loai="muc_phan_loai"), src)
            bonus = b.shared("HeQua", f"điểm thưởng {row['hang'][0].lower()}{row['hang'][1:]} "
                                      f"{row['dieu_kien'][len(prefix):]}", src, loai="quyen_loi",
                             mo_ta=row["nguyen_van"], gia_tri=parse_number(row["so"][0]), don_vi=table["don_vi"])
            b.edge("DAN_DEN", group, bonus, src)
            level = bonus
        refs.append({"nhom": group, "muc": level})
    return b.output(), refs


def build_record(table: dict, data: dict, by_id: dict, children: dict, table_sha256: str) -> tuple[dict, list[dict]]:
    """The semantic record of one unit (same keys as extract.py's records) and the per-row refs as global ids."""
    unit = table["unit"]
    output, local_refs = build_table(table, data)
    report = ss.validate(output, ex.allowed_nguon(unit, by_id, children), set(by_id), nguon_loai=NGUON_LOAI)
    fragment = ex.build_fragment(output, unit, nguon_loai=NGUON_LOAI) if report.ok else None
    id_map = fragment["meta"]["id_map"] if fragment else {}
    refs = [{k: id_map.get(v) if v else None for k, v in r.items()} for r in local_refs]
    record = {"format": RECORD_FORMAT, "unit": unit, "status": "ok" if report.ok else "invalid",
              "errors": report.errors, "warnings": report.warnings, "raw": {"text": None, "parsed": output},
              "fragment": fragment,
              "metadata": {"nguon_loai": NGUON_LOAI, "table_file": TABLES_PATH.relative_to(REPO_ROOT).as_posix(),
                           "table_sha256": table_sha256, "n_rows": len(table["dong"])}}
    return record, refs


# --- Checks on the typed data -----------------------------------------------------------------------------------

def interval(row: dict) -> tuple[float, bool, float, bool]:
    """(low, low_incl, high, high_incl); open ends are infinite."""
    b = bounds(row)
    return (b.get("min", float("-inf")), b.get("min_incl", False), b.get("max", float("inf")), b.get("max_incl", False))


def chain_errors(label: str, rows: list[dict], step: float) -> list[str]:
    """Rows of one chain (same table, same program or scale): no overlap, no gap (the printed values are on a fixed
    step: 1 credit, 0.1 point out of 10, 0.01 point out of 4)."""
    errors = []
    ordered = sorted(rows, key=lambda r: interval(r)[0])
    for a, b in zip(ordered, ordered[1:]):
        _, _, hi, hi_incl = interval(a)
        lo, lo_incl, _, _ = interval(b)
        gap = round(lo - hi, 6)
        if hi_incl and lo_incl:
            ok = gap == round(step, 6)
        elif hi_incl or lo_incl:
            ok = gap == 0
        else:
            ok = False  # both exclusive: the boundary value belongs to no row
        if not ok:
            errors.append(f"{label}: {a['muc']!r} ({a['dieu_kien']}) and {b['muc']!r} ({b['dieu_kien']}) are not "
                          f"adjacent on step {step} (gap {gap}, inclusive {hi_incl}/{lo_incl})")
    return errors


def verify_data(data: dict, by_id: dict[str, dict]) -> list[str]:
    """Problems in the typed file against the regulation text; an empty list means it is consistent."""
    errors = []
    tables = {t["unit"]: t for t in data["bang"]}
    if set(tables) != set(EXPECTED_ROWS):
        errors.append(f"tables {sorted(tables)} differ from the six of schema Q10 {sorted(EXPECTED_ROWS)}")
    levels = level_index(data)
    other_levels = {m["ten"] for m in data.get("muc_khac", [])}
    seen_ids = {}
    for unit, table in tables.items():
        if unit in EXPECTED_ROWS and len(table["dong"]) != EXPECTED_ROWS[unit]:
            errors.append(f"{unit}: {len(table['dong'])} rows, schema Q10 says {EXPECTED_ROWS[unit]}")
        if table["kieu"] not in KIEU:
            errors.append(f"{unit}: unknown kieu {table['kieu']!r}")
        chains: dict[str, list[dict]] = {}
        for i, row in enumerate(table["dong"], 1):
            where = f"{unit} row {i}"
            src = row["nguon"]
            if src not in by_id or not (src == unit or src.startswith(unit + ".")):
                errors.append(f"{where}: nguon {src!r} is not the table's unit or one of its points")
                continue
            text = by_id[src]["text"]
            for field in ("nguyen_van", "dieu_kien", "pham_vi"):
                if field in row and row[field] not in text:
                    errors.append(f"{where}: {field} {row[field]!r} is not verbatim in {src}")
            if "loai_tru" in row and row["loai_tru"]["dieu_kien"] not in text:
                errors.append(f"{where}: loai_tru.dieu_kien is not verbatim in {src}")
            if "dieu_kien" in row and table["kieu"] != "diem_thuong" and row["dieu_kien"] not in row["nguyen_van"]:
                errors.append(f"{where}: dieu_kien is not part of nguyen_van")
            numbers = numbers_in(row["nguyen_van"])
            if numbers != row["so"]:
                errors.append(f"{where}: so {row['so']} differs from the numbers in nguyen_van {numbers}")
            if row["dang"] not in DANG_NUMBERS or len(row["so"]) != DANG_NUMBERS[row["dang"]]:
                errors.append(f"{where}: dang {row['dang']!r} needs {DANG_NUMBERS.get(row['dang'])} number(s)")
            if "muc" in row and row["muc"] not in levels and row["muc"] not in other_levels:
                errors.append(f"{where}: level {row['muc']!r} is in neither thang nor muc_khac")
            if table.get("thang_muc") and row.get("muc") in levels and levels[row["muc"]][0] != table["thang_muc"]:
                errors.append(f"{where}: level {row['muc']!r} belongs to scale {levels[row['muc']][0]}, "
                              f"not {table['thang_muc']}")
            if table["kieu"] == "khoang_theo_muc" and row["dang"] in ("duoi", "tu_den", "tu"):
                chains.setdefault(row["chuoi"], []).append(row)
        for chain, rows in chains.items():
            errors += chain_errors(f"{unit} [{chain}]", rows, table["buoc"])
            # reading order of the ranges must agree with thu_tu
            by_range = [r["muc"] for r in sorted(rows, key=lambda r: interval(r)[0]) if r["muc"] in levels]
            by_thu_tu = sorted(by_range, key=lambda m: levels[m][1]["thu_tu"])
            if by_range != by_thu_tu:
                errors.append(f"{unit} [{chain}]: ranges run {by_range} but thu_tu says {by_thu_tu}")
    names = [m["ten"] for scale in data["thang"].values() for m in scale["muc"]]
    errors += [f"level name {n!r} is used in more than one scale" for n in sorted({n for n in names if names.count(n) > 1})]
    for key, scale in data["thang"].items():
        numbers = [m["thu_tu"] for m in scale["muc"]]
        if numbers != list(range(1, len(numbers) + 1)):
            errors.append(f"thang {key}: thu_tu {numbers} is not 1..{len(numbers)} in order")
        used = {r.get("muc") for t in data["bang"] if t.get("thang_muc") == key for r in t["dong"]}
        for m in scale["muc"]:
            if m["ten"] not in used:
                errors.append(f"thang {key}: level {m['ten']!r} appears in no table row")
    for m in [*levels, *other_levels, *(s["dai_luong"] for s in data["thang"].values())]:
        gid = sid.global_id("KhaiNiem", m, "", "")
        if seen_ids.setdefault(gid, m) != m:
            errors.append(f"names {seen_ids[gid]!r} and {m!r} give the same id {gid}")
    return errors


def numbers_in(text: str) -> list[str]:
    """The numbers of a text as printed (decimal comma), in order of appearance."""
    return re.findall(r"\d+(?:,\d+)?", text)


def verify_records(records: list[dict]) -> list[str]:
    """Problems in the built records: validator errors and warnings, merge conflicts, and properties that two
    fragments give differently to the same node (levels appear in d41.k2 and d43.k1)."""
    errors, nodes = [], {}
    for record in records:
        unit = record["unit"]
        errors += [f"{unit}: {e}" for e in record["errors"]] + [f"{unit}: warning {w}" for w in record["warnings"]]
        if record["fragment"] is None:
            continue
        if record["fragment"]["meta"]["merge_conflicts"]:
            errors.append(f"{unit}: merge conflicts {record['fragment']['meta']['merge_conflicts']}")
        for node in record["fragment"]["nodes"]:
            props = node["props"]
            if props["nguon_loai"] != NGUON_LOAI:
                errors.append(f"{unit}: {node['id']} has nguon_loai {props['nguon_loai']!r}")
            for key, value in props.items():
                if key in ("nguon", "bi_danh", "nguon_loai"):
                    continue
                other = nodes.setdefault(node["id"], {}).setdefault(key, (unit, value))
                if other[1] != value:
                    errors.append(f"{node['id']}: {key} is {other[1]!r} in {other[0]} but {value!r} in {unit}")
    return errors


# --- Check CSV and CLI ------------------------------------------------------------------------------------------

def khoang_text(row: dict) -> str:
    """The row's range or value for the check CSV: "< 35", "[40; 80]", "≥ 5,0", "= 3,7", "+0,20 điểm"."""
    so, dang = row["so"], row["dang"]
    if dang == "duoi":
        return f"< {so[0]}"
    if dang == "tu_den":
        return f"[{so[0]}; {so[1]}]"
    if dang == "tu":
        return f"≥ {so[0]}"
    if dang == "bang":
        return f"= {so[0]}"
    return f"+{so[0]} điểm"


def csv_rows(data: dict, refs_by_unit: dict[str, list[dict]]) -> list[dict]:
    levels = level_index(data)
    rows = []
    for table in data["bang"]:
        for row, ref in zip(table["dong"], refs_by_unit[table["unit"]]):
            scale, entry = levels.get(row.get("muc"), (None, {}))
            rows.append({"unit": table["unit"], "nguon": row["nguon"], "chuong_trinh": row.get("chuong_trinh", ""),
                         "thang": scale or "", "muc": row.get("muc", row.get("hang", "")),
                         "thu_tu": entry.get("thu_tu", ""), "khoang_hoac_gia_tri": khoang_text(row),
                         "nguyen_van": row["nguyen_van"], "id_nut_dich": ref["muc"] or "", "id_nhom": ref["nhom"] or "",
                         "verdict": "", "note": ""})
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # BOM so Excel shows the Vietnamese text
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def build_all(data: dict, table_sha256: str, regulation: tuple[dict, dict] | None = None
              ) -> tuple[list[dict], dict, list[str]]:
    """(records, refs by unit, problems): every table built and every check run, nothing written.
    regulation: (by_id, children) of qc3626.json, loaded when not given."""
    by_id, children = regulation or ex.load_regulation()
    problems = verify_data(data, by_id)
    records, refs_by_unit = [], {}
    if problems:  # the typed data is wrong: do not build on it
        return records, refs_by_unit, problems
    for table in data["bang"]:
        record, refs = build_record(table, data, by_id, children, table_sha256)
        records.append(record)
        refs_by_unit[table["unit"]] = refs
    return records, refs_by_unit, verify_records(records)


def show(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def summary_lines(records: list[dict]) -> list[str]:
    lines = []
    for r in records:
        frag = r["fragment"] or {"nodes": [], "edges": []}
        labels = {}
        for n in frag["nodes"]:
            labels[n["labels"][0]] = labels.get(n["labels"][0], 0) + 1
        lines.append(f"  {r['unit']}: {r['metadata']['n_rows']} dòng | {len(frag['nodes'])} nút "
                     f"({', '.join(f'{k} {v}' for k, v in sorted(labels.items()))}) | {len(frag['edges'])} cạnh "
                     f"| {r['status']}")
    return lines


def main(argv=None, out_dir: Path = OUT_DIR, check_csv: Path = CHECK_CSV, tables_path: Path = TABLES_PATH) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="build and check, write nothing")
    args = parser.parse_args(argv)

    data = load_tables(tables_path)
    records, refs_by_unit, problems = build_all(data, file_sha256(tables_path))
    n_rows = sum(len(t["dong"]) for t in data["bang"])
    print(f"Bảng: {len(data['bang'])} | dòng: {n_rows} (schema Q10: {sum(EXPECTED_ROWS.values())}) | "
          f"file {show(tables_path)}")
    print("\n".join(summary_lines(records)))
    if problems:
        print(f"LỖI: {len(problems)} vấn đề:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print("Kiểm tra: ĐẠT (số dòng, nguyên văn, số, liền khoảng, thu_tu, validator, nhất quán giữa các fragment)")
    if args.check:
        print("--check: không ghi file.")
        return 0
    for record in records:
        ex.write_record(out_dir / f"{record['unit']}.json", record)
    write_csv(check_csv, csv_rows(data, refs_by_unit))
    print(f"Đã ghi {len(records)} fragment vào {show(out_dir)} và {show(check_csv)} ({n_rows} dòng kiểm tay)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
