"""Tests for src/kg/tables.py: the six classification tables and the thu_tu table (T4 PR 1b, schema Q10 = B).

The typed data (data/manual/bang_phan_loai_v1.json) is checked against qc3626.json by re-deriving every table
from the regulation text independently (markdown table of d17.k2, regexes on d41.k2 / d43.k1, exact point text of
d45.k2 / d47.k5 / d32.k2), so a missing, extra or mistyped row fails. No API, no Neo4j. Tests that compare with the
LLM extraction records need data/processed/semantic/extract/ (not committed) and are skipped when it is absent.
Run from the repo root:  python -m unittest discover -s tests -t .
"""

import copy
import csv
import glob
import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from src.kg import extract as ex
from src.kg import semantic_ids as sid
from src.kg import semantic_schema as ss
from src.kg import tables as tb

P = "qc3626."
REPO = Path(__file__).resolve().parents[1]
BY_ID, CHILDREN = ex.load_regulation()
DATA = tb.load_tables()
TABLE = {t["unit"]: t for t in DATA["bang"]}
LLM_RECORDS = sorted(glob.glob(str(REPO / "data" / "processed" / "semantic" / "extract" / "*.json")))

# Table 2.1a of docs/schema_v1.md, typed here independently of the data file
THU_TU_2_1A = {
    "nam_dao_tao": {"năm thứ nhất": 1, "năm thứ hai": 2, "năm thứ ba": 3, "năm thứ tư": 4, "năm thứ năm": 5,
                    "năm thứ sáu": 6},
    "diem_chu": {"điểm F": 1, "điểm D": 2, "điểm D+": 3, "điểm C": 4, "điểm C+": 5, "điểm B": 6, "điểm B+": 7,
                 "điểm A": 8, "điểm A+": 9},
    "hoc_luc": {"học lực yếu": 1, "học lực trung bình": 2, "học lực khá": 3, "học lực giỏi": 4,
                "học lực xuất sắc": 5},
    "hang_tot_nghiep": {"hạng tốt nghiệp trung bình": 1, "hạng tốt nghiệp khá": 2, "hạng tốt nghiệp giỏi": 3,
                        "hạng tốt nghiệp xuất sắc": 4},
}
NUM = r"(\d+(?:,\d+)?)"


def build():
    return tb.build_all(DATA, "sha-for-tests", (BY_ID, CHILDREN))


def rows_of(unit: str, nguon: str | None = None) -> list[dict]:
    return [r for r in TABLE[P + unit]["dong"] if nguon is None or r["nguon"] == P + nguon]


class RowCountTests(unittest.TestCase):
    def test_rows_per_table_match_schema_q10(self):
        self.assertEqual({u: len(t["dong"]) for u, t in TABLE.items()}, tb.EXPECTED_ROWS)
        self.assertEqual(list(tb.EXPECTED_ROWS.values()), [14, 7, 10, 9, 5, 4])
        self.assertEqual(sum(len(t["dong"]) for t in DATA["bang"]), 49)


