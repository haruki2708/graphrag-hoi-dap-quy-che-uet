"""DAN_CHIEU edges: cross-references inside the regulation and mentions of external documents.

Rules (docs/schema_v1.md section 1.3, decisions S4 = A, S5 = B):
- A reference phrase is `[điểm a, b, ...] [khoản 2, 3] Điều N` or `... Điều này`; commas and spaces between the
  parts are optional ("điểm a, b,c khoản 3, Điều này", "khoản 2 Điều này").
- "Điều N" alone points to the Dieu node (S4 = A); "Điều này" means the article that holds the source unit.
- A phrase followed by another document's name ("khoản 22, Điều 1 Luật sửa đổi, ...") is not an internal edge.
- Every internal target must exist; a missing target raises UnresolvedReference, never a silent skip.
- Named external documents (S5 = B) come from the fixed EXTERNAL_DOCUMENTS list. Unnamed phrases such as
  "quy định hiện hành" make no node. Other named documents found in the text are only reported
  (unregistered_named_documents), never added.
Scanned text: own_text of every unit (the "Điều N. <title>" heading removed) and the text of each doan_khong_so
paragraph; a paragraph's source is the unit that holds it, e.g. d36.k3 for the paragraph moved out of d36.k3.d.
"""

import re
import unicodedata
from dataclasses import dataclass

DOC_ID = "qc3626"

_DIEM_LIST = r"[a-zđ](?!\w)(?:\s*,\s*[a-zđ](?!\w))*"
_KHOAN_LIST = r"\d+(?:\s*,\s*\d+)*"
PHRASE = re.compile(
    rf"(?:[Đđ]iểm\s+(?P<diem>{_DIEM_LIST})\s*,?\s*)?"
    rf"(?:[Kk]hoản\s+(?P<khoan>{_KHOAN_LIST})\s*,?\s*)?"
    r"Điều\s+(?P<dieu>\d+|này)(?!\w)"
)
# What follows a phrase when it points into another document ("của Quy chế này" is internal)
_EXTERNAL_TAIL = re.compile(
    r"\s*(?:của\s+)?(?:Luật|Bộ\s+luật|Nghị\s+định|Nghị\s+quyết|Thông\s+tư|Quyết\s+định|Pháp\s+lệnh|Hiến\s+pháp|"
    r"Chỉ\s+thị|Điều\s+lệ|Quy\s+định|Quy\s+chế(?!\s+này))\b"
)
_ARTICLE_OF = re.compile(rf"^{DOC_ID}\.d(\d+)(?:\.|$)")


class UnresolvedReference(ValueError):
    """A reference phrase that cannot be resolved to existing units."""


@dataclass(frozen=True)
class ExternalDocument:
    title: str  # as the regulation calls it

    @property
    def id(self) -> str:
        return "ext." + slugify(self.title)

    @property
    def pattern(self) -> re.Pattern:
        return re.compile(r"\s+".join(re.escape(word) for word in self.title.split()))


# The six named documents of schema S5. Do not add or drop one without the project owner's decision.
EXTERNAL_DOCUMENTS = (
    ExternalDocument("Luật sửa đổi, bổ sung một số điều của Luật Giáo dục đại học"),  # d10.k1
    ExternalDocument("Luật Giáo dục"),                                                  # d33.k1.a
    ExternalDocument("Luật Giáo dục đại học"),                                          # d33.k1.a
    ExternalDocument("Luật Viên chức"),                                                 # d33.k1.a
    ExternalDocument("Khung năng lực ngoại ngữ 6 bậc dùng cho Việt Nam"),               # d13.k3, d29.k3.b
    ExternalDocument("Quy định về chuẩn chương trình đào tạo hiện hành của Bộ Giáo dục và Đào tạo"),  # d6.k1
)


def slugify(text: str) -> str:
    """ASCII slug with underscores: 'Luật Giáo dục đại học' -> 'luat_giao_duc_dai_hoc'."""
    text = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


@dataclass(frozen=True)
class Reference:
    source: str               # unit that holds the text
    text: str                 # the matched phrase, whitespace collapsed
    kind: str                 # "internal" or "external"
    targets: tuple[str, ...]  # internal: unit ids; external: the registered document id, or () if not registered


@dataclass(frozen=True)
class ExternalMention:
    source: str
    document_id: str
    text: str


def segments(unit: dict) -> list[str]:
    """Texts to scan for one unit: own_text (article heading removed), then each doan_khong_so paragraph."""
    own = unit["own_text"]
    if unit["type"] == "dieu":
        heading = f"Điều {unit['number']}. {unit['title']}"
        if not own.startswith(heading):
            raise ValueError(f"{unit['id']}: own_text does not start with {heading!r}")
        own = own[len(heading):]
    return [own, *(p["text"] for p in unit.get("doan_khong_so", []))]


def _collapse(text: str) -> str:
    return " ".join(text.split())


