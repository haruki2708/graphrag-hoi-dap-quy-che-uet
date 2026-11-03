"""Tests for the semantic extraction core: src/kg/semantic_schema.py, src/kg/extract.py, src/kg/export_triplets.py.

No API call anywhere: the LLM is a fake complete() returning a hand-written reply. One test runs the real
src/llm/core.complete() (cache layer) with a patched provider adapter; it is skipped when the provider packages
(httpx, python-dotenv) are not installed.
Run from the repo root:  python -m unittest discover -s tests -t .
"""

import copy
import csv
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src.kg import export_triplets as et
from src.kg import extract as ex
from src.kg import semantic_schema as ss

P = "qc3626."
CONTENT_UNITS = Path(__file__).resolve().parents[1] / "data" / "processed" / "qc3626_content_units.jsonl"
UNIT = P + "d44.k2"

# Hand-written reply for d44.k2 ("Buộc thôi học", OR of 5 cases); valid against schema 2.1/2.2.
FIXTURE = {
    "KhaiNiem": [
        {"id": "k1", "ten": "thời gian tối đa được phép học", "bi_danh": [], "nguon": [P + "d44.k2.b"], "loai": "dai_luong"},
    ],
    "DoiTuong": [
        {"id": "o1", "ten": "thủ trưởng đơn vị đào tạo", "bi_danh": [], "nguon": [P + "d44.k2.a"], "loai": "chuc_danh"},
    ],
    "HoatDong": [],
    "NhomDieuKien": [
        {"id": "g1", "ten": "các trường hợp bị buộc thôi học", "bi_danh": [], "nguon": [P + "d44.k2"],
         "ket_hop": "OR", "mo_ta": "Sinh viên bị buộc thôi học nếu thuộc một trong các trường hợp a–đ"},
    ],
    "DieuKien": [
        {"id": "c1", "ten": "số lần cảnh báo kết quả học tập vượt quá giới hạn", "bi_danh": [], "nguon": [P + "d44.k2.a"],
         "mo_ta": "Có số lần cảnh báo kết quả học tập vượt quá giới hạn theo quy định của thủ trưởng đơn vị đào tạo",
         "toan_tu": "co"},
        {"id": "c2", "ten": "vượt quá thời gian tối đa được phép học", "bi_danh": [], "nguon": [P + "d44.k2.b"],
         "mo_ta": "Vượt quá thời gian tối đa được phép học", "toan_tu": "co"},
        {"id": "c3", "ten": "không đăng ký học 2 học kỳ liên tiếp", "bi_danh": ["đăng kí"], "nguon": [P + "d44.k2.d"],
         "mo_ta": "Không đăng ký học 2 học kỳ liên tiếp", "min": 2, "min_incl": True, "don_vi": "học kỳ"},
    ],
    "HeQua": [
        {"id": "q1", "ten": "bị buộc thôi học", "bi_danh": [], "nguon": [P + "d44.k2"], "loai": "trang_thai"},
    ],
    "edges": [
        {"type": "GOM", "from": "g1", "to": "c1", "nguon": P + "d44.k2.a"},
        {"type": "GOM", "from": "g1", "to": "c2", "nguon": P + "d44.k2.b"},
        {"type": "GOM", "from": "g1", "to": "c3", "nguon": P + "d44.k2.d"},
        {"type": "XET", "from": "c2", "to": "k1", "nguon": P + "d44.k2.b"},
        {"type": "DAN_DEN", "from": "g1", "to": "q1", "nguon": P + "d44.k2"},
        {"type": "GIAO_QUY_DINH", "from": "g1", "to": "o1", "nguon": P + "d44.k2.a",
         "noi_dung": "giới hạn số lần cảnh báo kết quả học tập"},
    ],
}


def fake_result(text: str, parsed=None, **kw):
    """Stand-in for src.llm.core.LLMResult (same attribute names)."""
    base = dict(text=text, parsed=parsed, role="extract", provider="anthropic", model_requested="claude-haiku-5-5",
                model_served="claude-haiku-5-5", run=0, finish_reason="end_turn", input_tokens=9000,
                output_tokens=800, latency_s=1.0, attempts=1, called_at="2026-11-03T10:00:00+07:00",
                from_cache=False, structured_output=True, truncated=False, params={"effort": "medium"})
    base.update(kw)
    return SimpleNamespace(**base)


