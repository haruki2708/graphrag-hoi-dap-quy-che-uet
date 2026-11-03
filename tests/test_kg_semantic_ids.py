"""Tests for src/kg/semantic_ids.py: spelling-variant keys, slugs, global ids (schema Q6). No API, no Neo4j.
Run from the repo root:  python -m unittest discover -s tests -t .
"""

import unittest

from src.kg import references as refs
from src.kg import semantic_ids as sid

# (variant in the text, canonical form) - schema Q6 plus the siblings found in QĐ 3626
VARIANT_PAIRS = [
    ("tích luỹ", "tích lũy"),
    ("khoá học", "khóa học"),
    ("học kì", "học kỳ"),
    ("đăng kí", "đăng ký"),
    ("qui định", "quy định"),
    ("kĩ sư", "kỹ sư"),
    ("tỉ lệ", "tỷ lệ"),
    ("tu sỹ", "tu sĩ"),
    ("thoả mãn", "thỏa mãn"),
]


class VariantTests(unittest.TestCase):
    def test_variants_get_the_canonical_form(self):
        for variant, canonical in VARIANT_PAIRS:
            with self.subTest(variant=variant):
                self.assertEqual(sid.normalize_variants(variant), canonical)
                self.assertEqual(sid.normalize_variants(canonical), canonical)
                self.assertEqual(sid.slug(variant), sid.slug(canonical))
                self.assertEqual(sid.search_key(variant), sid.search_key(canonical))

    def test_i_y_variants_would_differ_without_the_word_step(self):
        # dropping diacritics alone does not unify kì/kỳ or qui/quy, which is why the word step runs first
        self.assertNotEqual(refs.slugify("học kì"), refs.slugify("học kỳ"))
        self.assertNotEqual(refs.slugify("qui định"), refs.slugify("quy định"))
        self.assertEqual(sid.slug("học kì"), "hoc_ky")
        self.assertEqual(sid.slug("qui định"), "quy_dinh")

    def test_case_is_kept_and_unrelated_words_are_untouched(self):
        self.assertEqual(sid.normalize_variants("Khoá học"), "Khóa học")
        self.assertEqual(sid.normalize_variants("Học Kì"), "Học Kỳ")
        for word in ("quý", "khoa", "thuế", "thủy", "khuỷu", "quyết"):
            self.assertEqual(sid.normalize_variants(word), word)

    def test_whole_words_only(self):
        # "kì" inside a longer word is not rewritten
        self.assertEqual(sid.normalize_variants("kìm"), "kìm")
        self.assertEqual(sid.search_key("  Điểm   trung bình chung tích luỹ "), "điểm trung bình chung tích lũy")


class SlugAndIdTests(unittest.TestCase):
    def test_slug_reuses_the_t3_slugify(self):
        self.assertEqual(sid.slug("Luật Giáo dục đại học"), refs.slugify("Luật Giáo dục đại học"))
        self.assertEqual(sid.slug("Điểm trung bình chung tích luỹ"), "diem_trung_binh_chung_tich_luy")

    def test_mergeable_labels_ignore_the_unit(self):
        a = sid.global_id("KhaiNiem", "điểm trung bình chung tích luỹ", "qc3626.d44.k1", "k1")
        b = sid.global_id("KhaiNiem", "điểm trung bình chung tích lũy", "qc3626.d43.k2", "n7")
        self.assertEqual(a, b)
        self.assertEqual(a, "sem.KhaiNiem.diem_trung_binh_chung_tich_luy")
        self.assertEqual(sid.global_id("HeQua", "bị buộc thôi học", "u", "q1"), "sem.HeQua.bi_buoc_thoi_hoc")

    def test_condition_nodes_are_scoped_to_their_unit(self):
        a = sid.global_id("DieuKien", "dưới 1,40", "qc3626.d44.k1", "c1")
        b = sid.global_id("DieuKien", "dưới 1,40", "qc3626.d46.k1", "c1")
        self.assertEqual(a, "sem.DieuKien.qc3626.d44.k1.c1")
        self.assertNotEqual(a, b)
        self.assertEqual(sid.global_id("NhomDieuKien", "x", "qc3626.d44", "g1"), "sem.NhomDieuKien.qc3626.d44.g1")

    def test_bad_inputs_raise(self):
        with self.assertRaises(ValueError):
            sid.global_id("Nguong", "x", "u", "n1")
        with self.assertRaises(ValueError):
            sid.global_id("DieuKien", "x", "u", "bad id")
        with self.assertRaises(ValueError):
            sid.global_id("KhaiNiem", "!!!", "u", "k1")


if __name__ == "__main__":
    unittest.main()
