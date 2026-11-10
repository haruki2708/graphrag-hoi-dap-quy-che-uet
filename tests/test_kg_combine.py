"""Tests for src/kg/combine.py and src/kg/merge_candidates.py (fake data; no GPU, no model, no LLM)."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

from src.kg import combine as cb
from src.kg import merge_candidates as mc


def node(label, gid, ten, nguon, bi_danh=(), nguon_loai="trich_xuat", **props):
    return {"id": gid, "labels": [label], "props": {"id": gid, "ten": ten, "bi_danh": list(bi_danh),
                                                    "nguon_loai": nguon_loai, "nguon": list(nguon), **props}}


def edge(etype, src, dst, nguon, **props):
    return {"type": etype, "from": src, "to": dst, "props": {**props, "nguon": nguon}}


def item(source, kind, nodes, edges=()):
    return {"source": source, "kind": kind, "run": 0,
            "fragment": {"meta": {}, "nodes": list(nodes), "edges": list(edges)}}


def write_record(folder: Path, unit: str, run: int, status: str, marker: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{unit}.json" if run == 0 else f"{unit}.run{run}.json"
    fragment = {"meta": {"unit": unit, "marker": marker}, "nodes": [], "edges": []} if status == "ok" else None
    (folder / name).write_text(json.dumps({"unit": unit, "status": status, "fragment": fragment}), encoding="utf-8")


class SelectRecordsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.folder = self.root / "extract"

    def markers(self, items):
        return {i["source"]: (i["run"], i["fragment"]["meta"]["marker"]) for i in items}

    def test_run1_only_when_run0_is_not_ok(self):
        write_record(self.folder, "u1", 0, "ok", "u1-run0")
        write_record(self.folder, "u1", 1, "ok", "u1-run1")   # run 0 is ok: this file must be ignored
        write_record(self.folder, "u2", 0, "invalid", "x")
        write_record(self.folder, "u2", 1, "ok", "u2-run1")
        items, summary = cb.select_records(["u1", "u2"], self.folder)
        self.assertEqual(self.markers(items), {"extract:u1": (0, "u1-run0"), "extract:u2": (1, "u2-run1")})
        self.assertEqual((summary["from_run0"], summary["from_run1"], summary["run1_units"]), (1, 1, ["u2"]))

    def test_stops_when_a_unit_has_no_ok_record(self):
        write_record(self.folder, "u1", 0, "ok", "a")
        write_record(self.folder, "u2", 0, "invalid", "x")
        write_record(self.folder, "u2", 1, "invalid", "x")
        write_record(self.folder, "u3", 0, "invalid", "x")    # no run-1 file at all
        with self.assertRaises(cb.CombineError) as ctx:
            cb.select_records(["u1", "u2", "u3"], self.folder)
        message = str(ctx.exception)
        self.assertIn("2/3", message)
        self.assertIn("u2", message)
        self.assertIn("u3", message)
        self.assertNotIn("u1:", message)


class CombineFragmentsTest(unittest.TestCase):
    def test_same_id_joins_sources_aliases_and_loai(self):
        a = node("KhaiNiem", "sem.KhaiNiem.x", "điểm tích luỹ", ["u1"], bi_danh=["đtl"], loai="dai_luong")
        b = node("KhaiNiem", "sem.KhaiNiem.x", "điểm tích lũy", ["u2"], bi_danh=["đtl", "điểm TL"], loai="dai_luong",
                 dinh_nghia="def")
        nodes, edges, conflicts = cb.combine_fragments([item("extract:u1", "trich_xuat", [a]),
                                                        item("extract:u2", "trich_xuat", [b])])
        self.assertEqual(len(nodes), 1)
        props = nodes[0]["props"]
        self.assertEqual(props["ten"], "điểm tích luỹ")                         # first seen
        self.assertEqual(props["bi_danh"], ["điểm tích lũy", "đtl", "điểm TL"])  # other ten becomes an alias
        self.assertEqual(props["nguon"], ["u1", "u2"])
        self.assertEqual(props["nguon_loai"], ["trich_xuat"])
        self.assertEqual(props["dinh_nghia"], "def")                             # only on one side: union, no conflict
        self.assertEqual(conflicts, [])

    def test_conflict_is_recorded_and_the_table_wins(self):
        llm = node("KhaiNiem", "sem.KhaiNiem.nam", "năm đào tạo", ["u1"], loai="khac", thu_tu=None)
        table = node("KhaiNiem", "sem.KhaiNiem.nam", "năm đào tạo", ["t1"], nguon_loai="bang", loai="dai_luong")
        # LLM fragment listed first: tables are still read first
        nodes, _, conflicts = cb.combine_fragments([item("extract:u1", "trich_xuat", [llm]),
                                                    item("bang:t1", "bang", [table])])
        props = nodes[0]["props"]
        self.assertEqual(props["loai"], "dai_luong")
        self.assertEqual(props["nguon_loai"], ["bang", "trich_xuat"])
        self.assertEqual(len(conflicts), 1)
        c = conflicts[0]
        self.assertEqual((c["id"], c["property"], c["kept"], c["kept_from"]),
                         ("sem.KhaiNiem.nam", "loai", "dai_luong", "bang:t1"))
        self.assertEqual(c["dropped"], [{"value": "khac", "from": "extract:u1"}])

    def test_conflict_without_a_table_keeps_the_first_value(self):
        a = node("HeQua", "sem.HeQua.h", "bị buộc thôi học", ["u1"], loai="trang_thai")
        b = node("HeQua", "sem.HeQua.h", "bị buộc thôi học", ["u2"], loai="che_tai")
        nodes, _, conflicts = cb.combine_fragments([item("extract:u1", "trich_xuat", [a]),
                                                    item("extract:u2", "trich_xuat", [b])])
        self.assertEqual(nodes[0]["props"]["loai"], "trang_thai")
        self.assertEqual([(c["property"], c["kept"], c["dropped"][0]["value"]) for c in conflicts],
                         [("loai", "trang_thai", "che_tai")])

    def test_unit_scoped_id_in_two_fragments_is_an_error(self):
        n = node("DieuKien", "sem.DieuKien.u1.c1", "x", ["u1"], mo_ta="x")
        with self.assertRaises(cb.CombineError):
            cb.combine_fragments([item("extract:u1", "trich_xuat", [n]), item("extract:u1b", "trich_xuat", [n])])

    def test_equal_edges_join_and_nguon_becomes_a_list(self):
        a = node("KhaiNiem", "sem.KhaiNiem.a", "a", ["u1"], loai="dai_luong")
        b = node("KhaiNiem", "sem.KhaiNiem.b", "b", ["u1"], loai="dai_luong")
        e1 = edge("LA_MOT", a["id"], b["id"], "u1")
        e2 = edge("LA_MOT", a["id"], b["id"], "u2")
        other = edge("TINH_VAO", a["id"], b["id"], "u1", co=True, boi_canh=None)
        other2 = edge("TINH_VAO", a["id"], b["id"], "u2", co=False, boi_canh=None)  # differs in co: stays apart
        _, edges, _ = cb.combine_fragments([item("extract:u1", "trich_xuat", [a, b], [e1, other]),
                                            item("extract:u2", "trich_xuat", [a, b], [e2, other2])])
        by_type = {}
        for e in edges:
            by_type.setdefault(e["type"], []).append(e)
        self.assertEqual(len(by_type["LA_MOT"]), 1)
        self.assertEqual(by_type["LA_MOT"][0]["props"]["nguon"], ["u1", "u2"])
        self.assertEqual(len(by_type["TINH_VAO"]), 2)


class ApplyMergesTest(unittest.TestCase):
    def setUp(self):
        self.nodes = [
            node("HeQua", "sem.HeQua.canh_bao_hoc_vu", "cảnh báo học vụ", ["u1", "u2"], loai="trang_thai"),
            node("HeQua", "sem.HeQua.canh_bao_hoc_tap", "cảnh báo học tập", ["u3"], bi_danh=["CBHT"], loai="trang_thai",
                 gia_tri=1),
            node("HoatDong", "sem.HoatDong.chuyen_nganh", "chuyển ngành", ["u3"]),
            node("HoatDong", "sem.HoatDong.xep_nam", "xếp năm", ["u1"]),
            node("HoatDong", "sem.HoatDong.xep_nam_dao_tao", "xếp năm đào tạo", ["u2"]),
            node("KhaiNiem", "sem.KhaiNiem.tc", "tín chỉ", ["u1"], loai="dai_luong"),
            node("KhaiNiem", "sem.KhaiNiem.tu_do", "tự chọn tự do", ["u1"], loai="loai_hoc_phan"),
            node("NhomDieuKien", "sem.NhomDieuKien.u3.g1", "g", ["u3"], ket_hop="AND", mo_ta="g"),
        ]
        self.edges = [
            edge("YEU_CAU", "sem.HoatDong.chuyen_nganh", "sem.NhomDieuKien.u3.g1", "u3"),
            edge("DAN_DEN", "sem.NhomDieuKien.u3.g1", "sem.HeQua.canh_bao_hoc_tap", "u3"),
            edge("DAN_DEN", "sem.NhomDieuKien.u3.g1", "sem.HeQua.canh_bao_hoc_vu", "u3"),   # becomes a duplicate
            edge("TINH_VAO", "sem.KhaiNiem.tu_do", "sem.KhaiNiem.tc", "u1", co=False, boi_canh="sem.HoatDong.xep_nam"),
        ]

    def row(self, label, giu, bo, decision="gop"):
        return {"label": label, "id_giu": giu, "id_bo": bo, "quyet_dinh": decision, "ghi_chu": ""}

    def test_rewires_edges_moves_aliases_and_rewrites_boi_canh(self):
        rows = [self.row("HeQua", "sem.HeQua.canh_bao_hoc_vu", "sem.HeQua.canh_bao_hoc_tap"),
                self.row("HoatDong", "sem.HoatDong.xep_nam_dao_tao", "sem.HoatDong.xep_nam")]
        nodes, edges, info = cb.apply_merges(self.nodes, self.edges, rows)
        ids = {n["id"] for n in nodes}
        self.assertNotIn("sem.HeQua.canh_bao_hoc_tap", ids)
        self.assertNotIn("sem.HoatDong.xep_nam", ids)
        kept = next(n for n in nodes if n["id"] == "sem.HeQua.canh_bao_hoc_vu")["props"]
        self.assertEqual(kept["bi_danh"], ["cảnh báo học tập", "CBHT"])
        self.assertEqual(kept["nguon"], ["u1", "u2", "u3"])
        self.assertEqual(kept["gia_tri"], 1)                      # only the dropped node had it: copied
        dan_den = [e for e in edges if e["type"] == "DAN_DEN"]
        self.assertEqual([(e["to"], e["props"]["nguon"]) for e in dan_den], [("sem.HeQua.canh_bao_hoc_vu", ["u3"])])
        self.assertEqual(info["duplicate_edges_joined"], 1)
        tinh_vao = next(e for e in edges if e["type"] == "TINH_VAO")
        self.assertEqual(tinh_vao["props"]["boi_canh"], "sem.HoatDong.xep_nam_dao_tao")
        self.assertEqual(len(info["merges_applied"]), 2)
        errors, _ = cb.validate_graph(nodes, edges)
        self.assertEqual(errors, [])

    def test_only_rows_marked_gop_are_applied(self):
        rows = [self.row("HeQua", "sem.HeQua.canh_bao_hoc_vu", "sem.HeQua.canh_bao_hoc_tap", decision="khong"),
                self.row("HeQua", "sem.HeQua.canh_bao_hoc_vu", "sem.HeQua.canh_bao_hoc_tap", decision="")]
        nodes, edges, info = cb.apply_merges(self.nodes, self.edges, rows)
        self.assertEqual(len(nodes), len(self.nodes))
        self.assertEqual(len(edges), len(self.edges))
        self.assertEqual(info["merges_applied"], [])
        self.assertEqual(info["skipped"], {"khong": 1, "(trống)": 1})

    def test_merging_different_labels_is_refused(self):
        with self.assertRaisesRegex(cb.CombineError, "different labels"):
            cb.apply_merges(self.nodes, self.edges, [self.row("HeQua", "sem.HeQua.canh_bao_hoc_vu", "sem.HoatDong.chuyen_nganh")])

    def test_unknown_id_chain_and_unit_scoped_label_are_refused(self):
        with self.assertRaisesRegex(cb.CombineError, "not found"):
            cb.apply_merges(self.nodes, self.edges, [self.row("HeQua", "sem.HeQua.nope", "sem.HeQua.canh_bao_hoc_tap")])
        chain = [self.row("HoatDong", "sem.HoatDong.xep_nam_dao_tao", "sem.HoatDong.xep_nam"),
                 self.row("HoatDong", "sem.HoatDong.chuyen_nganh", "sem.HoatDong.xep_nam_dao_tao")]
        with self.assertRaisesRegex(cb.CombineError, "chain"):
            cb.apply_merges(self.nodes, self.edges, chain)
        with self.assertRaisesRegex(cb.CombineError, "not mergeable"):
            cb.apply_merges(self.nodes + [node("NhomDieuKien", "sem.NhomDieuKien.u3.g2", "g2", ["u3"], ket_hop="OR", mo_ta="g")],
                            self.edges, [self.row("NhomDieuKien", "sem.NhomDieuKien.u3.g1", "sem.NhomDieuKien.u3.g2")])

    def test_wrong_label_column_is_refused(self):
        with self.assertRaisesRegex(cb.CombineError, "column label"):
            cb.apply_merges(self.nodes, self.edges, [self.row("HoatDong", "sem.HeQua.canh_bao_hoc_vu", "sem.HeQua.canh_bao_hoc_tap")])

    def test_edge_between_the_two_nodes_becomes_a_dropped_self_loop(self):
        nodes = [node("KhaiNiem", "sem.KhaiNiem.a", "a", ["u1"], loai="dai_luong"),
                 node("KhaiNiem", "sem.KhaiNiem.b", "b", ["u1"], loai="dai_luong")]
        edges = [edge("LA_MOT", "sem.KhaiNiem.a", "sem.KhaiNiem.b", "u1")]
        nodes, edges, info = cb.apply_merges(nodes, edges, [self.row("KhaiNiem", "sem.KhaiNiem.a", "sem.KhaiNiem.b")])
        self.assertEqual(edges, [])
        self.assertEqual(len(info["self_loops_dropped"]), 1)
        self.assertEqual(info["self_loops_dropped"][0]["node"], "sem.KhaiNiem.a")

    def test_property_difference_between_merged_nodes_is_a_conflict(self):
        nodes = [node("KhaiNiem", "sem.KhaiNiem.a", "a", ["u1"], loai="dai_luong"),
                 node("KhaiNiem", "sem.KhaiNiem.b", "b", ["u1"], loai="khac")]
        nodes, _, info = cb.apply_merges(nodes, [], [self.row("KhaiNiem", "sem.KhaiNiem.a", "sem.KhaiNiem.b")])
        self.assertEqual(nodes[0]["props"]["loai"], "dai_luong")
        self.assertEqual([(c["property"], c["phase"]) for c in info["conflicts"]], [("loai", "merge")])

    def test_merge_list_with_a_bom_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gop.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as f:
                csv.writer(f).writerows([cb.MERGE_FIELDS, ["HeQua", "g", "b", " gop ", "ok"]])
            rows = cb.read_merge_list(path)
            self.assertEqual(rows[0]["label"], "HeQua")
            self.assertEqual(rows[0]["quyet_dinh"], "gop")
            self.assertEqual(cb.read_merge_list(Path(tmp) / "missing.csv"), [])
            empty = Path(tmp) / "empty.csv"
            empty.write_text(",".join(cb.MERGE_FIELDS) + "\n", encoding="utf-8")
            self.assertEqual(cb.read_merge_list(empty), [])


class ValidateGraphTest(unittest.TestCase):
    def test_reports_dangling_edge_wrong_labels_and_missing_nguon(self):
        nodes = [node("KhaiNiem", "sem.KhaiNiem.a", "a", ["u1"], loai="dai_luong"),
                 node("HeQua", "sem.HeQua.h", "h", [], loai="trang_thai")]
        edges = [edge("LA_MOT", "sem.KhaiNiem.a", "sem.KhaiNiem.missing", ["u1"]),
                 edge("LA_MOT", "sem.KhaiNiem.a", "sem.HeQua.h", ["u1"]),
                 edge("TINH_VAO", "sem.KhaiNiem.a", "sem.KhaiNiem.a", ["u1"], co=True, boi_canh="sem.KhaiNiem.a")]
        errors, _ = cb.validate_graph(nodes, edges)
        text = "\n".join(errors)
        self.assertIn("sem.HeQua.h: node has no nguon", text)
        self.assertIn("to node does not exist", text)
        self.assertIn("KhaiNiem -> HeQua is not allowed for LA_MOT", text)
        self.assertIn("boi_canh sem.KhaiNiem.a is not an existing HoatDong", text)
        self.assertIn("edge from a node to itself", text)


class CandidatesTest(unittest.TestCase):
    def kn(self, ten, nguon=("u1",), bi_danh=(), nguon_loai="trich_xuat", label="KhaiNiem"):
        gid = f"sem.{label}.{ten.replace(' ', '_')}"
        extra = {"loai": "dai_luong"} if label == "KhaiNiem" else {}
        return node(label, gid, ten, nguon, bi_danh=bi_danh, nguon_loai=[nguon_loai], **extra)

    def rows(self, nodes, **kw):
        rows, report = mc.build_candidates(nodes, None, **kw)
        return {(r["ten_a"], r["ten_b"]): r for r in rows}, report

    def test_search_key_unifies_spelling_and_finds_containment(self):
        rows, _ = self.rows([self.kn("điểm trung bình chung tích luỹ"), self.kn("điểm trung bình chung tích lũy"),
                             self.kn("điểm trung bình chung"), self.kn("sinh viên"), self.kn("sinh viên năm thứ hai")])
        pairs = {frozenset(k) for k in rows}
        self.assertIn(frozenset(("điểm trung bình chung tích luỹ", "điểm trung bình chung tích lũy")), pairs)
        self.assertIn(frozenset(("điểm trung bình chung", "điểm trung bình chung tích lũy")), pairs)
        for r in rows.values():
            self.assertEqual(r["ly_do"], "search_key")
            self.assertEqual(r["cosine"], "")
            self.assertEqual((r["quyet_dinh"], r["ghi_chu"]), ("", ""))

    def test_containment_is_by_whole_words_and_needs_two_tokens(self):
        rows, _ = self.rows([self.kn("ba"), self.kn("tín chỉ ba"), self.kn("điểm"), self.kn("điểm chữ"),
                             self.kn("tín chỉ"), self.kn("tín chỉ tích lũy"), self.kn("sinh viên"), self.kn("sinh viên giỏi")])
        pairs = {frozenset(k) for k in rows}
        self.assertNotIn(frozenset(("ba", "tín chỉ ba")), pairs)      # one token: never "contained"
        self.assertNotIn(frozenset(("điểm", "điểm chữ")), pairs)
        self.assertIn(frozenset(("tín chỉ", "tín chỉ tích lũy")), pairs)
        self.assertIn(frozenset(("sinh viên", "sinh viên giỏi")), pairs)
        rows, _ = self.rows([self.kn("chỉ"), self.kn("tín chỉ tích lũy")], min_contain_tokens=1)
        self.assertEqual(len(rows), 1)

    def test_pairs_stay_inside_one_label(self):
        rows, _ = self.rows([self.kn("cảnh báo học vụ", label="HeQua"), self.kn("cảnh báo học vụ", label="HoatDong"),
                             self.kn("cảnh báo học vụ", label="DoiTuong")])
        self.assertEqual(rows, {})

    def test_warning_for_quantity_words_numbers_and_programmes(self):
        w = mc.warnings_for("điểm trung bình chung học kỳ", "điểm trung bình chung tích lũy")
        self.assertTrue(any("học kỳ" in x for x in w) and any("tích lũy" in x for x in w))
        w = mc.warnings_for("điểm trung bình chung các học phần", "điểm trung bình chung")
        self.assertTrue(any("các học phần" in x for x in w))
        self.assertTrue(mc.warnings_for("năm thứ hai", "năm thứ ba"))
        self.assertTrue(mc.warnings_for("năm thứ năm", "năm thứ hai"))     # "năm" the year vs the number
        self.assertTrue(mc.warnings_for("điểm C", "điểm C+"))
        self.assertTrue(mc.warnings_for("học lực giỏi", "học lực khá"))
        self.assertTrue(mc.warnings_for("tối đa 18 tín chỉ", "tối đa 24 tín chỉ"))
        w = mc.warnings_for("chương trình đào tạo chuẩn", "chương trình đào tạo chất lượng cao")
        self.assertTrue(any("chương trình" in x for x in w))
        self.assertEqual(mc.warnings_for("điểm trung bình chung tích luỹ", "điểm trung bình chung tích lũy"), [])
        self.assertEqual(mc.warnings_for("tiêu chuẩn tốt nghiệp", "tiêu chuẩn tốt nghiệp"), [])

    def test_warning_appears_in_a_candidate_row(self):
        rows, _ = self.rows([self.kn("điểm trung bình chung"), self.kn("điểm trung bình chung tích lũy")])
        (row,) = rows.values()
        self.assertIn("tích lũy", row["canh_bao"])

    def test_schema_rows_come_first_and_pair_the_warning_variants(self):
        nodes = [self.kn("bị cảnh báo học vụ", label="HeQua"), self.kn("bị cảnh báo học tập", label="HeQua"),
                 self.kn("cảnh báo kết quả học tập", label="HeQua"), self.kn("bị buộc thôi học", label="HeQua"),
                 self.kn("cảnh báo học vụ", label="HoatDong"), self.kn("xét cảnh báo học vụ", label="HoatDong"),
                 self.kn("sinh viên năm thứ hai"), self.kn("sinh viên năm thứ ba")]
        rows, report = mc.build_candidates(nodes, None)
        schema = [r for r in rows if r["ly_do"] == "schema"]
        self.assertEqual(rows[:len(schema)], schema)
        self.assertEqual(len(schema), 4)       # 3 HeQua variants -> 3 pairs; 2 HoatDong -> 1 pair; no cross-label pair
        self.assertEqual({r["label"] for r in schema}, {"HeQua", "HoatDong"})
        self.assertNotIn("bị buộc thôi học", {r["ten_a"] for r in schema} | {r["ten_b"] for r in schema})
        self.assertEqual(report["n_schema_pairs"], 4)

    def test_schema_pairs_table_node_with_llm_node_of_the_same_concept(self):
        table = self.kn("số tín chỉ tích lũy", nguon_loai="bang")
        llm = self.kn("tổng số tín chỉ tích lũy của sinh viên")
        other = self.kn("điểm rèn luyện")
        rows, report = mc.build_candidates([table, llm, other], None)
        self.assertEqual([(r["ly_do"], r["goi_y_id_giu"]) for r in rows], [("schema", table["id"])])
        found = report["schema"]["số tín chỉ tích lũy (bảng/LLM)"]
        self.assertEqual((found["bang"], found["llm"]), ([table["id"]], [llm["id"]]))

    def test_programme_variants_are_paired_but_never_across_programmes(self):
        def dt(ten):
            return node("DoiTuong", f"sem.DoiTuong.{ten}", ten, ["u1"], nguon_loai=["trich_xuat"], loai="chuong_trinh")

        nodes = [dt("chương trình đào tạo chất lượng cao"), dt("CTĐT chất lượng cao"), dt("chương trình đào tạo tài năng"),
                 dt("CTĐT tài năng"), dt("chương trình đào tạo chuẩn")]
        rows, _ = mc.build_candidates(nodes, None)
        schema = {frozenset((r["ten_a"], r["ten_b"])) for r in rows if r["ly_do"] == "schema"}
        self.assertEqual(schema, {frozenset(("chương trình đào tạo chất lượng cao", "CTĐT chất lượng cao")),
                                  frozenset(("chương trình đào tạo tài năng", "CTĐT tài năng"))})

    def test_suggested_keeper_prefers_the_table_node_then_more_sources(self):
        table = self.kn("tín chỉ tích lũy", nguon=("t",), nguon_loai="bang")
        many = self.kn("số tín chỉ tích lũy", nguon=("u1", "u2", "u3"))
        few = self.kn("tín chỉ tích lũy học", nguon=("u1",))
        self.assertEqual(mc.suggest_keep(table, many), table["id"])
        self.assertEqual(mc.suggest_keep(many, few), many["id"])
        self.assertEqual(mc.suggest_keep(few, many), many["id"])

    def test_cosine_reason_and_sort_order(self):
        a, b, c = self.kn("điểm khóa luận"), self.kn("điểm đồ án"), self.kn("hạng tốt nghiệp")
        cos = {frozenset((a["id"], b["id"])): 0.9, frozenset((a["id"], c["id"])): 0.86, frozenset((b["id"], c["id"])): 0.3}
        rows, _ = mc.build_candidates([a, b, c], lambda x, y: cos[frozenset((x, y))], threshold=0.85)
        self.assertEqual([(r["ly_do"], r["cosine"]) for r in rows], [("cosine", "0.9000"), ("cosine", "0.8600")])
        rows, _ = mc.build_candidates([a, b, c], lambda x, y: cos[frozenset((x, y))], threshold=0.95)
        self.assertEqual(rows, [])

    def test_csv_columns_and_bom(self):
        rows, _ = mc.build_candidates([self.kn("tín chỉ"), self.kn("tín chỉ tích lũy")], None)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.csv"
            mc.write_csv(path, rows)
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
            with path.open(encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                self.assertEqual(tuple(reader.fieldnames), mc.CSV_FIELDS)
                self.assertEqual(len(list(reader)), 1)


if __name__ == "__main__":
    unittest.main()
