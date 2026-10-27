"""Extract legal citations from an answer and map them to :DonViNoiDung unit ids (schema section 3).

Recognised forms (case-insensitive, commas optional): "Điều 44 khoản 1 điểm a", "khoản 2 Điều 17",
"điểm a khoản 1 Điều 44", "khoản 2, 3 Điều 10", "khoản 1 và 2 Điều 44", "Điều 16", and raw unit codes
such as "qc3626.d44.k1". Mapping: a point goes to its parent clause; an article without clauses (or with
lead text, e.g. Điều 35, 44) keeps the article id. Each cited id then falls in one of three groups:
- cited: one of the 192 content units;
- coarse: a whole article that has clauses but no lead text (e.g. "Điều 46" alone) - it exists, but is not
  a content unit, so it counts neither for nor against citation precision/recall;
- invalid: an id that does not exist (e.g. "Điều 16 khoản 2", "Điều 99").
The same extractor is used for every system.
"""

import re

DOC = "qc3626"
_NUM_LIST = r"\d+(?:\s*(?:,|và)\s*\d+)*"
# A point letter stands alone (not the first letter of a word such as "Điều")
_LETTER = r"[a-zđ](?!\w)"
_LETTER_LIST = rf"{_LETTER}(?:\s*(?:,|và)\s*{_LETTER})*"

# Reverse order first: "[điểm x] khoản M[, N] [của] Điều K"
RE_REVERSE = re.compile(
    rf"(?:điểm\s+(?P<pts>{_LETTER_LIST})\s*,?\s*)?khoản\s+(?P<ks>{_NUM_LIST})\s*,?\s*(?:của\s+)?Điều\s+(?P<d>\d+)",
    re.IGNORECASE,
)
# Forward order: "Điều K[, khoản M[, N]][, điểm x]"
RE_FORWARD = re.compile(
    rf"Điều\s+(?P<d>\d+)(?:\s*,?\s*khoản\s+(?P<ks>{_NUM_LIST}))?(?:\s*,?\s*điểm\s+(?P<pts>{_LETTER_LIST}))?",
    re.IGNORECASE,
)
RE_CODE = re.compile(rf"\b{DOC}\.d\d+(?:\.k\d+)?(?:\.[a-zđ])?(?![\w.])")


def _numbers(text: str | None) -> list[str]:
    return re.findall(r"\d+", text) if text else []


def extract_citations(answer: str) -> list[str]:
    """Raw cited ids in order of appearance (article or clause level; points are dropped to the clause)."""
    found: list[tuple[int, str]] = []
    taken = [False] * len(answer)
    for m in RE_REVERSE.finditer(answer):
        for k in _numbers(m.group("ks")):
            found.append((m.start(), f"{DOC}.d{m.group('d')}.k{k}"))
        for i in range(m.start(), m.end()):
            taken[i] = True
    for m in RE_FORWARD.finditer(answer):
        if taken[m.start()]:
            continue
        clauses = _numbers(m.group("ks"))
        if clauses:
            found += [(m.start(), f"{DOC}.d{m.group('d')}.k{k}") for k in clauses]
        else:
            found.append((m.start(), f"{DOC}.d{m.group('d')}"))
    for m in RE_CODE.finditer(answer):
        code = m.group(0)
        parts = code.split(".")
        found.append((m.start(), ".".join(parts[:3]) if len(parts) > 3 else code))  # point -> clause
    ordered, seen = [], set()
    for _, uid in sorted(found, key=lambda pair: pair[0]):
        if uid not in seen:
            seen.add(uid)
            ordered.append(uid)
    return ordered


def map_to_units(cited: list[str], unit_ids: set[str], article_ids: set[str]) -> tuple[list[str], list[str], list[str]]:
    """Split raw cited ids into (content-unit ids, coarse whole-article ids, invalid ids)."""
    valid, coarse, invalid = [], [], []
    for uid in cited:
        if uid in unit_ids:
            valid.append(uid)
        elif uid in article_ids:
            coarse.append(uid)
        else:
            invalid.append(uid)
    return valid, coarse, invalid


def citations(answer: str, unit_ids: set[str], article_ids: set[str]) -> tuple[list[str], list[str], list[str]]:
    """(cited_ids, coarse_citations, invalid_citations) for one answer.

    article_ids: ids of all 51 articles (e.g. the dieu_id values of the content units).
    """
    return map_to_units(extract_citations(answer), unit_ids, article_ids)
