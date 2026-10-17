"""Tách văn bản quy chế (PDF có lớp chữ) thành chương / điều / khoản / điểm.

Chạy từ thư mục gốc repo:
    python src/ingest/parse_regulation.py

Đọc mọi PDF trong data/raw/core/, ghi data/processed/<doc_id>.json và in bảng kiểm tra
(danh sách "Điều N. Tên điều" theo chương, số khoản mỗi điều).

Mã đơn vị: <doc>.c{N}, <doc>.d{N}, <doc>.d{N}.k{M}, <doc>.d{N}.k{M}.{điểm}
(ví dụ qc3626.c3, qc3626.d12, qc3626.d12.k2, qc3626.d12.k2.a).

Quy tắc nhận diện:
- Chỉ lấy từ dòng in đậm "Chương I" đầu tiên trở đi → bỏ qua phần Quyết định ban hành (nếu có).
- Chương / Điều: dòng bắt đầu bằng "Chương <La Mã>" / "Điều N." và in đậm; tên có thể xuống dòng
  (các dòng in đậm liền sau). Số điều phải tăng liên tiếp.
- Khoản: dòng bắt đầu "M. " với M = khoản trước + 1. Điểm: dòng bắt đầu "x) " theo thứ tự chữ cái
  tiếng Việt (a, b, c, d, đ, e, g, ...). Điều kiện liên tiếp giúp tránh nhận nhầm dòng bị ngắt giữa câu.
- Bỏ dòng số trang và header/footer lặp lại ở mép trên/dưới trang.
"""

import json
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw" / "core"
OUT_DIR = REPO_ROOT / "data" / "processed"

POINT_LETTERS = "abcdđeghiklmnopqrstuvxy"
ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50}

RE_CHAPTER = re.compile(r"^Chương\s+([IVXL]+)\b\s*(.*)$")
RE_ARTICLE = re.compile(r"^Điều\s+(\d+)\s*\.\s*(.*)$")
RE_CLAUSE = re.compile(r"^(\d{1,2})\.\s+\S")
RE_POINT = re.compile(r"^([a-zđ])\)\s*\S")
RE_PAGE_NUMBER = re.compile(r"^\d{1,3}$")

# Dòng nằm trong dải này (tỷ lệ chiều cao trang) mới được xét là header/footer
EDGE_BAND = 0.08
# Dòng ở mép trang lặp lại trên ít nhất tỷ lệ số trang này thì coi là header/footer
REPEAT_RATIO = 0.5


@dataclass
class Line:
    text: str
    page: int  # đánh số từ 1
    bold: bool


@dataclass
class Unit:
    id: str
    type: str  # chuong | dieu | khoan | diem
    number: str
    parent_id: str | None
    title: str | None = None
    lines: list[Line] = field(default_factory=list)
    children: list["Unit"] = field(default_factory=list)


def roman_to_int(s: str) -> int:
    total = 0
    for i, ch in enumerate(s):
        v = ROMAN[ch]
        total += -v if i + 1 < len(s) and ROMAN[s[i + 1]] > v else v
    return total


def doc_id_from_filename(path: Path) -> str:
    m = re.search(r"\d{3,5}", path.stem)
    if not m:
        raise ValueError(f"Không suy ra được số quyết định từ tên file: {path.name}")
    return f"qc{m.group(0)}"


def read_lines(pdf_path: Path) -> tuple[list[Line], list[str]]:
    """Đọc PDF thành danh sách dòng (đã NFC), đã bỏ số trang và header/footer lặp."""
    doc = pymupdf.open(pdf_path)
    pages: list[list[tuple[str, bool, bool]]] = []  # (text, bold, ở mép trang)
    for page in doc:
        height = page.rect.height
        rows = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans = [s for s in line["spans"] if s["text"].strip()]
                if not spans:
                    continue
                text = unicodedata.normalize("NFC", "".join(s["text"] for s in line["spans"]))
                text = re.sub(r"\s+", " ", text).strip()
                bold = all(s["flags"] & 16 or "Bold" in s["font"] for s in spans)
                y0, y1 = line["bbox"][1], line["bbox"][3]
                at_edge = y1 < height * EDGE_BAND or y0 > height * (1 - EDGE_BAND)
                rows.append((text, bold, at_edge))
        pages.append(rows)

    # Header/footer: dòng ở mép trang, thay chữ số bằng "#", lặp trên nhiều trang
    edge_counts = Counter()
    for rows in pages:
        edge_counts.update({re.sub(r"\d+", "#", t) for t, _, edge in rows if edge})
    repeated = {t for t, n in edge_counts.items() if n >= max(2, REPEAT_RATIO * len(pages))}

    lines: list[Line] = []
    removed: Counter = Counter()
    for pno, rows in enumerate(pages, start=1):
        for text, bold, at_edge in rows:
            if at_edge and (RE_PAGE_NUMBER.match(text) or re.sub(r"\d+", "#", text) in repeated):
                removed["số trang" if RE_PAGE_NUMBER.match(text) else f"lặp: {text[:40]}"] += 1
                continue
            lines.append(Line(text, pno, bold))
    removed_report = [f"{k} × {v}" for k, v in removed.items()]
    return lines, removed_report