class AgainstTheRegulationTextTests(unittest.TestCase):
    """Every table re-read from qc3626.json and compared with the typed rows."""

    def test_every_typed_string_is_verbatim_in_its_unit(self):
        for table in DATA["bang"]:
            for row in table["dong"]:
                text = BY_ID[row["nguon"]]["text"]
                for field in ("nguyen_van", "dieu_kien", "pham_vi"):
                    if field in row:
                        self.assertIn(row[field], text, f"{row['nguon']} {field}")
                self.assertEqual(re.findall(NUM, row["nguyen_van"]), row["so"], row["nguyen_van"])
                for s in row["so"]:
                    self.assertRegex(s, r"^\d+(,\d+)?$")

    def test_d17_k2_equals_the_markdown_table(self):
        lines = [ln for ln in BY_ID[P + "d17.k2"]["own_text"].splitlines() if ln.startswith("|")]
        cells = [[c.strip() for c in ln.strip("|").split("|")] for ln in lines]
        header, body = cells[0], [r for r in cells[2:]]  # cells[1] is the --- separator
        self.assertEqual(len(body), 6)
        from_text = {(prog.lower(), r[0].lower(), r[i]) for r in body for i, prog in enumerate(header) if i > 0 and r[i] != "-"}
        typed = {(r["chuong_trinh"].lower(), r["muc"], r["dieu_kien"]) for r in rows_of("d17.k2")}
        self.assertEqual(typed, from_text)
        self.assertEqual(len(from_text), 14)  # 18 cells minus 4 blank "-" cells

    def test_d41_k2_points(self):
        a = BY_ID[P + "d41.k2.a"]["own_text"]
        pairs = re.findall(rf"{NUM} – {NUM} tương ứng với (\S+)", a)
        self.assertEqual(len(pairs), 8)
        typed = [(r["so"], r["muc"]) for r in rows_of("d41.k2", "d41.k2.a")]
        self.assertEqual(typed, [([lo, hi], f"điểm {grade}") for lo, hi, grade in pairs])
        (p,) = rows_of("d41.k2", "d41.k2.b")
        self.assertEqual((p["so"], p["muc"]), ([re.search(rf"P: từ {NUM} trở lên", BY_ID[P + "d41.k2.b"]["own_text"]).group(1)], "điểm P"))
        (f,) = rows_of("d41.k2", "d41.k2.c")
        self.assertEqual(f["so"], [re.search(rf"Dưới {NUM} tương ứng với F", BY_ID[P + "d41.k2.c"]["own_text"]).group(1)])
        self.assertEqual(f["muc"], "điểm F")

    def test_d43_k1_pairs(self):
        pairs = re.findall(rf"^- (\S+) tương ứng với {NUM}$", BY_ID[P + "d43.k1"]["own_text"], re.M)
        self.assertEqual(len(pairs), 9)
        self.assertEqual([(r["muc"], r["so"]) for r in rows_of("d43.k1")],
                         [(f"điểm {grade}", [value]) for grade, value in pairs])

    def test_d45_and_d47_rows_are_the_points_exactly(self):
        for unit in ("d45.k2", "d47.k5"):
            points = [c for c in CHILDREN[P + unit] if c["type"] == "diem"]
            rows = rows_of(unit)
            self.assertEqual(len(points), len(rows), unit)
            for point, row in zip(points, rows):
                self.assertEqual(point["id"], row["nguon"])
                body = point["own_text"].split(") ", 1)[1].rstrip(".")
                self.assertEqual(body, row["nguyen_van"], point["id"])

    def test_d32_k2_rows_are_the_prize_lines_exactly(self):
        for letter in "ab":
            rows = rows_of("d32.k2", f"d32.k2.{letter}")
            own = BY_ID[P + f"d32.k2.{letter}"]["own_text"].split(") ", 1)[1]
            self.assertEqual(own, f"{rows[0]['dieu_kien']}: " + " ".join(r["nguyen_van"] for r in rows))
            self.assertEqual(len({r["dieu_kien"] for r in rows}), 1)
        self.assertEqual((len(rows_of("d32.k2", "d32.k2.a")), len(rows_of("d32.k2", "d32.k2.b"))), (4, 3))

    def test_verify_data_passes_on_the_real_file(self):
        self.assertEqual(tb.verify_data(DATA, BY_ID), [])


