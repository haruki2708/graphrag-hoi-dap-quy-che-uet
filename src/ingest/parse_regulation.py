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

Manual fixes live in data/raw/core/<doc_id>.overrides.json:
- "moves" (schema S1 = B): unnumbered paragraphs that the line rules attach to the preceding point are
  moved, line by line, into doan_khong_so of the clause or article they belong to. Each move names the
  source point (unit_id), the paragraph's first line (start, verbatim), the target (move_to) and the
  child it follows (sau). The paragraph runs to the end of the source point, or to the next move's
  start in the same point. Anything ambiguous raises ValueError. Units that receive paragraphs get a
  "doan_khong_so" field; their text keeps the original reading order.
- "overrides": find/replace pairs applied to own_text after the moves (formulas and tables broken by
  text extraction). Each find string must occur exactly once, otherwise ValueError, so a parser change
  cannot silently patch the wrong place. Patched units get an "overrides" field.
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
# Ký tự vùng dùng riêng (Private Use Area): thường là ký hiệu font Symbol bị trích sai
RE_PRIVATE_USE = re.compile(r"[-]")

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
    patches: list[dict] = field(default_factory=list)  # các mục sửa tay từ file overrides
    # Moved unnumbered paragraphs: (id of the child they follow, their lines), in reading order
    doan_khong_so: list[tuple[str, list[Line]]] = field(default_factory=list)


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


def walk(unit: Unit):
    yield unit
    for c in unit.children:
        yield from walk(c)


def attach_overrides(chapters: list[Unit], doc_id: str) -> int:
    """Gắn các mục sửa tay vào đơn vị; trả về số mục. Dừng nếu mã đơn vị không tồn tại."""
    path = RAW_DIR / f"{doc_id}.overrides.json"
    if not path.exists():
        return 0
    items = json.loads(path.read_text(encoding="utf-8"))["overrides"]
    units = {u.id: u for ch in chapters for u in walk(ch)}
    for item in items:
        if item["unit_id"] not in units:
            raise ValueError(f"{path.name}: không có đơn vị {item['unit_id']}")
        units[item["unit_id"]].patches.append(item)
    return len(items)


def load_moves(doc_id: str) -> list[dict]:
    path = RAW_DIR / f"{doc_id}.overrides.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("moves", [])


def apply_moves(chapters: list[Unit], moves: list[dict]) -> int:
    """Move unnumbered paragraphs out of their source points into doan_khong_so (schema S1 = B).

    Works on Line objects, before any text is joined. Returns the number of paragraphs moved.
    Raises ValueError on a missing unit, a start that matches zero or several lines (or only
    mid-line), or a "sau" that is not a direct child of move_to containing the source point.
    """
    units = {u.id: u for ch in chapters for u in walk(ch)}
    # Global reading order of every line, to keep several paragraphs in one target in order
    order = {id(ln): i for i, ln in enumerate(ln for ch in chapters for u in walk(ch) for ln in u.lines)}

    by_source: dict[str, list[dict]] = {}
    for move in moves:
        for key in ("unit_id", "move_to", "sau"):
            if move[key] not in units:
                raise ValueError(f"move {move['unit_id']}: unknown unit {move[key]}")
        target, sau = units[move["move_to"]], units[move["sau"]]
        if sau.parent_id != target.id:
            raise ValueError(f"move {move['unit_id']}: sau {sau.id} is not a direct child of {target.id}")
        if not (move["unit_id"] == sau.id or move["unit_id"].startswith(sau.id + ".")):
            raise ValueError(f"move {move['unit_id']}: sau {sau.id} does not contain the source point")
        by_source.setdefault(move["unit_id"], []).append(move)

    moved = 0
    for source_id, source_moves in by_source.items():
        source = units[source_id]
        starts = []
        for move in source_moves:
            hits = [i for i, ln in enumerate(source.lines) if ln.text.startswith(move["start"])]
            if not hits:
                mid = any(move["start"] in ln.text for ln in source.lines)
                where = "only in the middle of a line" if mid else "on no line"
                raise ValueError(f"move {source_id}: start {move['start'][:40]!r} found {where}")
            if len(hits) > 1:
                raise ValueError(f"move {source_id}: start {move['start'][:40]!r} matches {len(hits)} lines")
            if hits[0] == 0:
                raise ValueError(f"move {source_id}: start is the point's own first line")
            starts.append((hits[0], move))
        starts.sort(key=lambda pair: pair[0])
        # Each paragraph ends just before the next move's start, or at the end of the source point
        bounds = [i for i, _ in starts] + [len(source.lines)]
        segments = []
        for (begin, move), end in zip(starts, bounds[1:]):
            segments.append((move, source.lines[begin:end]))
        source.lines = source.lines[: bounds[0]]
        for move, seg_lines in segments:
            target = units[move["move_to"]]
            target.doan_khong_so.append((move["sau"], seg_lines))
            target.doan_khong_so.sort(key=lambda item: order[id(item[1][0])])
            moved += 1
    return moved


