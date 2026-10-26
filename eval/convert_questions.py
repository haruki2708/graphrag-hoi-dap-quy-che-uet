"""Convert the question draft spreadsheet into the v1 JSONL files.

Run from the repo root:  python eval/convert_questions.py

Reads data/eval/drafts/cau_hoi_v1.xlsx (sheets "test" and "dev"; columns id, type, delegated, question,
answer, source_ids, ref_points, ghi_chu) with the standard library only, and writes
data/eval/questions_v1_test.jsonl and data/eval/questions_v1_dev.jsonl. The dev file also gets the
11 trial questions from docs/schema_v1.md section 3 (origin "generated").

Test-split rule: this script prints only counts and ids, never question or answer text.
"""

import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

REPO_ROOT = Path(__file__).resolve().parent.parent
XLSX_PATH = REPO_ROOT / "data" / "eval" / "drafts" / "cau_hoi_v1.xlsx"
OUT_DIR = REPO_ROOT / "data" / "eval"
DOC_ID = "qc3626"

KEY_ORDER = ("id", "question", "answer", "source_ids", "type", "split", "delegated", "origin", "ref_points")
COLUMNS = ("id", "type", "delegated", "question", "answer", "source_ids", "ref_points", "ghi_chu")
SPLITS = ("test", "dev")

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
PKG_REL_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}

TRUE_VALUES = {"1", "true", "x", "có", "co", "yes", "y"}
FALSE_VALUES = {"0", "false", "", "không", "khong", "no", "n"}

