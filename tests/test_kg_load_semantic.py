"""Tests for src/kg/load_semantic.py with a fake session (no Neo4j), the shared semantic queries and
scripts/check_query7.py. Run from the repo root:  python -m unittest discover -s tests -t .

Query7InheritanceNeo4jTests runs QUERY7 on the local Neo4j and is skipped unless RUN_NEO4J_TESTS=1: it creates a
small graph inside a transaction that is rolled back, so nothing is written.
"""

import copy
import importlib.util
import json
import os
import re
import unittest
from collections import Counter
from pathlib import Path

from src.kg import load_semantic as ls
from src.kg import queries as q
from src.kg import semantic_schema as ss

WRITE_WORDS = ("CREATE", "MERGE", "DELETE", "SET ", "REMOVE")
STRUCTURE_LABELS = ("VanBan", "Chuong", "Dieu", "Khoan", "Diem", "DonViNoiDung")


class FakeResult(list):
    def single(self):
        return self[0] if self else None


class FakeSession:
    """Records every call; answers the read queries the loader asks before writing."""

    def __init__(self, existing: dict[str, set], n_dieu: int = 51, n_semantic: int = 0):
        self.calls, self.existing, self.n_dieu, self.n_semantic = [], existing, n_dieu, n_semantic

    def run(self, query, parameters=None):
        parameters = parameters or {}
        self.calls.append((query, parameters))
        if query == q.COUNT_DIEU:
            return FakeResult([{"n": self.n_dieu}])
        if query.startswith("UNWIND $ids AS id MATCH (u:"):
            label = re.search(r"MATCH \(u:(\w+)", query).group(1)
            return FakeResult({"id": i} for i in parameters["ids"] if i in self.existing.get(label, set()))
        if query == q.RESET_SEMANTIC:
            return FakeResult([{"n": self.n_semantic}])
        return FakeResult()

    def writes(self):
        return [(query, params) for query, params in self.calls if any(w in query.upper() for w in WRITE_WORDS)]


def is_write(query: str) -> bool:
    return any(w in query.upper() for w in WRITE_WORDS)


def all_targets_exist(graph: dict) -> dict[str, set]:
    return {label: set(ids) for label, ids in ls.nguon_targets(graph).items()}


def tiny_graph() -> dict:
    """Two nodes, one edge; a nested property and a None-valued edge property."""
    return {"nodes": [
        {"id": "sem.KhaiNiem.a", "labels": ["KhaiNiem"],
         "props": {"id": "sem.KhaiNiem.a", "ten": "a", "bi_danh": ["x", "y"], "nguon_loai": ["trich_xuat"],
                   "nguon": ["qc3626.d17.k2", "qc3626.d44.k1.a"], "loai": "dai_luong",
                   "nested": [{"k": 1}]}},
        {"id": "sem.KhaiNiem.b", "labels": ["KhaiNiem"],
         "props": {"id": "sem.KhaiNiem.b", "ten": "b", "bi_danh": [], "nguon_loai": ["bang"],
                   "nguon": ["qc3626.d17"], "loai": "dai_luong"}},
    ], "edges": [
        {"type": "TINH_VAO", "from": "sem.KhaiNiem.a", "to": "sem.KhaiNiem.b",
         "props": {"co": False, "boi_canh": None, "nguon": ["qc3626.d17.k2"]}},
    ]}


class StatementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = json.loads(ls.GRAPH_PATH.read_text(encoding="utf-8"))
        cls.session = FakeSession(all_targets_exist(cls.graph))
        cls.summary = ls.load_semantic(cls.session, cls.graph)
        cls.expected = ls.expected_counts(cls.graph)

    def test_reads_first_then_constraints_then_data(self):
        kinds = []
        for query, _ in self.session.calls:
            kind = ("read" if not is_write(query) else "constraint" if query.startswith("CREATE CONSTRAINT")
                    else "nguon" if "[r:NGUON]" in query else "edge" if "MERGE (a)-[r:" in query else "node")
            if not kinds or kinds[-1] != kind:
                kinds.append(kind)
        self.assertEqual(kinds, ["read", "constraint", "node", "edge", "nguon"])

    def test_one_constraint_per_semantic_label(self):
        constraints = [query for query, _ in self.session.calls if query.startswith("CREATE CONSTRAINT")]
        self.assertEqual([re.search(r"FOR \(n:(\w+)\)", c).group(1) for c in constraints], list(ss.NODE_LABELS))
        self.assertTrue(all("IF NOT EXISTS" in c and "REQUIRE n.id IS UNIQUE" in c for c in constraints))
        self.assertEqual(self.summary["constraints"], 6)

    def test_node_rows_per_label_match_the_file(self):
        per_label = Counter()
        for query, params in self.session.calls:
            if query.startswith("UNWIND $rows AS row MERGE (n:"):
                per_label[re.search(r"MERGE \(n:(\w+) \{id: row.id\}\)", query).group(1)] += len(params["rows"])
                self.assertIn("SET n += row.props", query)
        self.assertEqual(dict(per_label), self.expected["nodes"])
        self.assertEqual(sum(per_label.values()), 1027)

    def test_semantic_edges_merge_on_from_type_to_and_set_props(self):
        by_type = Counter()
        for query, params in self.session.calls:
            m = re.search(r"MERGE \(a\)-\[r:(\w+)\]->\(b\) SET r \+= row.props$", query)
            if m and m.group(1) != "NGUON":
                by_type[m.group(1)] += len(params["rows"])
                self.assertTrue(all(row["props"]["nguon"] for row in params["rows"]))
        self.assertEqual(sum(by_type.values()), 1168)
        self.assertEqual(dict(by_type), {t: n for t, n in self.expected["edges"].items() if t != "NGUON"})

    def test_nguon_edges_one_per_node_and_unit_to_a_structure_label(self):
        pairs = set()
        n = 0
        for query, params in self.session.calls:
            if "[r:NGUON]" in query:
                a = re.search(r"MATCH \(a:(\w+)", query).group(1)
                b = re.search(r"MATCH \(b:(\w+)", query).group(1)
                self.assertIn(a, ss.NODE_LABELS)
                self.assertIn(b, q.SOURCE_LABELS)
                self.assertTrue(all(ls.unit_label(row["dst"]) == b for row in params["rows"]))
                pairs |= {(row["src"], row["dst"]) for row in params["rows"]}
                n += len(params["rows"])
        self.assertEqual(n, 1397)
        self.assertEqual(len(pairs), n)
        self.assertEqual(pairs, {(node["id"], u) for node in self.graph["nodes"] for u in node["props"]["nguon"]})

    def test_batches_stay_within_the_limit(self):
        self.assertTrue(all(len(params.get("rows", [])) <= ls.BATCH_SIZE for _, params in self.session.calls))

    def test_no_delete_without_the_reset_flag(self):
        self.assertFalse(any("DELETE" in query.upper() for query, _ in self.session.calls))
        self.assertIsNone(self.summary["deleted"])

    def test_same_statements_on_a_second_run(self):
        again = FakeSession(all_targets_exist(self.graph))
        ls.load_semantic(again, self.graph)
        self.assertEqual(again.calls, self.session.calls)


