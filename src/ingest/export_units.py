"""Export the :DonViNoiDung content units (schema S2 = B) from data/processed/qc3626.json.

Run from the repo root:  python src/ingest/export_units.py

Writes data/processed/qc3626_content_units.jsonl, one unit per line in reading order:
- every Khoan (179): text = the clause's full text (points and doan_khong_so included);
- every Dieu with lead text or article-level doan_khong_so (13): text = lead text (own_text without the
  "Điều N. <title>" heading) + any non-clause children + article-level doan_khong_so; clauses excluded.
Each character of the regulation's body falls in exactly one unit (checked). The heading is a separate
field and is not prepended to text.
"""

import json
import re
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from eval.validate_questions import ARTICLE_UNITS  # noqa: E402

PROCESSED_JSON = REPO_ROOT / "data" / "processed" / "qc3626.json"
OUT_PATH = REPO_ROOT / "data" / "processed" / "qc3626_content_units.jsonl"
FIELDS = ("id", "unit_type", "dieu_id", "dieu_number", "dieu_title", "chuong_id", "page_start", "page_end",
          "heading", "text", "n_chars")
EXPECTED_KHOAN = 179


def article_heading(dieu: dict) -> str:
    return f"Điều {dieu['number']}. {dieu['title']}"


def strip_heading(dieu: dict, text: str) -> str:
    """Remove the exact "Điều N. <title>" prefix; anything else is an error, never a guess."""
    heading = article_heading(dieu)
    if not text.startswith(heading):
        raise ValueError(f"{dieu['id']}: text does not start with {heading!r}")
    return text[len(heading):].strip()


def build_units(units: list[dict]) -> list[dict]:
    children: dict[str, list[dict]] = {}
    for u in units:
        children.setdefault(u["parent_id"], []).append(u)

    out = []
    for dieu in (u for u in units if u["type"] == "dieu"):
        base = {"dieu_id": dieu["id"], "dieu_number": int(dieu["number"]), "dieu_title": dieu["title"],
                "chuong_id": dieu["parent_id"]}
        lead = strip_heading(dieu, dieu["own_text"])
        extra = [c["text"] for c in children.get(dieu["id"], []) if c["type"] != "khoan"]
        moved = [d["text"] for d in dieu.get("doan_khong_so", [])]
        dieu_text = "\n".join(p for p in [lead, *extra, *moved] if p)
        if dieu_text:
            # Lead text ends where the first clause starts; with no clause, or with an article-level
            # moved paragraph (which comes after the clauses), the unit runs to the article's last page.
            # Line-level pages are not in qc3626.json, so the end page can overstate by one page.
            first_khoan = next((c for c in children.get(dieu["id"], []) if c["type"] == "khoan"), None)
            page_end = dieu["page_end"] if (first_khoan is None or moved) else first_khoan["page_start"]
            out.append({**base, "id": dieu["id"], "unit_type": "dieu",
                        "page_start": dieu["page_start"], "page_end": page_end,
                        "heading": article_heading(dieu), "text": dieu_text})
        for khoan in (c for c in children.get(dieu["id"], []) if c["type"] == "khoan"):
            out.append({**base, "id": khoan["id"], "unit_type": "khoan",
                        "page_start": khoan["page_start"], "page_end": khoan["page_end"],
                        "heading": f"{article_heading(dieu)}, khoản {khoan['number']}", "text": khoan["text"]})
    for record in out:
        record["n_chars"] = len(record["text"])
    return [{k: r[k] for k in FIELDS} for r in out]


def non_ws_len(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def check(content: list[dict], units: list[dict]) -> list[str]:
    """Return a list of problems; empty when all checks pass."""
    problems = []
    khoan = [u for u in content if u["unit_type"] == "khoan"]
    dieu = [u for u in content if u["unit_type"] == "dieu"]
    if len(khoan) != EXPECTED_KHOAN:
        problems.append(f"{len(khoan)} khoan units, expected {EXPECTED_KHOAN}")
    if {u["id"] for u in dieu} != set(ARTICLE_UNITS):
        problems.append(f"dieu units differ from ARTICLE_UNITS: "
                        f"extra {sorted({u['id'] for u in dieu} - ARTICLE_UNITS)}, "
                        f"missing {sorted(ARTICLE_UNITS - {u['id'] for u in dieu})}")
    ids = [u["id"] for u in content]
    if len(ids) != len(set(ids)):
        problems.append("duplicate ids")
    problems += [f"{u['id']}: empty text" for u in content if not u["text"].strip()]
    # Coverage: the units hold every character of the 51 articles' bodies, exactly once
    body = sum(non_ws_len(strip_heading(u, u["text"])) for u in units if u["type"] == "dieu")
    covered = sum(non_ws_len(u["text"]) for u in content)
    if body != covered:
        problems.append(f"coverage: {covered} non-whitespace chars in units vs {body} in article bodies")
    return problems


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    units = json.loads(PROCESSED_JSON.read_text(encoding="utf-8"))["units"]
    try:
        content = build_units(units)
    except ValueError as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 1
    problems = check(content, units)
    if problems:
        print("LỖI:\n" + "\n".join(f"  - {p}" for p in problems), file=sys.stderr)
        return 1

    with OUT_PATH.open("w", encoding="utf-8", newline="\n") as f:
        for record in content:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    n_khoan = sum(u["unit_type"] == "khoan" for u in content)
    lengths = sorted((u["n_chars"], u["id"]) for u in content)
    body = sum(non_ws_len(strip_heading(u, u["text"])) for u in units if u["type"] == "dieu")
    print(f"{OUT_PATH.relative_to(REPO_ROOT).as_posix()}: {len(content)} đơn vị "
          f"({n_khoan} khoan + {len(content) - n_khoan} dieu), id không trùng, không text rỗng")
    print(f"Độ phủ: {sum(non_ws_len(u['text']) for u in content)} ký tự (không tính khoảng trắng) "
          f"= thân 51 điều {body}")
    print(f"n_chars: min {lengths[0][0]} ({lengths[0][1]}), trung vị {statistics.median(n for n, _ in lengths)}, "
          f"max {lengths[-1][0]} ({lengths[-1][1]})")
    print("Đơn vị Dieu:")
    for u in content:
        if u["unit_type"] == "dieu":
            print(f"  {u['id']}: {u['n_chars']} ký tự — {u['heading']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