# Trial questions from docs/schema_v1.md section 3 (rows 1, 2, 3a, 3b, 4, 5, 6, 7, 8, 9, 10).
# Question text and source_ids as in the table; ref_points from the "Điểm: …" notes in its last column.
GENERATED = [
    ("dev-s01", "single", False, "Điểm chữ C+ được quy đổi thành bao nhiêu điểm theo thang 4?",
     "2,5: điểm chữ C+ tương ứng với 2,5 trên thang điểm 4.",
     ["d43.k1"], []),
    ("dev-s02", "conditional", False, "SV CTĐT chất lượng cao đã tích lũy 50 tín chỉ là SV năm thứ mấy?",
     "Năm thứ hai: với chương trình đào tạo chất lượng cao, tích lũy từ 40 đến 80 tín chỉ là sinh viên năm thứ hai "
     "(số tín chỉ tích lũy không kể học phần tự chọn tự do, giáo dục thể chất, giáo dục quốc phòng - an ninh và kỹ năng bổ trợ).",
     ["d17.k2"], []),
    ("dev-s03", "multi", False, "Điểm TBC tích lũy được tính thế nào?",
     "Là điểm trung bình theo trọng số tín chỉ của các học phần đã được đánh giá loại đạt mà sinh viên đã tích lũy, "
     "tính từ đầu khóa học đến thời điểm xem xét; tính theo công thức A = Σ(a_i × n_i) / Σ n_i và làm tròn đến 2 chữ số thập phân.",
     ["d43.k2", "d42.k5"], []),
    ("dev-s04", "multi", False, "Những học phần nào không được tính vào điểm TBC tích lũy?",
     "Học phần điều kiện: giáo dục thể chất, giáo dục quốc phòng - an ninh, kỹ năng bổ trợ (Điều 5 khoản 2 điểm g); "
     "học phần tự chọn tự do (Điều 21 khoản 2 điểm d); học phần chỉ yêu cầu đạt, loại P (Điều 41 khoản 2 điểm b); "
     "học phần chưa đạt, vì điểm TBC tích lũy chỉ tính các học phần đạt (Điều 42 khoản 5).",
     ["d5.k2", "d21.k2", "d41.k2", "d42.k5"], ["d5.k2.g", "d21.k2.d", "d41.k2.b"]),
    ("dev-s05", "multi", False, "SV CTĐT chất lượng cao cần những điều kiện gì để được xét tốt nghiệp?",
     "Đủ 8 điều kiện a–h ở Điều 46 khoản 1; với chương trình chất lượng cao: điểm TBC tích lũy từ 2,50 trở lên và "
     "trình độ ngoại ngữ bậc 4 (Điều 13 khoản 3); điều kiện ở điểm e do ĐHQGHN và đơn vị đào tạo quy định.",
     ["d46.k1", "d13.k3"], ["d46.k1.d", "d13.k3.b"]),
    ("dev-s06", "multi", False, "Khi nào SV bị buộc thôi học?",
     "Khi thuộc một trong 5 trường hợp (Điều 44 khoản 2): số lần cảnh báo kết quả học tập vượt giới hạn do thủ trưởng "
     "đơn vị đào tạo quy định; vượt quá thời gian tối đa được phép học (Điều 17 khoản 1); bị kỷ luật lần thứ hai vì thi hộ "
     "hoặc nhờ người thi hộ (Điều 35 khoản 4 điểm d), hoặc bị kỷ luật ở mức xóa tên; không đăng ký học 2 học kỳ liên tiếp; "
     "hết hạn nghỉ học tạm thời mà không có đơn xin học trở lại (Điều 36 khoản 3).",
     ["d44.k2", "d17.k1", "d35.k4", "d36.k3"], ["d17.k1.d", "d35.k4.d"]),
    ("dev-s07", "single", True, "Học phí ngành KHMT năm 2026 là bao nhiêu?",
     "Quy chế không quy định mức học phí bằng tiền. Quy chế chỉ nêu công thức tính học phí và giao thủ trưởng đơn vị "
     "đào tạo xây dựng định mức học phí cho một tín chỉ (Điều 8 khoản 1 điểm b).",
     ["d8.k1"], ["d8.k1.b"]),
    ("dev-s08", "conditional", True, "SV CLC tích lũy 50 tín chỉ, TBC tích lũy 1,35 thì có bị cảnh báo học vụ không?",
     "Thuộc diện cảnh báo: chương trình chất lượng cao tích lũy 50 tín chỉ là sinh viên năm thứ hai (Điều 17 khoản 2); "
     "năm thứ hai bị cảnh báo khi điểm TBC tích lũy dưới 1,40 (Điều 44 khoản 1 điểm a), và 1,35 < 1,40. Tuy nhiên thủ "
     "trưởng đơn vị đào tạo chọn áp dụng 1–2 trong 3 điều kiện cảnh báo, nên cần đối chiếu quy định của UET.",
     ["d17.k2", "d44.k1"], ["d44.k1.a"]),
    ("dev-s09", "single", True, "Ở UET, bị cảnh báo học vụ mấy lần thì bị buộc thôi học?",
     "QĐ 3626 không nêu số lần cụ thể: thủ trưởng đơn vị đào tạo quy định số lần cảnh báo học vụ, nhưng không quá 2 lần "
     "liên tiếp (Điều 44 khoản 1); vượt giới hạn này thì bị buộc thôi học (Điều 44 khoản 2 điểm a).",
     ["d44.k2", "d44.k1"], ["d44.k2.a"]),
    ("dev-s10", "multi", False, "Học phần GDTC có tính vào điểm TBC tích lũy không, có cần để tốt nghiệp không?",
     "Không tính vào điểm TBC tích lũy (giáo dục thể chất là học phần điều kiện, Điều 5 khoản 2 điểm g; Điều 43 khoản 2), "
     "nhưng phải đạt mới đủ điều kiện xét tốt nghiệp (Điều 46 khoản 1 điểm g).",
     ["d5.k2", "d43.k2", "d46.k1"], ["d5.k2.g", "d46.k1.g"]),
    ("dev-s11", "conditional", False, "SV đang bị cảnh báo học vụ có được chuyển ngành không?",
     "Không: một điều kiện chuyển ngành là không bị cảnh báo học tập hoặc bị xét thi hành kỷ luật (Điều 28 khoản 1 điểm d); "
     "ngoài ra từ năm thứ hai không được xét chuyển ngành (Điều 28 khoản 2).",
     ["d28.k1", "d28.k2"], ["d28.k1.d"]),
]


def split_ids(value) -> list[str]:
    """Split a cell of unit codes on spaces, newlines, ';' or ','; drop empties; add the qc3626. prefix."""
    if value is None:
        return []
    parts = re.split(r"[\s;,]+", str(value).strip())
    return [p if p.startswith(f"{DOC_ID}.") else f"{DOC_ID}.{p}" for p in parts if p]


def parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    raise ValueError(f"cannot read {value!r} as a boolean")


def read_xlsx(path: Path) -> dict[str, list[list]]:
    """Return {sheet name: rows}, each row a list of cell values (str, bool or None), using only the stdlib."""
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        rels = {
            r.get("Id"): r.get("Target")
            for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")).findall("r:Relationship", PKG_REL_NS)
        }
        sheets = {}
        for sheet in ET.fromstring(z.read("xl/workbook.xml")).find("m:sheets", NS):
            target = rels[sheet.get(REL_NS)].lstrip("/")
            target = target if target.startswith("xl/") else f"xl/{target}"
            sheets[sheet.get("name")] = _read_sheet(ET.fromstring(z.read(target)), shared)
    return sheets


def _column_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref).group(0)
    index = 0
    for ch in letters:
        index = index * 26 + ord(ch) - ord("A") + 1
    return index - 1


def _read_sheet(root, shared: list[str]) -> list[list]:
    rows = []
    for row in root.iter(f"{{{NS['m']}}}row"):
        values = {}
        for cell in row.findall("m:c", NS):
            kind = cell.get("t")
            v = cell.find("m:v", NS)
            raw = v.text if v is not None else None
            if kind == "s" and raw is not None:
                value = shared[int(raw)]
            elif kind == "b":
                value = raw == "1"
            elif kind == "inlineStr":
                value = "".join(t.text or "" for t in cell.iter(f"{{{NS['m']}}}t"))
            elif raw is not None and re.fullmatch(r"-?\d+(\.0+)?", raw):
                value = str(int(float(raw)))  # integer ids stored as numbers
            else:
                value = raw
            values[_column_index(cell.get("r"))] = value
        if values:
            rows.append([values.get(i) for i in range(max(values) + 1)])
    return rows


def records_from_sheet(rows: list[list], split: str) -> list[dict]:
    """Rows of one sheet (first row = header) -> JSONL records; rows without a question are skipped."""
    header = [str(h or "").strip().lower() for h in rows[0]]
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        raise ValueError(f"sheet {split!r}: missing columns {missing}")
    col = {name: header.index(name) for name in COLUMNS}
    records = []
    for row in rows[1:]:
        cell = lambda name: row[col[name]] if col[name] < len(row) else None  # noqa: E731
        question = str(cell("question") or "").strip()
        if not question:
            continue
        try:
            delegated = parse_bool(cell("delegated"))
        except ValueError as e:
            raise ValueError(f"sheet {split!r}, id {cell('id')!r}: delegated: {e}") from None
        record = {
            "id": str(cell("id") or "").strip(),
            "question": question,
            "answer": str(cell("answer") or "").strip(),
            "source_ids": split_ids(cell("source_ids")),
            "type": str(cell("type") or "").strip(),
            "split": split,
            "delegated": delegated,
            "origin": "manual",
            "ref_points": split_ids(cell("ref_points")),
        }
        records.append({k: record[k] for k in KEY_ORDER})
    return records


def generated_records() -> list[dict]:
    records = []
    for qid, qtype, delegated, question, answer, sources, points in GENERATED:
        record = {
            "id": qid, "question": question, "answer": answer, "source_ids": split_ids(" ".join(sources)),
            "type": qtype, "split": "dev", "delegated": delegated, "origin": "generated",
            "ref_points": split_ids(" ".join(points)),
        }
        records.append({k: record[k] for k in KEY_ORDER})
    return records


def write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sheets = read_xlsx(XLSX_PATH)
    for split in SPLITS:
        if split not in sheets:
            print(f"Thiếu sheet {split!r} trong {XLSX_PATH.name}", file=sys.stderr)
            return 1
    out = {split: records_from_sheet(sheets[split], split) for split in SPLITS}
    out["dev"] += generated_records()
    for split in SPLITS:
        path = OUT_DIR / f"questions_v1_{split}.jsonl"
        write_jsonl(path, out[split])
        print(f"{path.relative_to(REPO_ROOT).as_posix()}: {len(out[split])} câu")
    return 0


if __name__ == "__main__":
    sys.exit(main())