class PropertyTests(unittest.TestCase):
    def test_lists_stay_lists_nested_values_become_json(self):
        [(query, rows)] = ls.node_statements(tiny_graph())
        props = {r["id"]: r["props"] for r in rows}["sem.KhaiNiem.a"]
        self.assertEqual(props["bi_danh"], ["x", "y"])
        self.assertEqual(props["nguon"], ["qc3626.d17.k2", "qc3626.d44.k1.a"])
        self.assertIsInstance(props["nested"], str)
        self.assertEqual(json.loads(props["nested"]), [{"k": 1}])

    def test_unit_label_from_the_id_shape(self):
        self.assertEqual(ls.unit_label("qc3626.d17"), "Dieu")
        self.assertEqual(ls.unit_label("qc3626.d17.k2"), "Khoan")
        self.assertEqual(ls.unit_label("qc3626.d44.k1.a"), "Diem")
        self.assertEqual(ls.unit_label("qc3626.d50.k3.đ"), "Diem")
        self.assertIsNone(ls.unit_label("qc3626.c3"))
        self.assertIsNone(ls.unit_label("qc3626"))

    def test_a_name_outside_the_whitelists_is_refused(self):
        bad = tiny_graph()
        bad["nodes"][0]["labels"] = ["Evil) DETACH DELETE n //"]
        with self.assertRaises(ValueError):
            ls.node_statements(bad)
        self.assertEqual(ls.check_name("GOM"), "GOM")
        self.assertEqual(ls.check_name("Khoan"), "Khoan")


class RefusalTests(unittest.TestCase):
    def assert_refused_without_writes(self, session, graph, needle):
        with self.assertRaises(ls.LoadError) as ctx:
            ls.load_semantic(session, graph, reset=True)
        self.assertIn(needle, str(ctx.exception))
        self.assertEqual(session.writes(), [])

    def test_missing_nguon_target_writes_nothing(self):
        graph = tiny_graph()
        existing = all_targets_exist(graph)
        existing["Diem"].discard("qc3626.d44.k1.a")
        self.assert_refused_without_writes(FakeSession(existing), graph, "qc3626.d44.k1.a")

    def test_structure_not_loaded_writes_nothing(self):
        graph = tiny_graph()
        self.assert_refused_without_writes(FakeSession(all_targets_exist(graph), n_dieu=0), graph, "load_neo4j.py")

    def test_duplicate_triple_is_refused(self):
        graph = tiny_graph()
        dup = copy.deepcopy(graph["edges"][0])
        dup["props"]["co"] = True
        graph["edges"].append(dup)
        self.assertEqual(ls.duplicate_triples(graph), [("sem.KhaiNiem.a", "TINH_VAO", "sem.KhaiNiem.b")])
        session = FakeSession(all_targets_exist(graph))
        self.assert_refused_without_writes(session, graph, "khóa cạnh")
        self.assertEqual(session.calls, [])  # stopped before reading Neo4j

    def test_node_or_edge_without_nguon_is_refused(self):
        for drop in ("node", "edge"):
            graph = tiny_graph()
            (graph["nodes"][1] if drop == "node" else graph["edges"][0])["props"]["nguon"] = []
            self.assert_refused_without_writes(FakeSession(all_targets_exist(graph)), graph, "no nguon")

    def test_the_real_file_has_no_duplicate_triple_and_is_valid(self):
        graph = json.loads(ls.GRAPH_PATH.read_text(encoding="utf-8"))
        self.assertEqual(ls.duplicate_triples(graph), [])
        self.assertEqual(ls.validate_file(graph), [])


class ResetTests(unittest.TestCase):
    def test_reset_deletes_only_semantic_labels_before_any_other_write(self):
        graph = tiny_graph()
        session = FakeSession(all_targets_exist(graph), n_semantic=7)
        summary = ls.load_semantic(session, graph, reset=True)
        self.assertEqual(summary["deleted"], 7)
        deletes = [(query, params) for query, params in session.calls if "DELETE" in query.upper()]
        self.assertEqual(deletes, [(q.RESET_SEMANTIC, {"labels": list(ss.NODE_LABELS)})])
        query, params = deletes[0]
        for label in STRUCTURE_LABELS:
            self.assertNotIn(label, query)
            self.assertNotIn(label, params["labels"])
        self.assertEqual(session.writes()[0][0], q.RESET_SEMANTIC)

    def test_reset_query_is_parameterised_on_labels(self):
        self.assertIn("$labels", q.RESET_SEMANTIC)
        self.assertIn("DETACH DELETE n", q.RESET_SEMANTIC)
        self.assertNotIn(":", q.RESET_SEMANTIC.split("DETACH")[0].replace("labels(n)", ""))