def resolve_targets(match: re.Match, source_id: str, known_ids: set[str]) -> tuple[str, ...]:
    if match["dieu"] == "này":
        article = _ARTICLE_OF.match(source_id)
        if not article:
            raise UnresolvedReference(f"{source_id}: 'Điều này' in a unit that is not inside an article")
        number = article.group(1)
    else:
        number = match["dieu"]
    base = f"{DOC_ID}.d{number}"
    khoan = re.findall(r"\d+", match["khoan"] or "")
    diem = re.findall(r"[a-zđ]", match["diem"] or "")
    if diem:
        if len(khoan) != 1:
            raise UnresolvedReference(f"{source_id}: {_collapse(match[0])!r}: điểm needs exactly one khoản")
        targets = tuple(f"{base}.k{khoan[0]}.{letter}" for letter in diem)
    elif khoan:
        targets = tuple(f"{base}.k{k}" for k in khoan)
    else:
        targets = (base,)
    missing = [t for t in targets if t not in known_ids]
    if missing:
        raise UnresolvedReference(f"{source_id}: {_collapse(match[0])!r} points to missing unit(s) {missing}")
    return targets


def _external_tail_document(text: str, end: int) -> str | None:
    """Id of the registered document that follows a phrase ending at `end`, if any."""
    tail = re.sub(r"^\s*(?:của\s+)?", "", text[end:])
    return next((d.id for d in sorted(EXTERNAL_DOCUMENTS, key=lambda d: len(d.title), reverse=True)
                 if d.pattern.match(tail)), None)


def find_references(units: list[dict]) -> list[Reference]:
    """All reference phrases in reading order. When another document's name follows a phrase it is external:
    no unit targets, and the registered document (if any) is its only target."""
    known = {u["id"] for u in units}
    found = []
    for unit in units:
        for text in segments(unit):
            for match in PHRASE.finditer(text):
                phrase = _collapse(match[0])
                if _EXTERNAL_TAIL.match(text, match.end()):
                    doc = _external_tail_document(text, match.end())
                    found.append(Reference(unit["id"], phrase, "external", (doc,) if doc else ()))
                else:
                    found.append(Reference(unit["id"], phrase, "internal", resolve_targets(match, unit["id"], known)))
    return found


def _free_matches(text: str, patterns: list[tuple[str, re.Pattern]]) -> list[tuple[str, str, tuple[int, int]]]:
    """Matches of every pattern, longest name first; a span already taken by a longer name is not reused."""
    taken, out = [], []
    for key, pattern in patterns:
        for m in pattern.finditer(text):
            if any(m.start() < end and start < m.end() for start, end in taken):
                continue
            taken.append((m.start(), m.end()))
            out.append((key, _collapse(m[0]), (m.start(), m.end())))
    return out


def find_external_mentions(units: list[dict]) -> list[ExternalMention]:
    """One mention per (unit, named external document). 'Luật Giáo dục đại học' inside the name of the
    amendment law does not count as a separate mention."""
    ordered = sorted(EXTERNAL_DOCUMENTS, key=lambda d: len(d.title), reverse=True)
    patterns = [(d.id, d.pattern) for d in ordered]
    mentions = []
    for unit in units:
        seen = set()
        for text in segments(unit):
            for doc_id, phrase, _ in sorted(_free_matches(text, patterns), key=lambda x: x[2]):
                if doc_id not in seen:
                    seen.add(doc_id)
                    mentions.append(ExternalMention(unit["id"], doc_id, phrase))
    return mentions


def unresolved_external_phrases(references: list[Reference]) -> list[Reference]:
    """External-looking phrases whose tail is not a registered document (reported, never added)."""
    return [r for r in references if r.kind == "external" and not r.targets]


_NAMED_CANDIDATE = re.compile(
    r"(?:(?:Bộ\s+luật|Luật|Nghị\s+định|Nghị\s+quyết|Thông\s+tư|Pháp\s+lệnh|Hiến\s+pháp)\s+[A-ZÀ-Ỹ]"
    r"|Quy\s+chế\s+đào\s+tạo|Quy\s+định\s+về)[^;:\n]{0,70}"
)


def unregistered_named_documents(units: list[dict]) -> list[tuple[str, str]]:
    """(unit id, text) of document-like names that are not in EXTERNAL_DOCUMENTS. For the report only."""
    ordered = sorted(EXTERNAL_DOCUMENTS, key=lambda d: len(d.title), reverse=True)
    patterns = [(d.id, d.pattern) for d in ordered]
    out = []
    for unit in units:
        for text in segments(unit):
            taken = [span for _, _, span in _free_matches(text, patterns)]
            for m in _NAMED_CANDIDATE.finditer(text):
                if not any(m.start() < end and start < m.end() for start, end in taken):
                    out.append((unit["id"], _collapse(m[0])))
    return out