def own_text(unit: Unit) -> str:
    text = join_lines(unit.lines)
    for p in unit.patches:
        count = text.count(p["find"])
        if count != 1:
            raise ValueError(f"Sửa tay {unit.id}: chuỗi find xuất hiện {count} lần (cần đúng 1): {p['find'][:60]!r}")
        text = text.replace(p["find"], p["replace"])
    return text


def full_text(unit: Unit) -> str:
    """own_text, then each child, each moved paragraph right after the child it follows."""
    parts = [own_text(unit)] if unit.lines else []
    for c in unit.children:
        parts.append(full_text(c))
        parts += [join_lines(seg) for sau, seg in unit.doan_khong_so if sau == c.id]
    return "\n".join(p for p in parts if p)


def all_pages(unit: Unit) -> list[int]:
    pages = [ln.page for ln in unit.lines]
    for _, seg in unit.doan_khong_so:
        pages += [ln.page for ln in seg]
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
        "own_text": own_text(unit),  # chỉ phần của chính đơn vị, không gồm đơn vị con
        "text": full_text(unit),  # nguyên văn đầy đủ, gồm đơn vị con
    }
    if unit.type == "chuong":
        record["roman"] = unit.roman
    if unit.type == "dieu":
        record["n_khoan"] = sum(c.type == "khoan" for c in unit.children)
    if unit.patches:
        record["overrides"] = [p["note"] for p in unit.patches]
    if unit.doan_khong_so:
        record["doan_khong_so"] = [{"sau": sau, "text": join_lines(seg)} for sau, seg in unit.doan_khong_so]
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
    print(f"Sửa tay đã áp dụng: {meta.get('n_overrides', 0)} mục")
    print(f"Chuyển đoạn: {meta.get('n_moves', 0)} đoạn vào {meta.get('n_move_targets', 0)} đơn vị")
    print(f"Kiểm tra 9 chương & Điều 1→51 liên tiếp, không thiếu/lặp: {'ĐẠT' if ok else 'KHÔNG ĐẠT'}")
    if warnings:
        print("Cảnh báo:")
        for w in warnings:
            print("  -", w)
    return ok


def build_tree(pdf: Path) -> tuple[dict, list[Unit], list[str], list[str]]:
    """Parse one PDF and apply moves then overrides; returns (meta, chapters, removed, warnings)."""
    doc_id = doc_id_from_filename(pdf)
    lines, removed = read_lines(pdf)
    if sum(len(ln.text) for ln in lines) < 1000:
        raise ValueError(f"{pdf.name}: gần như không có lớp chữ (bản scan?)")
    meta, chapters, warnings = parse(lines, doc_id)
    meta["source_file"] = pdf.relative_to(REPO_ROOT).as_posix()
    moves = load_moves(doc_id)
    meta["n_moves"] = apply_moves(chapters, moves)
    meta["n_move_targets"] = len({m["move_to"] for m in moves})
    meta["n_overrides"] = attach_overrides(chapters, doc_id)
    return meta, chapters, removed, warnings


def flatten_all(chapters: list[Unit], warnings: list[str]) -> list[dict]:
    """Unit records in reading order; warns about leftover Private Use Area characters."""
    units = [u for ch in chapters for u in flatten(ch)]
    for u in units:
        if RE_PRIVATE_USE.search(u["own_text"]):
            warnings.append(f"{u['id']} (tr. {u['page_start']}): còn ký tự lạ (font Symbol?) — cần sửa tay")
    return units


def build(pdf: Path) -> tuple[dict, list[dict], list[str]]:
    """(meta, flattened unit records, warnings) without writing any file - used by tests."""
    meta, chapters, _, warnings = build_tree(pdf)
    return meta, flatten_all(chapters, warnings), warnings


def find_pdfs() -> list[Path]:
    # Bỏ file khóa tạm "~$..." do Word/WPS tạo khi đang mở file
    return sorted(p for p in RAW_DIR.glob("*.pdf") if not p.name.startswith("~$"))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    pdfs = find_pdfs()
    if not pdfs:
        print(f"Không có PDF trong {RAW_DIR}", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_ok = True
    for pdf in pdfs:
        doc_id = doc_id_from_filename(pdf)
        try:
            meta, chapters, removed, warnings = build_tree(pdf)
            units = flatten_all(chapters, warnings)
        except ValueError as e:
            print(f"{e} — dừng.", file=sys.stderr)
            return 1
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