class CountTests(unittest.TestCase):
    def test_expected_counts_and_comparison(self):
        graph = tiny_graph()
        expected = ls.expected_counts(graph)
        self.assertEqual(expected, {"nodes": {"KhaiNiem": 2}, "edges": {"TINH_VAO": 1, "NGUON": 3}})
        rows = ls.compare_counts(expected, {"nodes": {"KhaiNiem": 2}, "edges": {"TINH_VAO": 1, "NGUON": 2, "GOM": 1}})
        self.assertEqual(rows, [("nodes", "KhaiNiem", 2, 2), ("edges", "GOM", 0, 1), ("edges", "TINH_VAO", 1, 1),
                                ("edges", "NGUON", 3, 2)])


class SemanticQueriesTests(unittest.TestCase):
    def test_check_and_query7_queries_are_read_only(self):
        queries = [q.FIND_UNITS, q.SEMANTIC_NODE_IDS, q.SEMANTIC_EDGES, q.SEMANTIC_NODES_WITHOUT_NGUON,
                   q.SEMANTIC_NGUON_MISMATCH, q.NGUON_BAD_ENDS, q.SEMANTIC_EDGES_WITHOUT_NGUON, q.QUERY7,
                   *(query for _, query in q.QUERY7_STEPS)]
        for query in queries:
            self.assertFalse(is_write(query), query)

    def test_query7_takes_parameters_not_literals(self):
        for param in ("$tc", "$tbc", "$ct"):
            self.assertIn(param, q.QUERY7)
        self.assertNotIn("WITH 50", q.QUERY7)
        self.assertNotIn("CTĐT chất lượng cao", q.QUERY7)
        self.assertIn('(:KhaiNiem {ten: "số tín chỉ tích lũy"})', q.QUERY7)
        self.assertIn('(:KhaiNiem {ten: "điểm trung bình chung tích lũy"})', q.QUERY7)

    def test_query7_steps_are_cumulative_pieces_of_query7(self):
        names = [name for name, _ in q.QUERY7_STEPS]
        self.assertEqual(len(names), len(set(names)))
        last = q.QUERY7_STEPS[-1][1]
        self.assertTrue(last.startswith(q.QUERY7.rsplit("\nRETURN", 1)[0]))
        self.assertTrue(all("RETURN count(*) AS n" in query for _, query in q.QUERY7_STEPS))

    def test_query7_inherits_consequence_and_delegation_through_or_ancestors_only(self):
        self.assertNotIn("(r2)-[:DAN_DEN]", q.QUERY7)
        self.assertIn("MATCH inh = (h:NhomDieuKien)-[:GOM*0..]->(r2), (h)-[:DAN_DEN]->(hq:HeQua)", q.QUERY7)
        self.assertIn('WHERE all(x IN nodes(inh)[..-1] WHERE x.ket_hop = "OR")', q.QUERY7)
        self.assertIn("OPTIONAL MATCH dq = (p:NhomDieuKien)-[:GOM*0..]->(r2), (p)-[g:GIAO_QUY_DINH]->(ai:DoiTuong)",
                      q.QUERY7)
        self.assertIn('WHERE all(x IN nodes(dq)[..-1] WHERE x.ket_hop = "OR")', q.QUERY7)