class RangeRulesTests(unittest.TestCase):
    def mutated(self, edit):
        data = copy.deepcopy(DATA)
        edit(data)
        return tb.verify_data(data, BY_ID)

    def test_a_gap_between_rows_is_found(self):
        def edit(d):  # chuẩn, năm thứ ba: 71 -> 72 leaves 71 in no row
            next(r for r in d["bang"][0]["dong"] if r["nguyen_van"] == "Từ 71 – 105 tín chỉ")["so"][0] = "72"
        self.assertTrue(any("not adjacent" in e or "differs from the numbers" in e for e in self.mutated(edit)))

    def test_an_overlap_is_found(self):
        def edit(d):  # also edit the text so only the range rule can object
            row = next(r for r in d["bang"][0]["dong"] if r["nguyen_van"] == "Từ 71 – 105 tín chỉ")
            row["so"][0] = "70"
            row["nguyen_van"] = row["nguyen_van"].replace("71", "70")
        errors = self.mutated(edit)
        self.assertTrue(any("not adjacent" in e for e in errors), errors)

    def test_inclusive_and_exclusive_boundaries(self):
        a = {"muc": "a", "dieu_kien": "x", "dang": "duoi", "so": ["35"]}      # (-inf, 35)
        b = {"muc": "b", "dieu_kien": "y", "dang": "tu_den", "so": ["35", "70"]}  # [35, 70]
        c = {"muc": "c", "dieu_kien": "z", "dang": "tu_den", "so": ["71", "90"]}  # [71, 90]
        self.assertEqual(tb.chain_errors("t", [a, b, c], 1), [])
        d = {"muc": "d", "dieu_kien": "w", "dang": "tu_den", "so": ["72", "90"]}
        self.assertEqual(len(tb.chain_errors("t", [a, b, d], 1)), 1)
        self.assertEqual(len(tb.chain_errors("t", [a, {**b, "so": ["36", "70"]}], 1)), 1)   # 35 belongs to no row
        self.assertEqual(tb.chain_errors("t", [{**b, "so": ["2,00", "2,49"]}, {**c, "so": ["2,50", "3,19"]}], 0.01), [])

    def test_number_and_text_mistakes_are_found(self):
        def wrong_number(d):
            next(r for r in d["bang"][0]["dong"] if r["so"] == ["40", "80"])["so"] = ["40", "81"]
        self.assertTrue(any("differs from the numbers" in e for e in self.mutated(wrong_number)))

        def wrong_text(d):
            d["bang"][4]["dong"][0]["nguyen_van"] = d["bang"][4]["dong"][0]["nguyen_van"].replace("3,60", "3,61")
        self.assertTrue(any("not verbatim" in e for e in self.mutated(wrong_text)))

        def drop_row(d):
            d["bang"][2]["dong"].pop()
        self.assertTrue(any("schema Q10 says 10" in e for e in self.mutated(drop_row)))

    def test_thu_tu_mistakes_are_found(self):
        def swap(d):
            m = d["thang"]["hoc_luc"]["muc"]
            m[0]["thu_tu"], m[1]["thu_tu"] = 2, 1
        errors = self.mutated(swap)
        self.assertTrue(any("thu_tu says" in e for e in errors), errors)

        def duplicate(d):
            d["thang"]["nam_dao_tao"]["muc"][1]["thu_tu"] = 1
        self.assertTrue(any("is not 1.." in e for e in self.mutated(duplicate)))

    def test_same_name_in_two_scales_is_found(self):
        def edit(d):
            d["thang"]["hang_tot_nghiep"]["muc"][0]["ten"] = "học lực yếu"
        self.assertTrue(any("more than one scale" in e or "same id" in e for e in self.mutated(edit)))


class ThuTuTests(unittest.TestCase):
    def test_equals_table_2_1a(self):
        self.assertEqual({k: {m["ten"]: m["thu_tu"] for m in s["muc"]} for k, s in DATA["thang"].items()},
                         THU_TU_2_1A)
        self.assertEqual({k: s["dai_luong"] for k, s in DATA["thang"].items()},
                         {"nam_dao_tao": "năm đào tạo", "diem_chu": "điểm chữ", "hoc_luc": "học lực",
                          "hang_tot_nghiep": "hạng tốt nghiệp"})

    def test_plus_grades_do_not_share_an_id_with_plain_grades(self):
        ids = [sid.global_id("KhaiNiem", m["ten"], "", "") for m in DATA["thang"]["diem_chu"]["muc"]]
        self.assertEqual(len(set(ids)), 9)

    def test_scales_that_share_words_do_not_share_ids(self):
        hoc_luc = {sid.global_id("KhaiNiem", m["ten"], "", "") for m in DATA["thang"]["hoc_luc"]["muc"]}
        hang = {sid.global_id("KhaiNiem", m["ten"], "", "") for m in DATA["thang"]["hang_tot_nghiep"]["muc"]}
        self.assertFalse(hoc_luc & hang)


class FragmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records, cls.refs, cls.problems = build()
        cls.by_unit = {r["unit"]: r for r in cls.records}

    def nodes(self, unit: str, label: str | None = None) -> list[dict]:
        return [n for n in self.by_unit[P + unit]["fragment"]["nodes"] if label is None or n["labels"] == [label]]

    def test_all_records_pass_the_validator_without_warnings(self):
        self.assertEqual(self.problems, [])
        self.assertEqual(len(self.records), 6)
        for record in self.records:
            self.assertEqual((record["status"], record["errors"], record["warnings"]), ("ok", [], []), record["unit"])
            self.assertEqual(record["fragment"]["meta"]["nguon_loai"], "bang")
            self.assertEqual(record["fragment"]["meta"]["merge_conflicts"], [])
            self.assertTrue(all(n["props"]["nguon_loai"] == "bang" for n in record["fragment"]["nodes"]))

    def test_one_group_and_condition_per_range_row(self):
        for unit, rows in (("d17.k2", 14), ("d32.k2", 7), ("d41.k2", 10), ("d45.k2", 5), ("d47.k5", 4)):
            self.assertEqual(len(self.nodes(unit, "NhomDieuKien")), rows, unit)
        self.assertEqual(len(self.nodes("d17.k2", "DieuKien")), 14)
        self.assertEqual(len(self.nodes("d45.k2", "DieuKien")), 6)  # five ranges + the exclusion in "Yếu"
        self.assertEqual(self.nodes("d43.k1", "NhomDieuKien"), [])   # d43.k1 has no ranges, only gia_tri
        self.assertEqual(len(self.nodes("d43.k1", "KhaiNiem")), 10)  # 9 letter levels + "điểm chữ"

    def test_d17_k2_follows_pattern_a(self):
        by_id = {n["id"]: n for n in self.nodes("d17.k2")}
        edges = self.by_unit[P + "d17.k2"]["fragment"]["edges"]
        year2 = by_id["sem.KhaiNiem.nam_thu_hai"]["props"]
        self.assertEqual((year2["loai"], year2["thang"], year2["thu_tu"]), ("muc_phan_loai", "nam_dao_tao", 2))
        clc = "sem.DoiTuong.chuong_trinh_dao_tao_chat_luong_cao"
        group = next(e["from"] for e in edges if e["type"] == "AP_DUNG_CHO" and e["to"] == clc
                     and any(x["from"] == e["from"] and x["to"] == "sem.KhaiNiem.nam_thu_hai" and x["type"] == "DAN_DEN"
                             for x in edges))
        cond = next(e["to"] for e in edges if e["type"] == "GOM" and e["from"] == group)
        props = by_id[cond]["props"]
        self.assertEqual({k: props[k] for k in ("min", "max", "min_incl", "max_incl", "thang", "mo_ta")},
                         {"min": 40, "max": 80, "min_incl": True, "max_incl": True, "thang": "tin_chi",
                          "mo_ta": "Từ 40 – 80 tín chỉ"})
        self.assertTrue(any(e["type"] == "XET" and e["from"] == cond and e["to"] == "sem.KhaiNiem.so_tin_chi_tich_luy"
                            for e in edges))
        self.assertTrue(any(e["type"] == "LA_MOT" and e["from"] == "sem.KhaiNiem.nam_thu_hai"
                            and e["to"] == "sem.KhaiNiem.nam_dao_tao" for e in edges))

    def test_no_blank_cell_gives_a_row(self):
        levels = {(n["props"]["ten"]) for n in self.nodes("d17.k2", "KhaiNiem")}
        self.assertIn("năm thứ sáu", levels)  # the chuẩn column has year 6; CLC and tài năng stop at year 4
        programs = {(r["chuong_trinh"], r["muc"]) for r in rows_of("d17.k2")}
        self.assertNotIn(("chương trình đào tạo chất lượng cao", "năm thứ năm"), programs)
        self.assertNotIn(("chương trình đào tạo tài năng", "năm thứ sáu"), programs)

    def test_d45_yeu_excludes_students_forced_to_leave(self):
        edges = self.by_unit[P + "d45.k2"]["fragment"]["edges"]
        nodes = {n["id"]: n for n in self.nodes("d45.k2")}
        excl = next(n for n in nodes.values() if n["labels"] == ["DieuKien"] and n["props"].get("phu_dinh") is True)
        self.assertEqual(excl["props"]["toan_tu"], "la")
        self.assertTrue(any(e["type"] == "XET" and e["from"] == excl["id"] and e["to"] == "sem.HeQua.bi_buoc_thoi_hoc"
                            for e in edges))
        self.assertEqual(nodes["sem.HeQua.bi_buoc_thoi_hoc"]["props"]["loai"], "trang_thai")

    def test_d43_k1_sets_gia_tri_only_there(self):
        values = {n["props"]["ten"]: n["props"]["gia_tri"] for n in self.nodes("d43.k1", "KhaiNiem") if "gia_tri" in n["props"]}
        self.assertEqual(values, {"điểm A+": 4.0, "điểm A": 3.7, "điểm B+": 3.5, "điểm B": 3.0, "điểm C+": 2.5,
                                  "điểm C": 2.0, "điểm D+": 1.5, "điểm D": 1.0, "điểm F": 0})
        for n in self.nodes("d41.k2", "KhaiNiem"):
            self.assertNotIn("gia_tri", n["props"])
        # both fragments give the letter levels the same thang and thu_tu
        d41 = {n["id"]: n["props"] for n in self.nodes("d41.k2", "KhaiNiem")}
        for n in self.nodes("d43.k1", "KhaiNiem"):
            if n["id"] in d41 and "thu_tu" in n["props"]:
                self.assertEqual((n["props"]["thang"], n["props"]["thu_tu"]), (d41[n["id"]]["thang"], d41[n["id"]]["thu_tu"]))

    def test_d41_p_has_no_scale_and_no_thu_tu(self):
        p = next(n for n in self.nodes("d41.k2", "KhaiNiem") if n["props"]["ten"] == "điểm P")
        self.assertEqual(p["props"]["loai"], "muc_phan_loai")
        self.assertNotIn("thang", p["props"])
        self.assertNotIn("thu_tu", p["props"])
        edges = self.by_unit[P + "d41.k2"]["fragment"]["edges"]
        self.assertFalse(any(e["type"] == "LA_MOT" and e["from"] == p["id"] for e in edges))
        group = next(n for n in self.nodes("d41.k2", "NhomDieuKien") if n["props"]["nguon"] == [P + "d41.k2.b"])
        self.assertIn("không tính vào điểm trung bình học tập", group["props"]["mo_ta"])

    def test_d32_k2_bonus_rows(self):
        bonus = {n["props"]["ten"]: (n["props"]["gia_tri"], n["props"]["don_vi"], n["props"]["loai"])
                 for n in self.nodes("d32.k2", "HeQua")}
        self.assertEqual(len(bonus), 7)  # a row never merges with another: name = rank + body
        self.assertEqual(bonus["điểm thưởng giải nhất cấp Đại học Quốc gia Hà Nội, cấp Bộ Giáo dục và Đào tạo"],
                         (0.2, "điểm", "quyen_loi"))
        self.assertEqual(bonus["điểm thưởng giải ba cấp đơn vị đào tạo"], (0.05, "điểm", "quyen_loi"))
        self.assertEqual(sorted(v[0] for v in bonus.values()), [0.05, 0.07, 0.07, 0.1, 0.1, 0.15, 0.2])

    def test_sources_are_the_rows_own_unit_or_point(self):
        for record in self.records:
            allowed = ex.allowed_nguon(record["unit"], BY_ID, CHILDREN)
            for n in record["fragment"]["nodes"]:
                self.assertTrue(set(n["props"]["nguon"]) <= allowed, n["id"])
            for e in record["fragment"]["edges"]:
                self.assertIn(e["props"]["nguon"], allowed)

    def test_building_twice_gives_identical_records(self):
        again, _, _ = build()
        self.assertEqual(json.dumps(again, sort_keys=True), json.dumps(self.records, sort_keys=True))


class IdsAgreeWithTheLlmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records, _, _ = build()
        cls.table_ids = {n["id"] for r in cls.records for n in r["fragment"]["nodes"]}

    def test_names_give_the_ids_of_the_same_names(self):
        expected = {
            "năm thứ hai": ("KhaiNiem", "sem.KhaiNiem.nam_thu_hai"),
            "năm đào tạo": ("KhaiNiem", "sem.KhaiNiem.nam_dao_tao"),
            "số tín chỉ tích lũy": ("KhaiNiem", "sem.KhaiNiem.so_tin_chi_tich_luy"),
            "điểm học phần": ("KhaiNiem", "sem.KhaiNiem.diem_hoc_phan"),
            "điểm chữ": ("KhaiNiem", "sem.KhaiNiem.diem_chu"),
            "điểm F": ("KhaiNiem", "sem.KhaiNiem.diem_f"),
            "điểm trung bình chung học kỳ": ("KhaiNiem", "sem.KhaiNiem.diem_trung_binh_chung_hoc_ky"),
            "điểm trung bình chung tích lũy": ("KhaiNiem", "sem.KhaiNiem.diem_trung_binh_chung_tich_luy"),
            "bị buộc thôi học": ("HeQua", "sem.HeQua.bi_buoc_thoi_hoc"),
            "chương trình đào tạo chất lượng cao": ("DoiTuong", "sem.DoiTuong.chuong_trinh_dao_tao_chat_luong_cao"),
        }
        for ten, (label, gid) in expected.items():
            with self.subTest(ten=ten):
                self.assertEqual(sid.global_id(label, ten, "u", "x"), gid)
                self.assertIn(gid, self.table_ids)
        # a spelling variant of the LLM maps to the same id
        self.assertEqual(sid.global_id("KhaiNiem", "điểm trung bình chung tích luỹ", "u", "x"),
                         "sem.KhaiNiem.diem_trung_binh_chung_tich_luy")

    def test_unit_scoped_ids_use_the_bang_prefix(self):
        scoped = [i for i in self.table_ids if i.startswith(("sem.NhomDieuKien.", "sem.DieuKien."))]
        self.assertEqual(len(scoped), 14 + 14 + 7 + 7 + 10 + 10 + 5 + 6 + 4 + 4)
        self.assertTrue(all(f".{tb.LOCAL_PREFIX}" in i for i in scoped))

    def test_prompt_examples_never_use_the_bang_prefix(self):
        examples = ex.load_examples()
        local = {n["id"] for e in examples for label, nodes in e["output"].items() if label != "edges" for n in nodes}
        self.assertFalse({i for i in local if i.startswith(tb.LOCAL_PREFIX)})

    @unittest.skipUnless(LLM_RECORDS, "data/processed/semantic/extract/ is not in the repository")
    def test_llm_records_use_the_same_names_and_never_the_bang_prefix(self):
        llm_ids, llm_local = set(), set()
        for path in LLM_RECORDS:
            record = json.loads(Path(path).read_text(encoding="utf-8"))
            for n in (record.get("fragment") or {}).get("nodes", []):
                llm_ids.add(n["id"])
            for label, nodes in ((record.get("raw") or {}).get("parsed") or {}).items():
                if label != "edges" and isinstance(nodes, list):
                    llm_local |= {n["id"] for n in nodes if isinstance(n, dict) and "id" in n}
        self.assertFalse({i for i in llm_local if i.startswith(tb.LOCAL_PREFIX)})
        self.assertFalse(llm_ids & {i for i in self.table_ids if ".bang_" in i})
        shared = {"sem.KhaiNiem.nam_dao_tao", "sem.KhaiNiem.so_tin_chi_tich_luy", "sem.KhaiNiem.diem_chu",
                  "sem.KhaiNiem.diem_hoc_phan", "sem.KhaiNiem.diem_trung_binh_chung_hoc_ky",
                  "sem.KhaiNiem.diem_trung_binh_chung_tich_luy", "sem.HeQua.bi_buoc_thoi_hoc",
                  "sem.DoiTuong.chuong_trinh_dao_tao_chat_luong_cao", "sem.DoiTuong.chuong_trinh_dao_tao_chuan",
                  "sem.DoiTuong.chuong_trinh_dao_tao_tai_nang", "sem.KhaiNiem.diem_f"}
        self.assertEqual(shared - llm_ids, set(), "the LLM names these differently from the tables")
        self.assertEqual(shared - self.table_ids, set())


