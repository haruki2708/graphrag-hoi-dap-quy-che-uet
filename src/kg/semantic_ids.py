"""Ids, slugs and spelling-variant keys for semantic-layer nodes (docs/schema_v1.md section 2, decision Q6 = A + B).

Spelling variants that NFC cannot unify ("tích luỹ"/"tích lũy", "khoá"/"khóa", "học kì"/"học kỳ", "đăng kí"/"đăng ký",
"qui"/"quy", "kĩ"/"kỹ", ...) are rewritten word by word BEFORE diacritics are dropped. The result is only for keys,
slugs and aliases (bi_danh matching); quoted regulation text is never rewritten.

Id rules (a proposal of the PR author, not written in the schema; see the PR description):
- KhaiNiem, DoiTuong, HoatDong, HeQua: sem.{Label}.{slug(ten)} -> the same name in two clauses gives the same id, so
  these nodes merge across content units.
- NhomDieuKien, DieuKien: sem.{Label}.{unit_id}.{local_id} -> never merged across units: each is a clause of one
  specific passage of the regulation.
"""

import re
import unicodedata

from src.kg.references import slugify  # T3 helper: NFD, drop combining marks, đ -> d, non-alnum -> "_"

MERGEABLE_LABELS = ("KhaiNiem", "DoiTuong", "HoatDong", "HeQua")
UNIT_SCOPED_LABELS = ("NhomDieuKien", "DieuKien")
LOCAL_ID = re.compile(r"^[A-Za-z0-9_]{1,40}$")

# Whole-word i/y variants found in QĐ 3626 (counts in the text: kì 2 / kỳ 98, kí 2 / ký 70, kĩ 2 / kỹ 19,
# qui 2 / quy 201, tỉ 1 / tỷ 1, sỹ 1 / sĩ 7) plus their obvious siblings. Canonical = the majority form in the text;
# for the tỉ/tỷ tie the y form is kept, like kỳ/ký/kỹ.
WORD_VARIANTS = {
    "kì": "kỳ", "kí": "ký", "kĩ": "kỹ", "kỉ": "kỷ", "kị": "kỵ",
    "qui": "quy", "quí": "quý", "quì": "quỳ", "quỉ": "quỷ", "quĩ": "quỹ", "quị": "quỵ",
    "lí": "lý", "tỉ": "tỷ", "mĩ": "mỹ", "sỹ": "sĩ",
}
# Old-style tone placement on a word-final "oa", "oe", "uy" (khoá -> khóa, luỹ -> lũy, thoả -> thỏa): the tone mark
# moves from the second vowel to the first. "qu" + y keeps its mark on y (quý), so a preceding q is excluded.
_TONES = "̣́̀̉̃"  # sắc, huyền, hỏi, ngã, nặng (combining, NFD)
_OLD_TONE = re.compile(rf"(?<![qQ])([oO])([aAeE])([{_TONES}])$|(?<![qQ])([uU])([yY])([{_TONES}])$")


def _move_tone(word: str) -> str:
    decomposed = unicodedata.normalize("NFD", word)

    def repl(m: re.Match) -> str:
        first, second, tone = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(4), m.group(5), m.group(6))
        return first + tone + second

    return unicodedata.normalize("NFC", _OLD_TONE.sub(repl, decomposed))


def _canonical_word(word: str) -> str:
    lower = word.lower()
    if lower in WORD_VARIANTS:
        canon = WORD_VARIANTS[lower]
        return canon[0].upper() + canon[1:] if word[0].isupper() else canon
    return _move_tone(word)


def normalize_variants(text: str) -> str:
    """NFC, then rewrite each spelling variant to its canonical form; case and spacing are kept."""
    text = unicodedata.normalize("NFC", text)
    return re.sub(r"\w+", lambda m: _canonical_word(m.group(0)), text)


def search_key(text: str) -> str:
    """Lowercase key with variants unified and whitespace collapsed (diacritics kept)."""
    return " ".join(normalize_variants(text).lower().split())


def slug(ten: str) -> str:
    """ASCII slug of a canonical name, variants unified first: 'tích luỹ' and 'tích lũy' -> 'tich_luy'.

    "+" is spelled out ("cộng") before slugifying: the generic slugify drops it, which made the plus grades
    collide with the plain ones ("điểm C+" and "điểm C" both gave "diem_c"; found while building the
    classification tables, T4 PR 1b). references.slugify, which builds the T3 VanBan ids, is left as it is.
    """
    return slugify(normalize_variants(ten).replace("+", " cộng"))


def global_id(label: str, ten: str, unit_id: str, local_id: str) -> str:
    """Global id of a semantic node (rules in the module docstring)."""
    if label in MERGEABLE_LABELS:
        s = slug(ten)
        if not s:
            raise ValueError(f"{label} {local_id!r}: name {ten!r} gives an empty slug")
        return f"sem.{label}.{s}"
    if label in UNIT_SCOPED_LABELS:
        if not LOCAL_ID.match(local_id):
            raise ValueError(f"bad local id {local_id!r}")
        return f"sem.{label}.{unit_id}.{local_id}"
    raise ValueError(f"unknown semantic label {label!r}")