def load_check_query7():
    path = Path(__file__).resolve().parents[1] / "scripts" / "check_query7.py"
    spec = importlib.util.spec_from_file_location("check_query7", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CheckQuery7JudgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cq = load_check_query7()
        cls.case = cls.cq.CASES[0]
        cls.good = {"nam": "năm thứ hai", "thu_tu": 2, "nhom": "g6", "cn_min": 2, "cn_max": 2, "nguong": 1.4,
                    "nhom_he_qua": "g1", "he_qua": "bị cảnh báo học vụ", "nhom_giao_quyen": "g1",
                    "giao_quyen": "chọn 1–2 trong 3 điều kiện", "giao_cho": "thủ trưởng đơn vị đào tạo",
                    "nguon_1": "qc3626.d17.k2", "nguon_2": "qc3626.d44.k1.a"}

    def test_one_expected_row_passes(self):
        self.assertEqual(self.cq.judge(self.case, [self.good]), (True, []))

    def test_more_than_one_row_is_reported_not_filtered(self):
        ok, reasons = self.cq.judge(self.case, [self.good, dict(self.good)])
        self.assertFalse(ok)
        self.assertIn("2 dòng", reasons[0])

    def test_missing_delegation_or_wrong_threshold_fails(self):
        self.assertFalse(self.cq.judge(self.case, [{**self.good, "giao_quyen": None, "giao_cho": None}])[0])
        self.assertFalse(self.cq.judge(self.case, [{**self.good, "nguong": 1.6}])[0])
        self.assertEqual(self.cq.judge(self.case, []), (False, ["truy vấn trả về 0 dòng"]))


# Synthetic groups for QUERY7, all with year condition [2, 2] and a GPA threshold < 10 (real thresholds are <= 1.8,
# so with tbc = 9.5 only these groups can match):
#   leaf_direct: its own DAN_DEN                                  -> row
#   leaf_or:     OR top (DAN_DEN, GIAO_QUY_DINH) -> OR mid -> leaf -> row, inherited through two OR groups
#   leaf_and:    AND top (DAN_DEN, GIAO_QUY_DINH) -> leaf          -> no row, blocked by the AND parent
#   leaf_mix:    OR top (DAN_DEN) -> AND mid -> leaf                -> no row, blocked by the AND group on the path
Q7_LEAVES = ("leaf_direct", "leaf_or", "leaf_and", "leaf_mix")
Q7_FIXTURE = """
CREATE (ct:DoiTuong {id: 'test.q7.ct', ten: $ct, loai: 'chuong_trinh'})
CREATE (tc:KhaiNiem {id: 'test.q7.tc', ten: 'số tín chỉ tích lũy', loai: 'dai_luong'})
CREATE (tbc:KhaiNiem {id: 'test.q7.tbc', ten: 'điểm trung bình chung tích lũy', loai: 'dai_luong'})
CREATE (nam:KhaiNiem {id: 'test.q7.nam', ten: 'năm thử', loai: 'muc_phan_loai', thang: 'nam_dao_tao', thu_tu: 2})
CREATE (unit:Khoan {id: 'test.q7.unit'})
CREATE (ai:DoiTuong {id: 'test.q7.ai', ten: 'người được giao', loai: 'chuc_danh'})
CREATE (r1:NhomDieuKien {id: 'test.q7.r1', ket_hop: 'AND'})-[:AP_DUNG_CHO]->(ct)
CREATE (r1)-[:GOM]->(:DieuKien {id: 'test.q7.r1.c', min: 0, max: 1000, min_incl: true, max_incl: true,
                               thang: 'tin_chi'})-[:XET]->(tc)
CREATE (r1)-[:DAN_DEN]->(nam), (r1)-[:NGUON]->(unit)
""" + "".join(f"""
CREATE ({name}:NhomDieuKien {{id: 'test.q7.{name}', ket_hop: 'AND'}})-[:NGUON]->(unit)
CREATE ({name})-[:GOM]->(:DieuKien {{id: 'test.q7.{name}.nam', min: 2, max: 2, min_incl: true, max_incl: true,
                                     thang: 'nam_dao_tao'}})
CREATE ({name})-[:GOM]->(:DieuKien {{id: 'test.q7.{name}.tbc', max: 10.0, max_incl: false,
                                     thang: 'thang_4'}})-[:XET]->(tbc)""" for name in Q7_LEAVES) + """
CREATE (leaf_direct)-[:DAN_DEN]->(:HeQua {id: 'test.q7.hq_direct', ten: 'hq_direct', loai: 'trang_thai'})
CREATE (or_top:NhomDieuKien {id: 'test.q7.or_top', ket_hop: 'OR'})-[:GOM]->
       (:NhomDieuKien {id: 'test.q7.or_mid', ket_hop: 'OR'})-[:GOM]->(leaf_or)
CREATE (or_top)-[:DAN_DEN]->(:HeQua {id: 'test.q7.hq_or', ten: 'hq_or', loai: 'trang_thai'})
CREATE (or_top)-[:GIAO_QUY_DINH {noi_dung: 'gq_or'}]->(ai)
CREATE (and_top:NhomDieuKien {id: 'test.q7.and_top', ket_hop: 'AND'})-[:GOM]->(leaf_and)
CREATE (and_top)-[:DAN_DEN]->(:HeQua {id: 'test.q7.hq_and', ten: 'hq_and', loai: 'trang_thai'})
CREATE (and_top)-[:GIAO_QUY_DINH {noi_dung: 'gq_and'}]->(ai)
CREATE (mix_top:NhomDieuKien {id: 'test.q7.mix_top', ket_hop: 'OR'})-[:GOM]->
       (:NhomDieuKien {id: 'test.q7.mix_mid', ket_hop: 'AND'})-[:GOM]->(leaf_mix)
CREATE (mix_top)-[:DAN_DEN]->(:HeQua {id: 'test.q7.hq_mix', ten: 'hq_mix', loai: 'trang_thai'})
WITH 1 AS done
MATCH (leaf:NhomDieuKien) WHERE leaf.id IN [x IN $leaves | 'test.q7.' + x]
RETURN count(leaf) AS n
"""
Q7_TEST_PROGRAMME = "__q7 test programme__"


@unittest.skipUnless(os.getenv("RUN_NEO4J_TESTS") == "1",
                     "set RUN_NEO4J_TESTS=1 to run QUERY7 on the local Neo4j (inside a rolled-back transaction)")
class Query7InheritanceNeo4jTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from src.kg.load_neo4j import neo4j_driver

        cls.driver = neo4j_driver()
        cls.session = cls.driver.session()
        cls.tx = cls.session.begin_transaction()
        try:
            cls.created = cls.tx.run(Q7_FIXTURE, {"ct": Q7_TEST_PROGRAMME, "leaves": list(Q7_LEAVES)}).single()["n"]
            cls.rows = [r.data() for r in cls.tx.run(q.QUERY7, {"tc": 50, "tbc": 9.5, "ct": Q7_TEST_PROGRAMME})]
        except Exception:
            cls.tearDownClass()  # tearDownClass is not called when setUpClass fails
            raise

    @classmethod
    def tearDownClass(cls):
        cls.tx.rollback()
        cls.session.close()
        cls.driver.close()

    def test_fixture_built_four_leaves(self):
        self.assertEqual(self.created, 4)

    def test_only_the_synthetic_groups_match(self):
        self.assertTrue(all(r["nhom"].startswith("test.q7.") for r in self.rows), self.rows)

    def test_direct_consequence_and_or_inheritance(self):
        got = sorted((r["nhom"], r["nhom_he_qua"], r["he_qua"], r["giao_quyen"]) for r in self.rows)
        self.assertEqual(got, [("test.q7.leaf_direct", "test.q7.leaf_direct", "hq_direct", None),
                               ("test.q7.leaf_or", "test.q7.or_top", "hq_or", "gq_or")])

    def test_and_ancestor_blocks_inheritance(self):
        blocked = {"test.q7.leaf_and", "test.q7.leaf_mix"}
        self.assertFalse(blocked & {r["nhom"] for r in self.rows})
        self.assertFalse({"hq_and", "hq_mix", "gq_and"} & {v for r in self.rows for v in r.values()})


if __name__ == "__main__":
    unittest.main()