def reply(*nodes_by_label, edges=()):
    out = {label: [] for label in ss.NODE_LABELS}
    for label, node in nodes_by_label:
        out[label].append(node)
    return {**out, "edges": list(edges)}


class ValidatorModeTests(unittest.TestCase):
    UNIT = P + "d17.k2"

    def level(self, **extra):
        return reply(("KhaiNiem", {"id": "k1", "ten": "năm thứ hai", "bi_danh": [], "nguon": [self.UNIT],
                                   "loai": "muc_phan_loai", "thang": "nam_dao_tao", **extra}))

    def check(self, output, mode):
        return ss.validate(output, {self.UNIT}, set(BY_ID), nguon_loai=mode)

    def test_llm_mode_still_rejects_thu_tu(self):
        report = self.check(self.level(thu_tu=2), "trich_xuat")
        self.assertFalse(report.ok)
        self.assertEqual([e for e in report.errors if "set by script" in e].__len__(), 1)
        self.assertEqual(len(report.errors), 1)  # no extra noise from the table-only rules
        self.assertFalse(ss.validate(self.level(thu_tu=2), {self.UNIT}, set(BY_ID)).ok)  # default mode

    def test_table_mode_accepts_thu_tu_on_a_level(self):
        report = self.check(self.level(thu_tu=2), "bang")
        self.assertEqual((report.errors, report.warnings), ([], []))

    def test_table_mode_checks_thu_tu(self):
        for bad in (0, 2.5, -1):
            with self.subTest(thu_tu=bad):
                self.assertTrue(any("integer >= 1" in e for e in self.check(self.level(thu_tu=bad), "bang").errors))
        self.assertTrue(any("must be number" in e for e in self.check(self.level(thu_tu="2"), "bang").errors))
        no_scale = reply(("KhaiNiem", {"id": "k1", "ten": "điểm P", "bi_danh": [], "nguon": [self.UNIT],
                                       "loai": "muc_phan_loai", "thu_tu": 1}))
        self.assertTrue(any("belongs to a level" in e for e in self.check(no_scale, "bang").errors))
        quantity = reply(("KhaiNiem", {"id": "k1", "ten": "năm đào tạo", "bi_danh": [], "nguon": [self.UNIT],
                                       "loai": "dai_luong", "thu_tu": 1}))
        self.assertTrue(any("belongs to a level" in e for e in self.check(quantity, "bang").errors))

    def test_thu_tu_is_still_refused_on_other_labels_in_table_mode(self):
        output = reply(("DoiTuong", {"id": "o1", "ten": "sinh viên", "bi_danh": [], "nguon": [self.UNIT],
                                     "loai": "nguoi", "thu_tu": 1}))
        self.assertFalse(self.check(output, "bang").ok)

    def test_unknown_mode_is_an_error(self):
        with self.assertRaises(ValueError):
            self.check(self.level(), "nhap_tay")