class FakeComplete:
    def __init__(self, reply: dict | str, **kw):
        self.reply, self.kw, self.calls = reply, kw, []

    def __call__(self, role, system, messages, json_schema=None, run=0, **_):
        self.calls.append({"role": role, "system": system, "messages": messages, "schema": json_schema, "run": run})
        if isinstance(self.reply, Exception):
            raise self.reply
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply, ensure_ascii=False)
        parsed = None if isinstance(self.reply, str) else self.reply
        return fake_result(text, parsed, role=role, run=run, **self.kw)


CTX = None


def context() -> ex.Context:
    global CTX
    if CTX is None:
        CTX = ex.make_context()
    return CTX


def jsonl_units() -> list[dict]:
    with CONTENT_UNITS.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class ScopeTests(unittest.TestCase):
    def test_q9_scope_has_34_articles(self):
        self.assertEqual(len(ex.SCOPE_ARTICLES), 34)
        self.assertEqual(len(set(ex.SCOPE_ARTICLES)), 34)
        expected = [4, 5, 6, 7, 8, 13, 14] + list(range(16, 30)) + [32, 35, 36, 37, 38, 39] + list(range(41, 48))
        self.assertEqual(sorted(ex.SCOPE_ARTICLES), expected)

    def test_trial_and_table_units_are_inside_the_scope(self):
        self.assertEqual(ex.TRIAL_ARTICLES, (26, 28, 43, 44, 46))
        self.assertTrue(set(ex.TRIAL_ARTICLES) <= set(ex.SCOPE_ARTICLES))
        content = {u["id"]: u for u in jsonl_units()}
        self.assertEqual(len(ex.TABLE_UNITS), 6)
        for unit in ex.TABLE_UNITS:
            self.assertIn(unit, content)
            self.assertIn(content[unit]["dieu_number"], ex.SCOPE_ARTICLES)

    def test_trial_units_match_a_manual_filter_of_the_jsonl(self):
        manual = [u["id"] for u in jsonl_units() if u["dieu_number"] in (26, 28, 43, 44, 46)]
        self.assertEqual([u["id"] for u in ex.select_units(ex.TRIAL_ARTICLES)], manual)
        self.assertEqual(len(manual), 18)
        self.assertIn(P + "d44", manual)  # the Dieu-lead unit of article 44

    def test_full_scope_selects_137_units(self):
        manual = [u["id"] for u in jsonl_units() if u["dieu_number"] in ex.SCOPE_ARTICLES]
        self.assertEqual([u["id"] for u in ex.select_units(ex.SCOPE_ARTICLES)], manual)
        self.assertEqual(len(manual), 137)

    def test_parse_articles(self):
        self.assertEqual(ex.parse_articles("28, 26,26"), [26, 28])
        for bad in ("33", "26,50", "abc", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ex.parse_articles(bad)


class UnitTextTests(unittest.TestCase):
    def test_text_is_prefixed_and_rebuilds_the_unit_text(self):
        ctx = context()
        content = {u["id"]: u for u in jsonl_units()}
        for unit in ex.select_units(ex.SCOPE_ARTICLES):
            uid = unit["id"]
            with self.subTest(unit=uid):
                lines = ex.build_unit_text(uid, ctx.by_id, ctx.children).split("\n")
                self.assertEqual(lines[0], unit["heading"].split(", khoản")[0])
                pieces = ex.unit_parts(uid, ctx.by_id, ctx.children)
                self.assertEqual("\n".join(t for _, t in pieces), content[uid]["text"])
                allowed = ex.allowed_nguon(uid, ctx.by_id, ctx.children)
                for code, text in pieces:
                    self.assertIn(code, allowed)
                    self.assertIn(f"[{code}] {text}", "\n".join(lines))

    def test_clause_example(self):
        ctx = context()
        text = ex.build_unit_text(UNIT, ctx.by_id, ctx.children)
        self.assertTrue(text.startswith("Điều 44. Xử lý học vụ\n[qc3626.d44.k2] 2. Buộc thôi học"))
        self.assertIn("\n[qc3626.d44.k2.d] d) Không đăng ký học 2 học kỳ liên tiếp;\n"
                      "[qc3626.d44.k2] Chậm nhất là 1 tháng", text)  # moved paragraph keeps the clause id
        self.assertIn("\n[qc3626.d44.k2.đ] đ) Sau khi hết hạn", text)

    def test_dieu_lead_unit_cites_only_the_article(self):
        ctx = context()
        self.assertEqual(ex.allowed_nguon(P + "d44", ctx.by_id, ctx.children), {P + "d44"})
        text = ex.build_unit_text(P + "d35", ctx.by_id, ctx.children)
        self.assertTrue(text.startswith("Điều 35. Nghĩa vụ của sinh viên\n[qc3626.d35] Ngoài những nghĩa vụ chung"))
        self.assertNotIn("[qc3626.d35.k", text)

    def test_table_units_carry_the_skip_instruction(self):
        ctx = context()
        self.assertIn("BỎ QUA các dòng/mức của bảng", ex.build_user_message(P + "d17.k2", ctx.by_id, ctx.children))
        self.assertNotIn("BỎ QUA", ex.build_user_message(UNIT, ctx.by_id, ctx.children))


class PromptAndSchemaTests(unittest.TestCase):
    def test_examples_avoid_trial_articles_and_tables_and_are_valid(self):
        ctx = context()
        examples = ex.load_examples()
        self.assertGreaterEqual(len(examples), 2)
        for e in examples:
            with self.subTest(unit=e["unit"]):
                number = ctx.by_id[e["unit"]]["id"].split(".")[1]
                self.assertNotIn(int(number[1:]), ex.TRIAL_ARTICLES)
                self.assertIn(int(number[1:]), ex.SCOPE_ARTICLES)
                self.assertNotIn(e["unit"], ex.TABLE_UNITS)
                report = ss.validate(e["output"], ex.allowed_nguon(e["unit"], ctx.by_id, ctx.children), ctx.existing_ids)
                self.assertEqual(report.errors, [])
                self.assertEqual(report.warnings, [])
                self.assertIn(ex.build_user_message(e["unit"], ctx.by_id, ctx.children), ctx.system_prompt)
        self.assertNotIn("{{EXAMPLES}}", ctx.system_prompt)

    def test_json_schema_shape(self):
        schema = ss.json_schema()
        self.assertEqual(schema["required"], [*ss.NODE_LABELS, "edges"])
        blob = json.dumps(schema)
        for banned in ("$ref", "minimum", "maximum", "minLength", "thu_tu", "NGUON"):
            self.assertNotIn(banned, blob)

        def objects(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    yield node
                for v in node.values():
                    yield from objects(v)
            elif isinstance(node, list):
                for v in node:
                    yield from objects(v)

        for obj in objects(schema):
            self.assertIs(obj["additionalProperties"], False)
        edge_types = schema["properties"]["edges"]["items"]["properties"]["type"]["enum"]
        self.assertEqual(edge_types, ["GOM", "DAN_DEN", "YEU_CAU", "XET", "AP_DUNG_CHO", "LA_MOT", "TINH_VAO",
                                      "DUNG_DE", "GIAO_QUY_DINH"])
        self.assertEqual(len(ss.NODE_LABELS), 6)
        self.assertEqual(len(ss.schema_sha256()), 64)


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        ctx = context()
        self.allowed = ex.allowed_nguon(UNIT, ctx.by_id, ctx.children)
        self.existing = ctx.existing_ids
        self.reply = copy.deepcopy(FIXTURE)

    def check(self, reply=None, allowed=None) -> ss.Report:
        return ss.validate(reply if reply is not None else self.reply, allowed or self.allowed, self.existing)

    def assertRejected(self, needle: str, reply=None, allowed=None):
        report = self.check(reply, allowed)
        self.assertFalse(report.ok, "expected an error")
        self.assertTrue(any(needle in e for e in report.errors), report.errors)

    def node(self, label, local_id):
        return next(n for n in self.reply[label] if n["id"] == local_id)

    def test_fixture_is_valid(self):
        report = self.check()
        self.assertEqual(report.errors, [])
        self.assertEqual(report.warnings, [])

    def test_nguon_outside_the_unit(self):
        self.node("DieuKien", "c1")["nguon"] = [P + "d44.k1.a"]
        self.assertRejected("outside the unit")

    def test_edge_nguon_outside_the_unit(self):
        self.reply["edges"][0]["nguon"] = P + "d17.k1"
        self.assertRejected("outside the unit")

    def test_nguon_that_does_not_exist(self):
        self.node("HeQua", "q1")["nguon"] = [P + "d44.k2.z"]
        self.assertRejected("does not exist")

    def test_empty_nguon(self):
        self.node("HeQua", "q1")["nguon"] = []
        self.assertRejected("nguon is empty")

    def test_dieu_lead_unit_cannot_cite_its_clauses(self):
        ctx = context()
        allowed = ex.allowed_nguon(P + "d44", ctx.by_id, ctx.children)
        reply = {**{label: [] for label in ss.NODE_LABELS}, "edges": []}
        reply["HeQua"] = [{"id": "q1", "ten": "x", "bi_danh": [], "nguon": [P + "d44.k1"], "loai": "trang_thai"}]
        self.assertRejected("outside the unit", reply, allowed)

    def test_min_greater_than_max(self):
        self.node("DieuKien", "c3").update(max=1, max_incl=True)
        self.assertRejected("min 2 > max 1")

    def test_min_without_min_incl(self):
        del self.node("DieuKien", "c3")["min_incl"]
        self.assertRejected("min_incl must be true or false")

    def test_wrong_label_pair(self):
        self.reply["edges"].append({"type": "YEU_CAU", "from": "g1", "to": "q1", "nguon": UNIT})
        self.assertRejected("NhomDieuKien -> HeQua is not allowed for YEU_CAU")

    def test_gom_only_from_a_group(self):
        self.reply["edges"].append({"type": "GOM", "from": "c1", "to": "c2", "nguon": UNIT})
        self.assertRejected("is not allowed for GOM")

    def test_la_mot_needs_the_same_label(self):
        self.reply["edges"].append({"type": "LA_MOT", "from": "k1", "to": "o1", "nguon": UNIT})
        self.assertRejected("same label")

    def test_gom_cycle(self):
        self.reply["NhomDieuKien"].append({"id": "g2", "ten": "nhóm con", "bi_danh": [], "nguon": [UNIT],
                                           "ket_hop": "AND", "mo_ta": "x"})
        self.reply["edges"] += [{"type": "GOM", "from": "g1", "to": "g2", "nguon": UNIT},
                                {"type": "GOM", "from": "g2", "to": "g1", "nguon": UNIT}]
        self.assertRejected("GOM cycle")

    def test_empty_group(self):
        self.reply["NhomDieuKien"].append({"id": "g2", "ten": "nhóm rỗng", "bi_danh": [], "nguon": [UNIT],
                                           "ket_hop": "AND", "mo_ta": "x"})
        self.assertRejected("has no GOM edge")

    def test_boi_canh_must_be_a_hoatdong(self):
        ctx = context()
        example = copy.deepcopy(next(e for e in ex.load_examples() if e["unit"] == P + "d21.k2"))
        allowed = ex.allowed_nguon(example["unit"], ctx.by_id, ctx.children)
        tinh_vao = next(e for e in example["output"]["edges"] if e["type"] == "TINH_VAO")
        tinh_vao["boi_canh"] = "h1"  # a HoatDong of the same reply: fine
        self.assertTrue(self.check(example["output"], allowed).ok)
        tinh_vao["boi_canh"] = "k4"  # a KhaiNiem: rejected
        self.assertRejected("must be the id of a HoatDong", example["output"], allowed)
        tinh_vao["boi_canh"] = "nope"
        self.assertRejected("must be the id of a HoatDong", example["output"], allowed)

    def test_name_without_letters(self):
        self.node("HeQua", "q1")["ten"] = "!!!"
        self.assertRejected("empty id slug")
        self.node("HeQua", "q1")["ten"] = "  "
        self.assertRejected("empty ten")

    def test_duplicate_id(self):
        self.reply["HoatDong"].append({"id": "c1", "ten": "trùng id", "bi_danh": [], "nguon": [UNIT]})
        self.assertRejected("duplicate id")

    def test_thu_tu_is_rejected(self):
        self.reply["KhaiNiem"].append({"id": "k2", "ten": "năm thứ hai", "bi_danh": [], "nguon": [UNIT],
                                       "loai": "muc_phan_loai", "thang": "nam_dao_tao", "thu_tu": 2})
        self.assertRejected("set by script")

    def test_enum_values(self):
        self.node("NhomDieuKien", "g1")["ket_hop"] = "XOR"
        self.assertRejected("'ket_hop' = 'XOR'")
        reply = copy.deepcopy(FIXTURE)
        reply["edges"][0]["type"] = "NGUON"  # built by code, never by the LLM
        self.assertRejected("'type' = 'NGUON'", reply)
        reply = copy.deepcopy(FIXTURE)
        reply["DieuKien"][2]["thang"] = "thang_100"
        self.assertRejected("'thang' = 'thang_100'", reply)

    def test_unknown_and_misplaced_properties(self):
        self.node("HeQua", "q1")["muc_do"] = "cao"
        self.assertRejected("unknown property 'muc_do'")
        reply = copy.deepcopy(FIXTURE)
        reply["edges"][0]["co"] = True  # co only exists on TINH_VAO / DUNG_DE
        self.assertRejected("unknown property 'co'", reply)

    def test_required_edge_properties(self):
        del self.reply["edges"][-1]["noi_dung"]
        self.assertRejected("missing required property 'noi_dung'")

    def test_edge_to_unknown_node(self):
        self.reply["edges"].append({"type": "XET", "from": "c1", "to": "k9", "nguon": UNIT})
        self.assertRejected("'k9' is not a node id")

    def test_top_level_keys(self):
        reply = {k: v for k, v in self.reply.items() if k != "HeQua"}
        reply["Nguong"] = []
        self.assertRejected("missing key 'HeQua'", reply)
        self.assertRejected("unknown key 'Nguong'", reply)
        self.assertRejected("not a JSON object", [])

    def test_wrong_types(self):
        self.node("DieuKien", "c3")["min"] = "2"
        self.node("DieuKien", "c2")["phu_dinh"] = "true"
        report = self.check()
        self.assertTrue(any("'min' must be number" in e for e in report.errors), report.errors)
        self.assertTrue(any("'phu_dinh' must be boolean" in e for e in report.errors), report.errors)

    def test_soft_checks_are_warnings(self):
        self.reply["edges"].append({"type": "DAN_DEN", "from": "g1", "to": "k1", "nguon": UNIT})  # k1 is dai_luong
        report = self.check()
        self.assertTrue(report.ok, report.errors)
        self.assertTrue(any("expects ['muc_phan_loai']" in w for w in report.warnings), report.warnings)

    def test_bac_ngoai_ngu_scale_on_dieukien_only(self):
        self.reply["DieuKien"].append({"id": "c4", "ten": "trình độ ngoại ngữ bậc 3 trở lên", "bi_danh": [],
                                       "nguon": [UNIT], "mo_ta": "bậc 3 trở lên", "min": 3, "min_incl": True,
                                       "thang": "bac_ngoai_ngu"})
        self.reply["edges"].append({"type": "GOM", "from": "g1", "to": "c4", "nguon": UNIT})
        report = self.check()
        self.assertEqual((report.errors, report.warnings), ([], []))
        self.reply["KhaiNiem"].append({"id": "k2", "ten": "bậc 3", "bi_danh": [], "nguon": [UNIT],
                                       "loai": "muc_phan_loai", "thang": "bac_ngoai_ngu"})
        self.assertRejected("'thang' = 'bac_ngoai_ngu' not in")
        self.assertIn("bac_ngoai_ngu", ss.THANG_DIEU_KIEN)
        self.assertNotIn("bac_ngoai_ngu", ss.THANG_MUC)
        dk = ss.json_schema()["properties"]["DieuKien"]["items"]["properties"]["thang"]["enum"]
        kn = ss.json_schema()["properties"]["KhaiNiem"]["items"]["properties"]["thang"]["enum"]
        self.assertIn("bac_ngoai_ngu", dk)
        self.assertNotIn("bac_ngoai_ngu", kn)


class ExtractUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)

    def run_unit(self, fake, unit=UNIT, run=0):
        return ex.extract_unit(unit, "extract", run, context(), "claude-haiku-5-5", complete_fn=fake, out_dir=self.out)

    def test_fixture_end_to_end_to_fragment_and_csv(self):
        fake = FakeComplete(FIXTURE)
        record = self.run_unit(fake)
        self.assertEqual(record["status"], "ok", record["errors"])
        call = fake.calls[0]
        self.assertEqual((call["role"], call["run"]), ("extract", 0))
        self.assertIs(call["schema"], context().schema)
        self.assertIn("[qc3626.d44.k2.a] a) Có số lần cảnh báo", call["messages"][0]["content"])

        path = self.out / "extract" / f"{UNIT}.json"
        self.assertTrue(path.exists())
        self.assertNotIn(b"\r\n", path.read_bytes())
        saved = json.loads(path.read_text(encoding="utf-8"))
        meta = saved["metadata"]
        for key in ("unit", "role", "model_requested", "model_served", "run", "input_tokens", "output_tokens",
                    "finish_reason", "prompt_sha256", "schema_sha256", "called_at"):
            self.assertIn(key, meta)
        self.assertEqual(meta["prompt_sha256"], context().prompt_sha256)
        self.assertEqual(meta["schema_sha256"], ss.schema_sha256())
        self.assertEqual(saved["raw"]["parsed"], FIXTURE)

        fragment = saved["fragment"]
        ids = {n["id"] for n in fragment["nodes"]}
        self.assertIn("sem.HeQua.bi_buoc_thoi_hoc", ids)
        self.assertIn("sem.NhomDieuKien.qc3626.d44.k2.g1", ids)
        self.assertIn("sem.DieuKien.qc3626.d44.k2.c3", ids)
        self.assertIn("sem.DoiTuong.thu_truong_don_vi_dao_tao", ids)
        for n in fragment["nodes"]:
            self.assertEqual(n["props"]["nguon_loai"], "trich_xuat")
            self.assertTrue(n["props"]["nguon"])
            self.assertNotIn("thu_tu", n["props"])
        self.assertEqual(len(fragment["edges"]), len(FIXTURE["edges"]))
        self.assertTrue(all(e["props"]["nguon"].startswith(UNIT) for e in fragment["edges"]))

        rows, skipped = et.collect(et.load_records("extract", 0, self.out), et.source_texts())
        self.assertEqual(skipped, {})
        self.assertEqual(len(rows), len(FIXTURE["edges"]))
        gom = next(r for r in rows if r["edge_type"] == "GOM" and r["tail_ten"].startswith("vượt quá"))
        self.assertEqual((gom["head_label"], gom["tail_label"], gom["group_ket_hop"]), ("NhomDieuKien", "DieuKien", "OR"))
        self.assertEqual(gom["nguon"], P + "d44.k2.b")
        self.assertTrue(gom["nguon_text"].startswith("b) Vượt quá thời gian tối đa"))
        xet = next(r for r in rows if r["edge_type"] == "XET")
        self.assertEqual(xet["group_ket_hop"], "OR")  # the group that holds the head condition
        giao = next(r for r in rows if r["edge_type"] == "GIAO_QUY_DINH")
        self.assertEqual(json.loads(giao["edge_props"]), {"noi_dung": "giới hạn số lần cảnh báo kết quả học tập"})
        dk = next(r for r in rows if r["head_ten"] == "các trường hợp bị buộc thôi học" and r["tail_label"] == "DieuKien"
                  and r["tail_ten"].startswith("không đăng ký"))
        self.assertEqual(json.loads(dk["tail_props"])["min"], 2)
        csv_path = self.out / "t.csv"
        et.write_csv(csv_path, rows)
        with csv_path.open(encoding="utf-8", newline="") as f:
            read = list(csv.DictReader(f))
        self.assertEqual(list(read[0]), list(et.CSV_FIELDS))
        self.assertEqual(len(read), len(rows))
        self.assertTrue(all(r["verdict"] == "" and r["note"] == "" for r in read))
        self.assertTrue(all(len(r["nguon_text"]) <= et.SOURCE_TEXT_CHARS for r in read))

    def test_same_name_nodes_merge_inside_a_fragment(self):
        reply = copy.deepcopy(FIXTURE)
        reply["KhaiNiem"].append({"id": "k2", "ten": "thời gian tối đa được phép học", "bi_danh": ["thời gian học tối đa"],
                                  "nguon": [P + "d44.k2.b", P + "d44.k2"], "loai": "dai_luong"})
        reply["edges"].append({"type": "XET", "from": "c1", "to": "k2", "nguon": P + "d44.k2.a"})
        fragment = ex.build_fragment(reply, UNIT)
        merged = [n for n in fragment["nodes"] if n["id"] == "sem.KhaiNiem.thoi_gian_toi_da_duoc_phep_hoc"]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["props"]["nguon"], [P + "d44.k2.b", P + "d44.k2"])
        self.assertEqual(merged[0]["props"]["bi_danh"], ["thời gian học tối đa"])
        self.assertEqual(fragment["meta"]["id_map"]["k2"], fragment["meta"]["id_map"]["k1"])

    def test_invalid_unit_is_saved_with_its_errors(self):
        reply = copy.deepcopy(FIXTURE)
        reply["DieuKien"][2].update(max=1, max_incl=True)
        record = self.run_unit(FakeComplete(reply))
        self.assertEqual(record["status"], "invalid")
        self.assertIsNone(record["fragment"])
        saved = json.loads((self.out / "extract" / f"{UNIT}.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "invalid")
        self.assertTrue(any("min 2 > max 1" in e for e in saved["errors"]))
        self.assertEqual(saved["raw"]["parsed"], reply)
        rows, skipped = et.collect(et.load_records("extract", 0, self.out), et.source_texts())
        self.assertEqual((rows, skipped), ([], {UNIT: "invalid"}))

    def test_reply_without_json_is_invalid(self):
        record = self.run_unit(FakeComplete("Xin lỗi, tôi không chắc."))
        self.assertEqual(record["status"], "invalid")
        self.assertIn("no JSON object found in the reply", record["errors"])

    def test_truncated_reply_is_invalid(self):
        record = self.run_unit(FakeComplete(FIXTURE, finish_reason="max_tokens", truncated=True))
        self.assertEqual(record["status"], "invalid")
        self.assertTrue(any("max_tokens" in e for e in record["errors"]))

    def test_llm_error_is_recorded_not_skipped(self):
        record = self.run_unit(FakeComplete(RuntimeError("HTTP 529 overloaded")))
        self.assertEqual(record["status"], "error")
        self.assertIn("RuntimeError: HTTP 529 overloaded", record["errors"])
        self.assertTrue((self.out / "extract" / f"{UNIT}.json").exists())

    def test_rerun_does_not_call_the_llm_again(self):
        fake = FakeComplete(FIXTURE)
        first = self.run_unit(fake)
        second = self.run_unit(fake)
        self.assertEqual(len(fake.calls), 1)
        self.assertTrue(second["metadata"]["reused_record"])
        self.assertEqual(second["fragment"], first["fragment"])
        self.run_unit(fake, run=1)  # another run is another cache key
        self.assertEqual(len(fake.calls), 2)
        self.assertTrue((self.out / "extract" / f"{UNIT}.run1.json").exists())

    def test_error_record_is_retried(self):
        self.run_unit(FakeComplete(RuntimeError("timeout")))
        fake = FakeComplete(FIXTURE)
        self.assertEqual(self.run_unit(fake)["status"], "ok")
        self.assertEqual(len(fake.calls), 1)


HAVE_PROVIDER_PACKAGES = all(importlib.util.find_spec(m) for m in ("httpx", "dotenv"))


@unittest.skipUnless(HAVE_PROVIDER_PACKAGES, "httpx/python-dotenv not installed (requirements.txt)")
class RealCompleteCacheTests(unittest.TestCase):
    """The real complete() with a fake provider adapter: the second extraction is served from the disk cache."""

    def test_second_call_hits_the_complete_cache(self):
        from src.llm import core
        from src.llm.config import LLMConfig, RetryConfig, RoleConfig
        from src.llm.providers import RawResponse

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        role = RoleConfig(name="extract", provider="anthropic", model="claude-haiku-5-5", max_tokens=8192,
                          timeout_s=5, params={"effort": "medium"})
        config = LLMConfig(retry=RetryConfig(max_retries=0), roles={"extract": role})
        calls = []

        def adapter(cfg, system, messages, json_schema):
            calls.append(1)
            return RawResponse(json.dumps(FIXTURE, ensure_ascii=False), "claude-haiku-5-5", "end_turn", 9000, 800, True)

        out = Path(tmp.name) / "out"
        with mock.patch.object(core, "CACHE_DIR", Path(tmp.name) / "cache"), \
                mock.patch.object(core, "load_config", lambda: config), \
                mock.patch.object(core, "load_dotenv", lambda *a, **k: None), \
                mock.patch.dict(core.ADAPTERS, {"anthropic": adapter}):
            first = ex.extract_unit(UNIT, "extract", 0, context(), "claude-haiku-5-5", core.complete, out)
            (out / "extract" / f"{UNIT}.json").unlink()  # force a second complete() call
            second = ex.extract_unit(UNIT, "extract", 0, context(), "claude-haiku-5-5", core.complete, out)
        self.assertEqual(len(calls), 1)
        self.assertEqual((first["status"], second["status"]), ("ok", "ok"))
        self.assertTrue(second["metadata"]["from_cache"])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)

    def main(self, argv, fake=None, answer="n"):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = ex.main(argv, complete_fn=fake, out_dir=self.out, input_fn=lambda _: answer)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_articles_or_all_is_required(self):
        fake = FakeComplete(FIXTURE)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            ex.main([], complete_fn=fake, out_dir=self.out)
        self.assertEqual(cm.exception.code, 2)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            ex.main(["--articles", "44", "--all"], complete_fn=fake, out_dir=self.out)
        self.assertEqual(fake.calls, [])

    def test_out_of_scope_article_and_unknown_role(self):
        fake = FakeComplete(FIXTURE)
        self.assertEqual(self.main(["--articles", "33"], fake)[0], 2)
        self.assertEqual(self.main(["--articles", "44", "--role", "answer"], fake)[0], 2)
        self.assertEqual(fake.calls, [])

    def test_dry_run_calls_nothing_and_writes_nothing(self):
        fake = FakeComplete(FIXTURE)
        code, out, _ = self.main(["--articles", "26,28,43,44,46", "--dry-run"], fake)
        self.assertEqual(code, 0)
        self.assertEqual(fake.calls, [])
        self.assertEqual(list(self.out.iterdir()), [])
        self.assertIn("Số đơn vị: 18", out)
        self.assertIn("ƯỚC THÔ", out)
        self.assertIn("[có bảng: bỏ qua dòng bảng]", out)  # d43.k1
        code, out, _ = self.main(["--articles", "44", "--role", "extract_sonnet", "--dry-run"], fake)
        self.assertEqual(code, 0)
        self.assertIn("claude-sonnet-5-5", out)
        self.assertIn("giá 2.0/10.0", out)

    def test_run_asks_before_calling(self):
        fake = FakeComplete(FIXTURE)
        code, out, _ = self.main(["--articles", "44"], fake, answer="")
        self.assertEqual(code, 1)
        self.assertIn("Đã hủy", out)
        self.assertEqual(fake.calls, [])

    def test_run_with_yes_writes_one_record_per_unit(self):
        fake = FakeComplete(FIXTURE)
        code, out, _ = self.main(["--articles", "44", "--yes"], fake)
        files = sorted(p.name for p in (self.out / "extract").iterdir())
        self.assertEqual(files, [P + "d44.json", P + "d44.k1.json", P + "d44.k2.json"])
        self.assertEqual(len(fake.calls), 3)
        # the d44.k2 fixture cites d44.k2 ids, so it is invalid for the other two units: not every unit is ok
        self.assertEqual(code, 1)
        self.assertIn("ok 1, invalid 2", out)
        # ok and invalid records with a complete reply are reused (complete() would return the same cached reply)
        code, out, _ = self.main(["--articles", "44", "--dry-run"], fake)
        self.assertIn("Số đơn vị: 3 (cần gọi LLM: 0)", out)


class SampleTests(unittest.TestCase):
    def test_sample_is_reproducible(self):
        rows = [{"triplet_id": str(i)} for i in range(100)]
        a = et.sample_rows(rows, 30, seed=7)
        self.assertEqual(a, et.sample_rows(rows, 30, seed=7))
        self.assertEqual(len(a), 30)
        self.assertNotEqual(a, et.sample_rows(rows, 30, seed=8))
        self.assertEqual(et.sample_rows(rows, None, seed=1), rows)
        self.assertEqual(et.sample_rows(rows[:5], 30, seed=1), rows[:5])

    def test_exclude_nguon_loai(self):
        fragment = ex.build_fragment(copy.deepcopy(FIXTURE), UNIT)
        for n in fragment["nodes"]:
            if n["id"] == "sem.HeQua.bi_buoc_thoi_hoc":
                n["props"]["nguon_loai"] = "bang"
        record = {"unit": UNIT, "status": "ok", "fragment": fragment}
        texts = et.source_texts()
        self.assertEqual(len(et.fragment_triplets(record, texts)), len(FIXTURE["edges"]))
        kept = et.fragment_triplets(record, texts, {"bang"})
        self.assertEqual(len(kept), len(FIXTURE["edges"]) - 1)
        self.assertNotIn("DAN_DEN", {r["edge_type"] for r in kept})


if __name__ == "__main__":
    unittest.main()
