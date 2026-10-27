"""Tests for src/kg/references.py and src/kg/structure.py on data/processed/qc3626.json (no Neo4j, no API).
Run from the repo root:  python -m unittest discover -s tests -t .
"""

import json
import unittest

from src.kg import references as refs
from src.kg import structure as st

P = "qc3626."

# docs/schema_v1.md Appendix A: (#, source unit, phrase, expected internal targets). "d6.k2.a" means P + "d6.k2.a".
APPENDIX_A = [
    (1, "d7.k1", "điểm a, b, c, d, đ, e, g, khoản 2, Điều 6", ["d6.k2.a", "d6.k2.b", "d6.k2.c", "d6.k2.d", "d6.k2.đ", "d6.k2.e", "d6.k2.g"]),
    (2, "d7.k2", "điểm a, khoản 2, Điều 6", ["d6.k2.a"]),
    (3, "d7.k2", "Điều 10", ["d10"]),
    (4, "d8.k2.b", "khoản 2, Điều 2", ["d2.k2"]),
    (5, "d10.k1", "khoản 22, Điều 1", []),  # trap: Luật sửa đổi, bổ sung một số điều của Luật Giáo dục đại học
    (6, "d10.k1", "khoản 2, 3, Điều này", ["d10.k2", "d10.k3"]),
    (7, "d15.k1.a", "Điều 11", ["d11"]),
    (8, "d21.k6", "khoản 1, Điều này", ["d21.k1"]),
    (9, "d23.k3.c", "khoản 1, Điều 21", ["d21.k1"]),
    (10, "d29.k3", "khoản 2 Điều này", ["d29.k2"]),
    (11, "d29.k3.b", "Điều 22", ["d22"]),
    (12, "d29.k3.b", "Điều 24", ["d24"]),
    (13, "d36.k3", "điểm a, b,c khoản 3, Điều này", ["d36.k3.a", "d36.k3.b", "d36.k3.c"]),  # source after S1: d36.k3
    (14, "d38.k8", "khoản 2, Điều này", ["d38.k2"]),
    (15, "d39.k2", "khoản 2, Điều 41", ["d41.k2"]),
    (16, "d41.k4", "khoản 2, Điều này", ["d41.k2"]),
    (17, "d44.k2.b", "khoản 1, Điều 17", ["d17.k1"]),
    (18, "d44.k2.c", "điểm d, khoản 4, Điều 35", ["d35.k4.d"]),
    (19, "d44.k2.đ", "khoản 3, Điều 36", ["d36.k3"]),
    (20, "d46.k1.đ", "khoản 3, Điều 13", ["d13.k3"]),
    (21, "d46.k2", "Điều 22", ["d22"]),
    (22, "d50.k3.đ", "khoản 2, 3, Điều 10", ["d10.k2", "d10.k3"]),
]
EXTERNAL_SOURCES = {  # S5 = B: unit that mentions each named external document
    "Luật sửa đổi, bổ sung một số điều của Luật Giáo dục đại học": ["d10.k1"],
    "Luật Giáo dục": ["d33.k1.a"],
    "Luật Giáo dục đại học": ["d33.k1.a"],
    "Luật Viên chức": ["d33.k1.a"],
    "Khung năng lực ngoại ngữ 6 bậc dùng cho Việt Nam": ["d13.k3.a", "d13.k3.b", "d29.k3.b"],
    "Quy định về chuẩn chương trình đào tạo hiện hành của Bộ Giáo dục và Đào tạo": ["d6.k1"],
}


def unit(uid, text, utype="khoan", **extra):
    return {"id": P + uid, "type": utype, "number": "1", "title": "T", "own_text": text, **extra}


class AppendixATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, cls.units = st.load_units()
        cls.found = refs.find_references(cls.units)

    def test_each_of_the_22_phrases(self):
        by_key = {(r.source, r.text): r for r in self.found}
        for number, source, phrase, targets in APPENDIX_A:
            with self.subTest(cum=number, source=source):
                ref = by_key.get((P + source, phrase))
                self.assertIsNotNone(ref, f"cụm {number} không được nhận ra")
                if number == 5:
                    self.assertEqual(ref.kind, "external")
                else:
                    self.assertEqual(ref.kind, "internal")
                    self.assertEqual(list(ref.targets), [P + t for t in targets])

    def test_no_phrase_extra_or_missing(self):
        expected = {(P + s, text) for _, s, text, _ in APPENDIX_A}
        found = {(r.source, r.text) for r in self.found}
        self.assertEqual((found - expected, expected - found), (set(), set()),
                         "extra (first) or missing (second) phrases against Appendix A")
        self.assertEqual(len(self.found), 22)

    def test_internal_edges_total_31_and_trap_gives_none(self):
        internal = [r for r in self.found if r.kind == "internal"]
        self.assertEqual(len(internal), 21)
        self.assertEqual(sum(len(r.targets) for r in internal), 31)
        self.assertEqual(sum(len(a[3]) for a in APPENDIX_A), 31)
        self.assertEqual([r.source for r in self.found if r.kind == "external"], [P + "d10.k1"])

    def test_every_target_exists(self):
        ids = {u["id"] for u in self.units}
        for r in self.found:
            if r.kind == "internal":
                self.assertTrue(set(r.targets) <= ids, r)

    def test_named_external_documents(self):
        mentions = refs.find_external_mentions(self.units)
        titles = {d.id: d.title for d in refs.EXTERNAL_DOCUMENTS}
        got = {}
        for m in mentions:
            got.setdefault(titles[m.document_id], []).append(m.source)
        self.assertEqual(got, {t: [P + s for s in srcs] for t, srcs in EXTERNAL_SOURCES.items()})
        self.assertEqual(len(mentions), 8)
        self.assertEqual(refs.unresolved_external_phrases(self.found), [])
        # the amendment law's name contains "Luật Giáo dục đại học" but gives d10.k1 only one edge
        self.assertEqual([m.document_id for m in mentions if m.source == P + "d10.k1"],
                         [refs.EXTERNAL_DOCUMENTS[0].id])


class PhraseParsingTests(unittest.TestCase):
    def targets(self, uid, text):
        known = {P + t for t in ("d5", "d5.k1", "d5.k2", "d5.k3", "d5.k3.a", "d5.k3.b", "d5.k3.c", "d5.k3.đ", "d7", "d7.k2", "d7.k2.a", "d7.k2.b")}
        units = [{"id": i, "type": "diem", "number": "a", "title": None, "own_text": ""} for i in known]
        units.append(unit(uid, text))
        return [list(r.targets) for r in refs.find_references(units)]

    def test_letter_lists_without_spaces_and_without_commas(self):
        self.assertEqual(self.targets("d5.k1", "xem điểm a, b,c khoản 3, Điều này"), [[P + "d5.k3.a", P + "d5.k3.b", P + "d5.k3.c"]])
        self.assertEqual(self.targets("d5.k1", "xem điểm a, đ khoản 3 Điều 5 của Quy chế này"), [[P + "d5.k3.a", P + "d5.k3.đ"]])

    def test_article_alone_and_this_article(self):
        self.assertEqual(self.targets("d7.k1", "theo Điều 5 của Quy chế này; khoản 2 Điều này"), [[P + "d5"], [P + "d7.k2"]])

    def test_phrase_before_another_document_is_external(self):
        refs_ = refs.find_references([unit("d7.k1", "theo khoản 2, Điều 5 Luật Viên chức")])
        self.assertEqual([(r.kind, r.targets) for r in refs_], [("external", (refs.EXTERNAL_DOCUMENTS[3].id,))])
        unknown = refs.find_references([unit("d7.k1", "theo Điều 5 Nghị định 99")])
        self.assertEqual(refs.unresolved_external_phrases(unknown), unknown)

    def test_missing_target_raises(self):
        with self.assertRaises(refs.UnresolvedReference):
            refs.find_references([unit("d7.k1", "theo khoản 9, Điều 5 của Quy chế này")])
        with self.assertRaises(refs.UnresolvedReference):
            refs.find_references([unit("d7.k1", "theo điểm a, b khoản 2, 3, Điều 5")])

    def test_article_heading_is_not_scanned(self):
        heading = {"id": P + "d5", "type": "dieu", "number": "5", "title": "Học phần", "own_text": "Điều 5. Học phần"}
        self.assertEqual(refs.find_references([heading]), [])

    def test_slug(self):
        self.assertEqual(refs.slugify("Luật Giáo dục đại học"), "luat_giao_duc_dai_hoc")
        self.assertEqual(refs.slugify("Khung năng lực ngoại ngữ 6 bậc dùng cho Việt Nam"), "khung_nang_luc_ngoai_ngu_6_bac_dung_cho_viet_nam")


class GraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = st.build_graph()
        cls.nodes = {n["id"]: n for n in cls.graph["nodes"]}

    def test_counts_and_validation(self):
        self.assertEqual(st.validate_graph(self.graph), [])
        c = self.graph["meta"]["counts"]
        self.assertEqual(c["nodes"], {"VanBan": 7, "Chuong": 9, "Dieu": 51, "Khoan": 179, "Diem": 262, "DonViNoiDung": 192})
        self.assertEqual(c["edges"], {"THUOC": 501, "DAN_CHIEU_internal": 31, "DAN_CHIEU_external": 8})

    def test_van_ban(self):
        props = self.nodes["qc3626"]["props"]
        self.assertEqual((props["so_hieu"], props["ngay_ban_hanh"], props["cap_ban_hanh"], props["ap_dung_tu_khoa"], props["trong_pham_vi"]),
                         ("3626/QĐ-ĐHQGHN", "2022-10-21", "dhqghn", 2022, True))
        external = [n for n in self.graph["nodes"] if n["labels"] == ["VanBan"] and n["id"] != "qc3626"]
        self.assertEqual(sorted(n["props"]["title"] for n in external), sorted(EXTERNAL_SOURCES))
        self.assertTrue(all(n["props"]["trong_pham_vi"] is False and n["id"].startswith("ext.") for n in external))

    def test_content_unit_label_matches_the_units_file(self):
        labelled = {n["id"] for n in self.graph["nodes"] if "DonViNoiDung" in n["labels"]}
        self.assertEqual(labelled, set(st.load_content_units()))
        self.assertFalse(any("Diem" in n["labels"] and "DonViNoiDung" in n["labels"] for n in self.graph["nodes"]))  # S3 = A

    def test_doan_khong_so_stays_on_the_holding_node(self):
        self.assertEqual(self.nodes[P + "d36.k3"]["props"]["doan_khong_so"][0]["sau"], P + "d36.k3.d")
        self.assertIn("doan_khong_so", self.nodes[P + "d35"]["props"])
        self.assertEqual(sum("doan_khong_so" in n["props"] for n in self.graph["nodes"]), 8)

    def test_thuoc_follows_the_hierarchy(self):
        parent = {e["from"]: e["to"] for e in self.graph["edges"] if e["type"] == "THUOC"}
        self.assertEqual(parent[P + "c1"], "qc3626")
        self.assertEqual(parent[P + "d44.k2.đ"], P + "d44.k2")
        self.assertEqual(parent[P + "d44.k2"], P + "d44")

    def test_who_references_examples(self):
        for target, want in st.WHO_REFERENCES_EXAMPLES.items():
            with self.subTest(target=target):
                self.assertEqual(st.who_references(self.graph, target), want)

    def test_committed_graph_file_is_current(self):
        committed = json.loads(st.GRAPH_PATH.read_text(encoding="utf-8"))
        self.assertEqual(committed, self.graph, "run: python src/kg/structure.py")


if __name__ == "__main__":
    unittest.main()
