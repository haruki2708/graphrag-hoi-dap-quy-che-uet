"""Build the blind calibration sheet for the GLM judge (30 answers the project owner grades by hand).

Run from the repo root:  python eval/build_judge_calibration.py [--overwrite]

Reads the two judged dev CSVs of 2026-10-27 (llm_only, rag_baseline) and picks 30 answers by fixed rules
(see select_rag / select_llm; the only randomness is random.Random(SEED)). Writes, standard library only:
  data/eval/calibration/judge_calib_v1.xlsx           sheets "huong_dan" and "cham" (blind: no system, label, run)
  data/eval/calibration/judge_calib_v1_manifest.json  stt -> (system, id, run) + reason + source commit
The manifest holds NO judge label (eval/judge_agreement.py joins labels back from the CSVs). The stdout
distribution table does show labels: it is for the reviewer, never copy it into the sheet.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import csv  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import random  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
import zipfile  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from xml.sax.saxutils import escape  # noqa: E402

from eval import retrieval_metrics as rm  # noqa: E402
from eval.judge_answers import JUDGE_SYSTEM, UNITS_PATH  # noqa: E402

SEED = 20261031
RESULTS_DIR = REPO_ROOT / "eval" / "results"
CALIB_DIR = REPO_ROOT / "data" / "eval" / "calibration"
SHEET_PATH = CALIB_DIR / "judge_calib_v1.xlsx"
MANIFEST_PATH = CALIB_DIR / "judge_calib_v1_manifest.json"
JUDGED_CSV = {
    "llm_only": RESULTS_DIR / "2026-10-27_llm_only_dev_judged.csv",
    "rag_baseline": RESULTS_DIR / "2026-10-27_rag_baseline_dev_judged.csv",
}
VN_TZ = timezone(timedelta(hours=7))

RAG_PINNED = ("dev-s04", "dev-s05")  # the two mot_phan answers of rag_baseline
RAG_LOW_PRECISION_COUNT = 9          # + 1 out_of_scope = 10 "dung" samples
LLM_OOS_COUNT = 2
# Stable-label llm_only questions added after the "label changes across runs" group: (type, label) -> count.
# Chosen so the 18 llm_only samples cover single/multi/conditional/out_of_scope with about 4 dung / 7 mot_phan / 7 sai.
LLM_STABLE_QUOTA = {("multi", "sai"): 3, ("multi", "mot_phan"): 1, ("conditional", "sai"): 2, ("single", "mot_phan"): 1}

SHEET_COLUMNS = ("stt", "cau_hoi", "dap_an_mau", "source_ids", "nguyen_van", "cau_tra_loi", "nhan", "ghi_chu")
COLUMN_WIDTHS = (6, 38, 52, 22, 90, 70, 12, 38)
NO_SOURCE_TEXT = "(ngoài phạm vi quy chế)"
NHAN_VALUES = ("dung", "mot_phan", "sai")
ZIP_DATE = (2026, 10, 27, 0, 0, 0)  # fixed so rebuilding gives a byte-identical xlsx


# --------------------------------------------------------------------------- selection

def load_rows(path: Path) -> dict[str, list[dict]]:
    """{question id: its judged rows sorted by run}."""
    by_id = defaultdict(list)
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_id[row["id"]].append(row)
    return {qid: sorted(rows, key=lambda r: int(r["run"])) for qid, rows in by_id.items()}


def pick_row(rows: list[dict], label: str | None = None) -> dict:
    """At most one row per (system, id): the lowest run (run 0 first), restricted to `label` when given."""
    for row in rows:
        if label is None or row["judge_label"] == label:
            return row
    raise LookupError(f"{rows[0]['id']}: no row with judge_label {label!r}")


def pick(system: str, row: dict, reason: str) -> dict:
    return {"system": system, "id": row["id"], "run": int(row["run"]), "reason": reason}


def select_rag(by_id: dict, qtype: dict, delegated: dict, rng: random.Random) -> list[dict]:
    """12 rag_baseline samples: dev-s04/s05, the 9 lowest-citation_precision "dung" answers, 1 out_of_scope."""
    picks = [pick("rag_baseline", pick_row(by_id[qid], "mot_phan"), "mẫu cố định theo yêu cầu (a)") for qid in RAG_PINNED]
    candidates = []
    for qid, rows in by_id.items():
        if qid in RAG_PINNED or qtype[qid] == "out_of_scope":
            continue
        row = pick_row(rows, "dung")
        candidates.append((float(row["citation_precision"]), qid, row))
    candidates.sort(key=lambda c: (c[0], c[1]))
    chosen = candidates[:RAG_LOW_PRECISION_COUNT]
    if chosen[-1][0] == candidates[RAG_LOW_PRECISION_COUNT][0]:
        raise RuntimeError("tie at the citation_precision cutoff; extend the tie-break rule before building")
    for precision, qid, row in chosen:
        picks.append(pick("rag_baseline", row, f"nằm trong {RAG_LOW_PRECISION_COUNT} câu có citation_precision thấp nhất "
                                               f"(lượt {row['run']}: {precision:g}); loại {qtype[qid]}"))
    types = {qtype[p["id"]] for p in picks}
    if not {"multi", "conditional"} <= types or not any(delegated[p["id"]] for p in picks):
        raise RuntimeError("rag_baseline sample misses multi, conditional or delegated; adjust the rule by hand")
    oos_ids = sorted(qid for qid, t in qtype.items() if t == "out_of_scope" and qid in by_id)
    oos = rng.choice(oos_ids)
    picks.append(pick("rag_baseline", pick_row(by_id[oos], "dung"), f"câu out_of_scope chọn ngẫu nhiên (seed {SEED})"))
    return picks


def select_llm(by_id: dict, qtype: dict, taken_by_rag: set[str], rng: random.Random) -> list[dict]:
    """18 llm_only samples: every question whose label changes across runs (run 0), 2 out_of_scope, stable fill by quota."""
    picks, used = [], set()
    unstable = sorted(qid for qid, rows in by_id.items()
                      if qtype[qid] != "out_of_scope" and len({r["judge_label"] for r in rows}) > 1)
    for qid in unstable:
        picks.append(pick("llm_only", by_id[qid][0], "nhãn của giám khảo đổi giữa 3 lượt (ưu tiên); lấy lượt 0"))
        used.add(qid)
    oos_ids = sorted(qid for qid, t in qtype.items() if t == "out_of_scope" and qid in by_id)
    oos_free = [q for q in oos_ids if q not in taken_by_rag]
    for qid in sorted(rng.sample(oos_free, LLM_OOS_COUNT)):
        picks.append(pick("llm_only", by_id[qid][0], f"câu out_of_scope chọn ngẫu nhiên, khác câu của hệ kia (seed {SEED})"))
        used.add(qid)
    for (t, label), count in sorted(LLM_STABLE_QUOTA.items()):
        pool = sorted(qid for qid, rows in by_id.items() if qid not in used and qtype[qid] == t
                      and {r["judge_label"] for r in rows} == {label})
        fresh = [q for q in pool if q not in taken_by_rag]  # prefer questions the other system does not already show
        if len(fresh) < count:
            fresh = pool
        for qid in sorted(rng.sample(fresh, count)):
            picks.append(pick("llm_only", by_id[qid][0], f"câu ổn định giữa 3 lượt, bốc ngẫu nhiên theo quota loại {t} (seed {SEED})"))
            used.add(qid)
    return picks


def select_samples() -> tuple[list[dict], dict, dict]:
    """Return (30 picks in shuffled order with stt, {system: by_id}, {id: question})."""
    questions = {q["id"]: q for q in rm.load_dev_questions()}
    qtype = {qid: q["type"] for qid, q in questions.items()}
    delegated = {qid: q["delegated"] for qid, q in questions.items()}
    judged = {system: load_rows(path) for system, path in JUDGED_CSV.items()}
    rng = random.Random(SEED)
    rag = select_rag(judged["rag_baseline"], qtype, delegated, rng)
    llm = select_llm(judged["llm_only"], qtype, {p["id"] for p in rag}, rng)
    picks = rag + llm
    rng.shuffle(picks)
    for stt, p in enumerate(picks, start=1):
        p["stt"] = stt
    return picks, judged, questions


# --------------------------------------------------------------------------- sheet content

def guide_lines() -> list[str]:
    """Instruction sheet; the label definitions are copied from JUDGE_SYSTEM so they cannot drift."""
    definitions = [ln for ln in JUDGE_SYSTEM.splitlines() if ln.startswith('- "')]
    principle = next(ln for ln in JUDGE_SYSTEM.splitlines() if ln.startswith("Chỉ dựa vào"))
    if len(definitions) != 3:
        raise RuntimeError("expected 3 label definitions in JUDGE_SYSTEM")
    return [
        "PHIẾU CHẤM TAY 30 CÂU TRẢ LỜI (hiệu chỉnh giám khảo) — chấm mù",
        "",
        "Mục đích: so nhãn của bạn với nhãn của giám khảo máy để biết giám khảo có đáng tin không. "
        "Chấm theo đúng tiêu chí mà giám khảo máy được giao, dưới đây (chép nguyên văn từ prompt giám khảo):",
        "",
        principle,
        "Gán đúng một nhãn cho câu trả lời cần chấm:",
        *definitions,
        "",
        "Cách điền:",
        "1. Mở sheet \"cham\". Mỗi dòng là một mẫu: đọc cau_hoi, dap_an_mau, nguyen_van (nguyên văn các khoản nguồn), cau_tra_loi.",
        "2. Điền cột nhan bằng đúng một trong ba giá trị: dung, mot_phan, sai (chữ thường, không dấu, đúng chính tả; ô có menu thả xuống). "
        "Ô trống hoặc sai chính tả thì script tính độ trùng sẽ báo lỗi theo stt và không tính.",
        "3. Cột ghi_chu: ghi ngắn lý do, nhất là khi phân vân giữa hai nhãn. Không bắt buộc nhưng rất có ích khi xem các mẫu lệch.",
        "4. Chấm từng dòng độc lập; thứ tự đã xáo trộn. Một số câu hỏi có thể xuất hiện hai lần với câu trả lời khác nhau.",
        "5. Câu ngoài phạm vi quy chế: nguyên văn ghi \"" + NO_SOURCE_TEXT + "\"; chấm theo đáp án mẫu.",
        "6. Ô nguyen_van dài có thể bị cắt khi hiển thị (Excel giới hạn chiều cao dòng): bấm vào ô để đọc đủ ở thanh công thức.",
        "7. Lưu bằng tên khác, ví dụ judge_calib_v1_filled.xlsx; không ghi đè phiếu gốc. Không sửa các cột khác.",
        "",
        "Chấm mù: chưa mở file *_judged.csv, *_judged.json, judge_calib_v1_manifest.json hay kết quả summary trước khi chấm xong cả 30 dòng.",
        "Chấm xong: python eval/judge_agreement.py <đường dẫn phiếu đã chấm>",
    ]


def unit_texts() -> dict[str, str]:
    with UNITS_PATH.open(encoding="utf-8") as f:
        return {u["id"]: u["text"] for u in map(json.loads, f)}


def sheet_rows(picks: list[dict], judged: dict, questions: dict) -> list[list]:
    texts = unit_texts()
    rows = []
    for p in sorted(picks, key=lambda p: p["stt"]):
        q = questions[p["id"]]
        csv_row = next(r for r in judged[p["system"]][p["id"]] if int(r["run"]) == p["run"])
        source = "\n\n".join(f"[{uid}]\n{texts[uid]}" for uid in q["source_ids"]) or NO_SOURCE_TEXT
        rows.append([p["stt"], q["question"], q["answer"], " ".join(q["source_ids"]), source, csv_row["answer"], None, None])
    return rows


# --------------------------------------------------------------------------- xlsx writer (stdlib only)

def col_letter(index: int) -> str:
    return chr(ord("A") + index)


def _cell(ref: str, value, style: int) -> str:
    if value is None:
        return f'<c r="{ref}" s="{style}"/>'
    if isinstance(value, int):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'


def worksheet_xml(rows: list[list], widths: tuple, header_rows: int, input_cols: tuple = (), validation: str = "") -> str:
    body = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            style = 2 if r <= header_rows else (3 if c in input_cols else 1)
            cells.append(_cell(f"{col_letter(c)}{r}", value, style))
        body.append(f'<row r="{r}">{"".join(cells)}</row>')
    cols = "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths, start=1))
    pane = ('<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>' if header_rows else "")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetViews><sheetView workbookViewId="0">{pane}</sheetView></sheetViews>'
            f'<cols>{cols}</cols><sheetData>{"".join(body)}</sheetData>{validation}</worksheet>')


STYLES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
    '<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFD9E1F2"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFFFF2CC"/></patternFill></fill></fills>'
    '<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>'
    '<border><left style="thin"><color rgb="FFBFBFBF"/></left><right style="thin"><color rgb="FFBFBFBF"/></right>'
    '<top style="thin"><color rgb="FFBFBFBF"/></top><bottom style="thin"><color rgb="FFBFBFBF"/></bottom><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="4">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">'
    '<alignment vertical="center" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="0" fillId="3" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1">'
    '<alignment vertical="top" wrapText="1"/></xf>'
    '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')


def write_xlsx(path: Path, sheets: list[tuple[str, str]]) -> None:
    """sheets = [(name, worksheet xml)]. Minimal SpreadsheetML package; Excel and LibreOffice open it."""
    n = len(sheets)
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                     '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                     + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                               for i in range(1, n + 1)) + '</Types>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                 '</Relationships>')
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                + "".join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in enumerate(sheets, start=1))
                + '</sheets></workbook>')
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                         f'Target="worksheets/sheet{i}.xml"/>' for i in range(1, n + 1))
               + f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
                 f'Target="styles.xml"/></Relationships>')
    parts = [("[Content_Types].xml", content_types), ("_rels/.rels", root_rels), ("xl/workbook.xml", workbook),
             ("xl/_rels/workbook.xml.rels", wb_rels), ("xl/styles.xml", STYLES_XML)]
    parts += [(f"xl/worksheets/sheet{i}.xml", xml) for i, (_, xml) in enumerate(sheets, start=1)]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, xml in parts:
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, xml.encode("utf-8"))


def build_sheets(rows: list[list]) -> list[tuple[str, str]]:
    n = len(rows)
    guide_xml = worksheet_xml([[line] for line in guide_lines()], (120,), header_rows=0)
    validation = (f'<dataValidations count="1"><dataValidation type="list" allowBlank="1" showErrorMessage="1" '
                  f'errorTitle="Nhãn không hợp lệ" error="Chỉ dùng: {", ".join(NHAN_VALUES)}" sqref="G2:G{n + 1}">'
                  f'<formula1>"{",".join(NHAN_VALUES)}"</formula1></dataValidation></dataValidations>')
    cham_xml = worksheet_xml([list(SHEET_COLUMNS)] + rows, COLUMN_WIDTHS, header_rows=1, input_cols=(6, 7), validation=validation)
    return [("huong_dan", guide_xml), ("cham", cham_xml)]


# --------------------------------------------------------------------------- manifest + report

def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_commit() -> str:
    out = subprocess.run(["git", "log", "-1", "--format=%H", "--", *map(str, JUDGED_CSV.values())],
                         cwd=REPO_ROOT, capture_output=True, text=True)
    return out.stdout.strip() or "unknown"


def manifest_dict(picks: list[dict]) -> dict:
    return {
        "version": "judge_calib_v1",
        "created": datetime.now(VN_TZ).isoformat(timespec="seconds"),
        "seed": SEED,
        "source_commit": source_commit(),
        "sources": {s: {"file": p.relative_to(REPO_ROOT).as_posix(), "sha256": file_sha256(p)} for s, p in JUDGED_CSV.items()},
        "note": "Contains NO judge label. Do not open before the sheet is graded (it reveals the system of each stt).",
        "samples": {str(p["stt"]): {"system": p["system"], "id": p["id"], "run": p["run"], "reason": p["reason"]}
                    for p in sorted(picks, key=lambda p: p["stt"])},
    }


def distribution_report(picks: list[dict], judged: dict, questions: dict) -> str:
    lines = ["system x type x GLM label (reviewer only; NOT part of the sheet):"]
    counts = Counter()
    for p in picks:
        row = next(r for r in judged[p["system"]][p["id"]] if int(r["run"]) == p["run"])
        counts[(p["system"], questions[p["id"]]["type"], row["judge_label"])] += 1
    for key in sorted(counts):
        lines.append(f"  {key[0]:13} {key[1]:13} {key[2]:9} {counts[key]}")
    for system in JUDGED_CSV:
        by_label = Counter(k[2] for k in counts.elements() if k[0] == system)
        lines.append(f"  total {system}: {sum(by_label.values())} " + ", ".join(f"{k}={by_label[k]}" for k in sorted(by_label)))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    overwrite = "--overwrite" in argv
    existing = [p.name for p in (SHEET_PATH, MANIFEST_PATH) if p.exists()]
    if existing and not overwrite:
        print(f"LỖI: đã có {existing}; không ghi đè (dùng --overwrite nếu chắc chắn chưa phát phiếu).", file=sys.stderr)
        return 2
    picks, judged, questions = select_samples()
    CALIB_DIR.mkdir(parents=True, exist_ok=True)
    write_xlsx(SHEET_PATH, build_sheets(sheet_rows(picks, judged, questions)))
    MANIFEST_PATH.write_text(json.dumps(manifest_dict(picks), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Ghi {SHEET_PATH.relative_to(REPO_ROOT)} và {MANIFEST_PATH.relative_to(REPO_ROOT)} ({len(picks)} mẫu)")
    print(distribution_report(picks, judged, questions))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
