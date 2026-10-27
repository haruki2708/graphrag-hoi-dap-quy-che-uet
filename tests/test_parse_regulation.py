"""Tests for src/ingest/parse_regulation.py (S1 moves) and src/ingest/export_units.py (S2 units).

Parsing tests run on the real PDF in data/raw/core; error cases use fake units.
Run from the repo root:  python -m unittest discover -s tests -t .
"""

import json
import unittest

from eval.validate_questions import ARTICLE_UNITS
from src.ingest import export_units as ex
from src.ingest import parse_regulation as pr

Q = "qc3626."
EXPECTED_MOVES = {
    "d44.k1": ["d44.k1.c"], "d44.k2": ["d44.k2.d"], "d35": ["d35.k4"], "d35.k4": ["d35.k4.c"],
    "d36.k3": ["d36.k3.d", "d36.k3.d"], "d41.k5": ["d41.k5.b"], "d32.k2": ["d32.k2.b", "d32.k2.b"],
    "d23.k3": ["d23.k3.c"],
}


class RealPdfTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pdf = pr.find_pdfs()[0]
        cls.meta, unit_list, cls.warnings = pr.build(pdf)
        cls.units = {u["id"]: u for u in unit_list}
        cls.unit_list = unit_list
        cls.moves = pr.load_moves("qc3626")

    def text(self, uid: str) -> str:
        return self.units[Q + uid]["text"]


class ParseTests(RealPdfTestCase):
    def test_counts(self):
        counts = {t: sum(u["type"] == t for u in self.unit_list) for t in ("chuong", "dieu", "khoan", "diem")}
        self.assertEqual(counts, {"chuong": 9, "dieu": 51, "khoan": 179, "diem": 262})
        self.assertEqual(self.warnings, [])

    def test_ten_moves_into_eight_units(self):
        got = {k[len(Q):]: [d["sau"][len(Q):] for d in u["doan_khong_so"]]
               for k, u in self.units.items() if "doan_khong_so" in u}
        self.assertEqual(got, EXPECTED_MOVES)
        self.assertEqual((self.meta["n_moves"], self.meta["n_move_targets"]), (10, 8))

    def test_start_removed_from_source_points(self):
        for move in self.moves:
            self.assertNotIn(move["start"], self.units[move["unit_id"]]["own_text"], move["unit_id"])

    def test_reading_order_in_parents(self):
        def ordered(uid, *needles):
            positions = [self.text(uid).find(n) for n in needles]
            self.assertTrue(all(p >= 0 for p in positions), (uid, positions))
            self.assertEqual(positions, sorted(positions), uid)

        ordered("d44.k2", "\nd) ", "Chậm nhất là 1 tháng", "\nđ) ")
        ordered("d35.k4", "\nc) ", "Các hình thức kỷ luật", "\nd) ")
        ordered("d36.k3", "\nd) ", "Sinh viên nghỉ học tạm thời", "Các đối tượng ở điểm a")
        ordered("d32.k2", "\nb) ", "Nếu công trình nghiên cứu", "Đối với các sinh viên có công bố")
        d35, k4 = self.text("d35"), self.text("d35.k4")
        self.assertGreater(d35.find("Ngoài những nghĩa vụ chung"), d35.find(k4) + len(k4) - 1)

    def test_old_overrides_do_not_target_source_points(self):
        path = pr.RAW_DIR / "qc3626.overrides.json"
        overrides = json.loads(path.read_text(encoding="utf-8"))["overrides"]
        sources = {m["unit_id"] for m in self.moves}
        self.assertFalse(sources & {o["unit_id"] for o in overrides})


def fake_tree():
    """Article d1 > clauses k1, k2; k1 has points a, b; point b holds a trailing paragraph."""
    line = lambda text: pr.Line(text, 1, False)  # noqa: E731
    article = pr.Unit("x.d1", "dieu", "1", "x.c1", title="T", lines=[line("Điều 1. T")])
    k1 = pr.Unit("x.d1.k1", "khoan", "1", "x.d1", lines=[line("1. Khoản một")])
    a = pr.Unit("x.d1.k1.a", "diem", "a", "x.d1.k1", lines=[line("a) Điểm a")])
    b = pr.Unit("x.d1.k1.b", "diem", "b", "x.d1.k1",
                lines=[line("b) Điểm b"), line("Đoạn chung lặp"), line("Đoạn chung lặp"), line("kết thúc giữa Đoạn X")])
    k2 = pr.Unit("x.d1.k2", "khoan", "2", "x.d1", lines=[line("2. Khoản hai")])
    k1.children = [a, b]
    article.children = [k1, k2]
    chapter = pr.Unit("x.c1", "chuong", "1", "x", lines=[line("Chương I")], children=[article])
    return [chapter]


def move(start: str, move_to: str = "x.d1.k1", sau: str = "x.d1.k1.b") -> dict:
    return {"unit_id": "x.d1.k1.b", "start": start, "move_to": move_to, "sau": sau, "page": 1, "note": ""}


class MoveErrorTests(unittest.TestCase):
    def test_start_matches_no_line(self):
        with self.assertRaisesRegex(ValueError, "on no line"):
            pr.apply_moves(fake_tree(), [move("Không có")])

    def test_start_only_mid_line(self):
        with self.assertRaisesRegex(ValueError, "middle of a line"):
            pr.apply_moves(fake_tree(), [move("Đoạn X")])

    def test_start_matches_two_lines(self):
        with self.assertRaisesRegex(ValueError, "matches 2 lines"):
            pr.apply_moves(fake_tree(), [move("Đoạn chung lặp")])

    def test_sau_not_direct_child_of_target(self):
        with self.assertRaisesRegex(ValueError, "not a direct child"):
            pr.apply_moves(fake_tree(), [move("kết thúc", move_to="x.d1", sau="x.d1.k1.b")])

    def test_sau_does_not_contain_source(self):
        with self.assertRaisesRegex(ValueError, "does not contain"):
            pr.apply_moves(fake_tree(), [move("kết thúc", sau="x.d1.k1.a")])

    def test_valid_move_keeps_order(self):
        chapters = fake_tree()
        self.assertEqual(pr.apply_moves(chapters, [move("kết thúc")]), 1)
        k1 = chapters[0].children[0].children[0]
        self.assertEqual(pr.full_text(k1).splitlines()[-1], "kết thúc giữa Đoạn X")
        self.assertEqual([ln.text for ln in k1.children[1].lines][-1], "Đoạn chung lặp")


class ExportTests(RealPdfTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.content = ex.build_units(cls.unit_list)

    def test_counts_and_unique_ids(self):
        types = [u["unit_type"] for u in self.content]
        self.assertEqual((len(self.content), types.count("khoan"), types.count("dieu")), (192, 179, 13))
        self.assertEqual(len({u["id"] for u in self.content}), 192)

    def test_dieu_set_matches_validator(self):
        self.assertEqual({u["id"] for u in self.content if u["unit_type"] == "dieu"}, set(ARTICLE_UNITS))

    def test_dieu_text_has_no_clause_text(self):
        for unit in (u for u in self.content if u["unit_type"] == "dieu"):
            for khoan in (u for u in self.content if u["unit_type"] == "khoan" and u["dieu_id"] == unit["id"]):
                self.assertNotIn(khoan["text"], unit["text"], (unit["id"], khoan["id"]))

    def test_all_checks_pass(self):
        self.assertEqual(ex.check(self.content, self.unit_list), [])


if __name__ == "__main__":
    unittest.main()