def next_letter(prev: str | None) -> str | None:
    if prev is None:
        return POINT_LETTERS[0]
    i = POINT_LETTERS.index(prev)
    return POINT_LETTERS[i + 1] if i + 1 < len(POINT_LETTERS) else None


def parse(lines: list[Line], doc_id: str) -> tuple[dict, list[Unit], list[str]]:
    warnings: list[str] = []

    start = next(
        (i for i, ln in enumerate(lines) if ln.bold and RE_CHAPTER.match(ln.text)
         and RE_CHAPTER.match(ln.text).group(1) == "I"),
        None,
    )
    if start is None:
        raise ValueError("Không tìm thấy dòng in đậm 'Chương I' — không xác định được phần Quy chế")

    preamble = [ln.text for ln in lines[:start]]
    title_lines = []
    for t in preamble:
        if t.startswith("(Ban hành") or title_lines and t.startswith("("):
            break
        if t.upper().startswith("QUY CHẾ") or title_lines:
            title_lines.append(t)
    issued_by = " ".join(t for t in preamble if "Ban hành kèm theo" in t or "ngày" in t and "năm" in t)
    meta = {
        "id": doc_id,
        "title": " ".join(title_lines),
        "issued_by": issued_by.strip(),
        "preamble_lines_skipped": len(preamble),
    }

    chapters: list[Unit] = []
    chapter = article = clause = point = None
    expected_article = 1
    in_title = False  # đang gom tên chương/điều xuống dòng

    for ln in lines[start:]:
        text = ln.text
        m_ch = RE_CHAPTER.match(text) if ln.bold else None
        m_ar = RE_ARTICLE.match(text) if ln.bold else None

        if m_ch:
            n = roman_to_int(m_ch.group(1))
            if chapters and n != int(chapters[-1].number) + 1:
                warnings.append(f"Chương {m_ch.group(1)} (trang {ln.page}) không liên tiếp")
            chapter = Unit(f"{doc_id}.c{n}", "chuong", str(n), doc_id, title=m_ch.group(2).strip())
            chapter.roman = m_ch.group(1)
            chapter.lines.append(ln)
            chapters.append(chapter)
            article = clause = point = None
            in_title = "chuong"
            continue

        if m_ar:
            n = int(m_ar.group(1))
            if n != expected_article:
                warnings.append(f"Điều {n} (trang {ln.page}): mong đợi Điều {expected_article}")
            expected_article = n + 1
            article = Unit(f"{doc_id}.d{n}", "dieu", str(n), chapter.id, title=m_ar.group(2).strip())
            article.lines.append(ln)
            chapter.children.append(article)
            clause = point = None
            in_title = "dieu"
            continue

        # Tên chương / điều bị xuống dòng: các dòng in đậm liền sau tiêu đề
        if in_title and ln.bold:
            owner = chapter if in_title == "chuong" else article
            owner.title = f"{owner.title} {text}".strip()
            owner.lines.append(ln)
            continue
        in_title = False

        if article is None:
            warnings.append(f"Trang {ln.page}: nội dung nằm ngoài điều nào: {text[:60]!r}")
            chapter.lines.append(ln)
            continue

        m_cl = RE_CLAUSE.match(text)
        expected_clause = int(clause.number) + 1 if clause else 1
        if m_cl and int(m_cl.group(1)) == expected_clause:
            clause = Unit(f"{article.id}.k{expected_clause}", "khoan", str(expected_clause), article.id)
            clause.lines.append(ln)
            article.children.append(clause)
            point = None
            continue

        m_pt = RE_POINT.match(text)
        if m_pt and m_pt.group(1) == next_letter(point.number if point else None):
            parent = clause or article
            if clause is None:
                warnings.append(f"Điều {article.number}: điểm {m_pt.group(1)}) không thuộc khoản nào")
            point = Unit(f"{parent.id}.{m_pt.group(1)}", "diem", m_pt.group(1), parent.id)
            point.lines.append(ln)
            parent.children.append(point)
            continue

        (point or clause or article).lines.append(ln)

    return meta, chapters, warnings


