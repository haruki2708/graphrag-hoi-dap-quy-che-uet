"""Export the semantic triplets of the extraction records to CSV for hand-checking (PLAN T4 "Xong khi").

Run from the repo root:
    python src/kg/export_triplets.py [--role extract] [--run 0] [--sample 30 --seed 1] [--exclude-nguon-loai bang]

One row per semantic edge (head node - edge - tail node). A triplet is correct when both nodes have the right label
and name, the edge type is right, the key properties are right (min/max/min_incl/max_incl, co, boi_canh, phu_dinh,
the ket_hop of the parent group) and nguon is right; one wrong numeric property makes the whole triplet wrong
(definition in docs/PLAN.md section 5). Columns verdict and note are left empty for the reviewer.
Only records with status "ok" have a fragment; the others are counted and reported, not exported.
A blind two-model scoring mode is not built yet (waits for the project owner's decision).
"""

import argparse
import csv
import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.kg.extract import OUT_ROOT, PROCESSED_JSON, load_regulation  # noqa: E402
from src.kg.structure import CONTENT_UNITS, load_content_units  # noqa: E402

SOURCE_TEXT_CHARS = 600
CSV_FIELDS = ("triplet_id", "unit", "nguon", "head_label", "head_ten", "edge_type", "edge_props", "tail_label",
              "tail_ten", "head_props", "tail_props", "group_ket_hop", "nguon_text", "verdict", "note")
NODE_PROPS_SHOWN_ELSEWHERE = ("id", "ten")


def load_records(role: str, run: int, out_dir: Path = OUT_ROOT) -> list[dict]:
    """Records of one role and run, sorted by file name (run 0: <unit>.json; run N: <unit>.runN.json)."""
    folder = out_dir / role
    if not folder.is_dir():
        return []
    records = []
    for path in sorted(folder.glob("*.json")):
        stem = path.name[:-len(".json")]
        is_other_run = ".run" in stem and stem.rsplit(".run", 1)[1].isdigit()
        if (run == 0 and is_other_run) or (run != 0 and not stem.endswith(f".run{run}")):
            continue
        records.append(json.loads(path.read_text(encoding="utf-8")))
    return records


def source_texts(processed_json: Path = PROCESSED_JSON, content_units_path: Path = CONTENT_UNITS) -> dict[str, str]:
    """Verbatim text per unit id: the content-unit text (a Dieu = its lead text only), else the unit's own text."""
    by_id, _ = load_regulation(processed_json)
    texts = {uid: u["text"] for uid, u in by_id.items()}
    texts.update({uid: u["text"] for uid, u in load_content_units(content_units_path).items()})
    return texts


def _cut(text: str, limit: int = SOURCE_TEXT_CHARS) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def fragment_triplets(record: dict, texts: dict[str, str], exclude_nguon_loai: set[str] = frozenset()) -> list[dict]:
    fragment = record["fragment"]
    nodes = {n["id"]: n for n in fragment["nodes"]}
    parents: dict[str, list[str]] = {}
    for e in fragment["edges"]:
        if e["type"] == "GOM":
            parents.setdefault(e["to"], []).append(e["from"])

    def group_ket_hop(node: dict) -> str:
        # The group a triplet hangs from: the head itself when it is a group, else the group(s) that GOM it
        if node["labels"][0] == "NhomDieuKien":
            return node["props"]["ket_hop"]
        return "|".join(nodes[p]["props"]["ket_hop"] for p in parents.get(node["id"], []))

    rows = []
    for i, e in enumerate(fragment["edges"]):
        head, tail = nodes[e["from"]], nodes[e["to"]]
        if {head["props"].get("nguon_loai"), tail["props"].get("nguon_loai")} & exclude_nguon_loai:
            continue
        nguon = e["props"]["nguon"]
        rows.append({
            "triplet_id": f"{record['unit']}.e{i}",
            "unit": record["unit"],
            "nguon": nguon,
            "head_label": head["labels"][0], "head_ten": head["props"]["ten"],
            "edge_type": e["type"],
            "edge_props": _json({k: v for k, v in e["props"].items() if k != "nguon"}),
            "tail_label": tail["labels"][0], "tail_ten": tail["props"]["ten"],
            "head_props": _json({k: v for k, v in head["props"].items() if k not in NODE_PROPS_SHOWN_ELSEWHERE}),
            "tail_props": _json({k: v for k, v in tail["props"].items() if k not in NODE_PROPS_SHOWN_ELSEWHERE}),
            "group_ket_hop": group_ket_hop(head),
            "nguon_text": _cut(texts.get(nguon, "")),
            "verdict": "", "note": "",
        })
    return rows


def collect(records: list[dict], texts: dict[str, str], exclude_nguon_loai: set[str] = frozenset()) -> tuple[list[dict], dict]:
    rows, skipped = [], {}
    for record in records:
        if record["status"] != "ok":
            skipped[record["unit"]] = record["status"]
            continue
        rows += fragment_triplets(record, texts, exclude_nguon_loai)
    return rows, skipped


def sample_rows(rows: list[dict], n: int | None, seed: int) -> list[dict]:
    """A fixed random sample: the same rows, seed and n always give the same picks (in their original order)."""
    if n is None or n >= len(rows):
        return rows
    picked = set(random.Random(seed).sample(range(len(rows)), n))
    return [r for i, r in enumerate(rows) if i in picked]


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def default_out(out_dir: Path, role: str, run: int, sample: int | None, seed: int) -> Path:
    name = f"{role}_run{run}_triplets" + (f"_sample{sample}_seed{seed}" if sample is not None else "") + ".csv"
    return out_dir / name


def main(argv=None, out_dir: Path = OUT_ROOT) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--role", default="extract")
    parser.add_argument("--run", type=int, default=0)
    parser.add_argument("--sample", type=int, help="export a fixed random sample of N triplets")
    parser.add_argument("--seed", type=int, default=0, help="seed of the sample (keep it to reproduce the sample)")
    parser.add_argument("--exclude-nguon-loai", action="append", default=[], choices=("trich_xuat", "bang", "nhap_tay"),
                        help="drop triplets touching a node of this nguon_loai (repeatable)")
    parser.add_argument("--out", type=Path, help="CSV path (default: data/processed/semantic/<role>_run<N>_triplets*.csv)")
    args = parser.parse_args(argv)
    if args.sample is not None and args.sample <= 0:
        print("LỖI: --sample phải > 0.", file=sys.stderr)
        return 2

    records = load_records(args.role, args.run, out_dir)
    if not records:
        print(f"LỖI: không có bản ghi nào ở {(out_dir / args.role).as_posix()} cho run {args.run}.", file=sys.stderr)
        return 1
    rows, skipped = collect(records, source_texts(), set(args.exclude_nguon_loai))
    picked = sample_rows(rows, args.sample, args.seed)
    out = args.out or default_out(out_dir, args.role, args.run, args.sample, args.seed)
    write_csv(out, picked)
    print(f"{len(records)} bản ghi | {len(rows)} bộ ba | xuất {len(picked)} dòng"
          + (f" (mẫu seed {args.seed})" if args.sample is not None else "") + f" -> {out.as_posix()}")
    if skipped:
        print(f"Bỏ qua {len(skipped)} đơn vị không ok: " + ", ".join(f"{u} ({s})" for u, s in skipped.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