class BuildFragmentTests(unittest.TestCase):
    def test_nguon_loai_defaults_to_trich_xuat_and_can_be_bang(self):
        output = reply(("KhaiNiem", {"id": "k1", "ten": "điểm chữ", "bi_danh": [], "nguon": [P + "d41.k2"],
                                     "loai": "dai_luong"}))
        default = ex.build_fragment(output, P + "d41.k2")
        self.assertEqual(default["meta"]["nguon_loai"], "trich_xuat")
        self.assertEqual(default["nodes"][0]["props"]["nguon_loai"], "trich_xuat")
        table = ex.build_fragment(output, P + "d41.k2", nguon_loai="bang")
        self.assertEqual((table["meta"]["nguon_loai"], table["nodes"][0]["props"]["nguon_loai"]), ("bang", "bang"))


class CliTests(unittest.TestCase):
    def run_main(self, tmp: Path, *args, tables_path: Path = tb.TABLES_PATH):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = tb.main(list(args), out_dir=tmp / "bang", check_csv=tmp / "bang_kiem_tay.csv",
                           tables_path=tables_path)
        return code, out.getvalue(), err.getvalue()

    def test_check_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            code, out, _ = self.run_main(Path(d), "--check")
            self.assertEqual(code, 0)
            self.assertIn("ĐẠT", out)
            self.assertIn("49", out)
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_write_makes_six_fragments_and_a_check_csv(self):
        with tempfile.TemporaryDirectory() as d:
            code, _, err = self.run_main(Path(d))
            self.assertEqual((code, err), (0, ""))
            self.assertEqual(sorted(p.name for p in (Path(d) / "bang").iterdir()),
                             sorted(f"{u}.json" for u in tb.EXPECTED_ROWS))
            raw = (Path(d) / "bang_kiem_tay.csv").read_bytes()
            self.assertNotIn(b"\r", raw)  # LF
            rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
            self.assertEqual(len(rows), 49)
            self.assertEqual(list(rows[0]), tb.CSV_FIELDS)
            self.assertTrue(all(r["verdict"] == "" and r["note"] == "" and r["nguyen_van"] and r["id_nut_dich"]
                                for r in rows))
            record = json.loads((Path(d) / "bang" / "qc3626.d17.k2.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(record), ["errors", "format", "fragment", "metadata", "raw", "status", "unit",
                                              "warnings"])  # same keys as the extraction records
            self.assertEqual(record["metadata"]["nguon_loai"], "bang")

    def test_a_mistyped_file_fails_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            bad = copy.deepcopy(DATA)
            bad["bang"][4]["dong"][1]["so"] = ["3,20", "3,58"]
            path = Path(d) / "bad.json"
            path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
            code, _, err = self.run_main(Path(d), tables_path=path)
            self.assertEqual(code, 1)
            self.assertIn("differs from the numbers", err)
            self.assertFalse((Path(d) / "bang").exists())
            self.assertFalse((Path(d) / "bang_kiem_tay.csv").exists())


if __name__ == "__main__":
    unittest.main()