def join_lines(lines: list[Line]) -> str:
    """Nối các dòng bị ngắt; giữ xuống dòng trước gạch đầu dòng "-", "+", "•"."""
    out = ""
    for ln in lines:
        if not out:
            out = ln.text
        elif re.match(r"^[-+•]\s", ln.text):
            out += "\n" + ln.text
        else:
            out += " " + ln.text
    return out


def full_text(unit: Unit) -> str:
    parts = [join_lines(unit.lines)] if unit.lines else []
    parts += [full_text(c) for c in unit.children]
    return "\n".join(p for p in parts if p)


def all_pages(unit: Unit) -> list[int]:
    pages = [ln.page for ln in unit.lines]
    for c in unit.children:
        pages += all_pages(c)
    return pages


def flatten(unit: Unit) -> list[dict]:
    pages = all_pages(unit)
    record = {
        "id": unit.id,
        "type": unit.type,
        "number": unit.number,
        "parent_id": unit.parent_id,
        "title": unit.title,
        "page_start": min(pages),
        "page_end": max(pages),
        "own_text": join_lines(unit.lines),  # chỉ phần của chính đơn vị, không gồm đơn vị con
        "text": full_text(unit),  # nguyên văn đầy đủ, gồm đơn vị con
    }
    if unit.type == "chuong":
        record["roman"] = unit.roman
    if unit.type == "dieu":
        record["n_khoan"] = sum(c.type == "khoan" for c in unit.children)
    records = [record]
    for c in unit.children:
        records += flatten(c)
    return records


def print_report(meta: dict, chapters: list[Unit], removed: list[str], warnings: list[str]) -> bool:
    articles = [a for ch in chapters for a in ch.children]
    numbers = [int(a.number) for a in articles]
    n_clauses = sum(c.type == "khoan" for a in articles for c in a.children)
    n_points = sum(1 for a in articles for c in a.children for p in [c, *c.children] if p.type == "diem")

    print(f"Văn bản: {meta['title']}")
    print(f"         {meta['issued_by']}")
    print(f"Bỏ qua {meta['preamble_lines_skipped']} dòng trước 'Chương I'. Đã bỏ header/footer: {', '.join(removed) or 'không có'}")
    print()
    for ch in chapters:
        print(f"Chương {ch.roman}. {ch.title}  [{ch.id}]")
        for a in ch.children:
            k = sum(c.type == "khoan" for c in a.children)
            pages = all_pages(a)
            print(f"   Điều {a.number}. {a.title}  — {k} khoản  (tr. {min(pages)}–{max(pages)})")
        print()

    ok = numbers == list(range(1, 52)) and len(chapters) == 9
    print(f"Tổng: {len(chapters)} chương, {len(articles)} điều, {n_clauses} khoản, {n_points} điểm")
    print(f"Kiểm tra 9 chương & Điều 1→51 liên tiếp, không thiếu/lặp: {'ĐẠT' if ok else 'KHÔNG ĐẠT'}")
    if warnings:
        print("Cảnh báo:")
        for w in warnings:
            print("  -", w)
    return ok


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    pdfs = sorted(RAW_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"Không có PDF trong {RAW_DIR}", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_ok = True
    for pdf in pdfs:
        doc_id = doc_id_from_filename(pdf)
        lines, removed = read_lines(pdf)
        if sum(len(ln.text) for ln in lines) < 1000:
            print(f"{pdf.name}: gần như không có lớp chữ (bản scan?) — dừng.", file=sys.stderr)
            return 1
        meta, chapters, warnings = parse(lines, doc_id)
        meta["source_file"] = pdf.relative_to(REPO_ROOT).as_posix()

        units = [u for ch in chapters for u in flatten(ch)]
        out_path = OUT_DIR / f"{doc_id}.json"
        out_path.write_text(
            json.dumps({"document": meta, "units": units}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"=== {pdf.name} → {out_path.relative_to(REPO_ROOT).as_posix()} ({len(units)} đơn vị)\n")
        all_ok &= print_report(meta, chapters, removed, warnings)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
