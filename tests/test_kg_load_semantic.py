"""Tests for src/kg/load_semantic.py with a fake session (no Neo4j) and the shared semantic queries.
Run from the repo root:  python -m pytest tests/test_kg_load_semantic.py
"""

import copy
import json
import re
import unittest
from collections import Counter

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


if __name__ == "__main__":
    unittest.main()
