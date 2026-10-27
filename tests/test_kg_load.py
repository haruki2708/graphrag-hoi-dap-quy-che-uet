"""Tests for src/kg/load_neo4j.py with a recording fake session (no Neo4j, no embedding model).
Run from the repo root:  python -m unittest discover -s tests -t .
"""

import json
import re
import unittest
from collections import Counter

import numpy as np

from src.kg import load_neo4j as ld
from src.kg import queries as q
from src.kg import structure as st


class FakeSession:
    def __init__(self):
        self.calls = []

    def run(self, query, parameters=None):
        self.calls.append((query, parameters or {}))


def rows_sent(session, needle):
    return [row for query, params in session.calls if needle in query for row in params.get("rows", [])]


class LoadStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = st.build_graph()

    def setUp(self):
        self.session = FakeSession()
        self.summary = ld.load_structure(self.session, self.graph)

    def test_constraints_come_first_one_per_label(self):
        first = [query for query, _ in self.session.calls[:6]]
        self.assertTrue(all(query.startswith("CREATE CONSTRAINT") and "IF NOT EXISTS" in query and "REQUIRE n.id IS UNIQUE" in query
                            for query in first))
        self.assertEqual([query.split("FOR (n:")[1].split(")")[0] for query in first], list(q.NODE_LABELS))
        self.assertEqual(self.summary["constraints"], 6)

    def test_node_counts_per_label_and_content_label(self):
        merges = [(query, params["rows"]) for query, params in self.session.calls if query.startswith("UNWIND $rows AS row MERGE (n:")]
        per_label = Counter()
        for query, rows in merges:
            per_label[re.search(r"MERGE \(n:(\w+)", query).group(1)] += len(rows)
        self.assertEqual(per_label, {"VanBan": 7, "Chuong": 9, "Dieu": 51, "Khoan": 179, "Diem": 262})
        self.assertEqual(self.summary["nodes_sent"], 508)
        extra = {query: len(rows) for query, rows in merges if "SET n:DonViNoiDung" in query}
        self.assertEqual(sorted(extra.values()), [13, 179])  # 13 Dieu + 179 Khoan
        self.assertTrue(all("MERGE (n:Diem" not in query for query in extra))

    def test_edge_counts_and_endpoint_labels(self):
        merges = [(query, params["rows"]) for query, params in self.session.calls if "MERGE (a)-[r:" in query]
        by_type, thuoc_pairs = Counter(), set()
        for query, rows in merges:
            edge_type = re.search(r"-\[r:(\w+)\]", query).group(1)
            by_type[edge_type] += len(rows)
            if edge_type == "THUOC":
                thuoc_pairs.add(f"{re.search(r'MATCH \(a:(\w+)', query).group(1)}>{re.search(r'MATCH \(b:(\w+)', query).group(1)}")
        self.assertEqual(by_type, {"THUOC": 501, "DAN_CHIEU": 39})
        self.assertEqual(self.summary["edges_sent"], 540)
        self.assertTrue(all("MATCH (a:" in query and "MATCH (b:" in query for query, _ in merges))
        self.assertEqual(thuoc_pairs, {"Chuong>VanBan", "Dieu>Chuong", "Khoan>Dieu", "Diem>Khoan"})

    def test_batches_stay_within_the_limit(self):
        self.assertTrue(all(len(params.get("rows", [])) <= ld.BATCH_SIZE for _, params in self.session.calls))

    def test_doan_khong_so_is_sent_as_a_json_string(self):
        row = next(r for r in rows_sent(self.session, "MERGE (n:Khoan") if r["id"] == "qc3626.d36.k3")
        self.assertIsInstance(row["props"]["doan_khong_so"], str)
        self.assertEqual(json.loads(row["props"]["doan_khong_so"])[0]["sau"], "qc3626.d36.k3.d")
        self.assertTrue(all(isinstance(v, (str, int, float, bool, list)) for r in rows_sent(self.session, "MERGE (n:") for v in r["props"].values()))

    def test_a_label_not_in_the_whitelist_is_refused(self):
        bad = {"nodes": [{"id": "x", "labels": ["Evil) DETACH DELETE n //"], "props": {}}], "edges": []}
        with self.assertRaises(ValueError):
            ld.node_statements(bad)


class EmbeddingAndIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = st.build_graph()
        cls.ids = [n["id"] for n in cls.graph["nodes"] if "DonViNoiDung" in n["labels"]]

    def test_embeddings_batches_and_index_statement(self):
        session = FakeSession()
        matrix = np.random.default_rng(0).random((192, 1024), dtype=np.float32)
        self.assertEqual(ld.load_embeddings(session, self.graph, self.ids, matrix), 192)
        self.assertEqual(sum(len(p["rows"]) for _, p in session.calls), 192)
        self.assertTrue(all(len(r["embedding"]) == 1024 for _, p in session.calls for r in p["rows"]))
        self.assertTrue(all("MATCH (n:DonViNoiDung" in query and "db.create.setNodeVectorProperty" in query for query, _ in session.calls))
        ld.create_vector_index(session)
        create = session.calls[-2][0]
        self.assertEqual(create, "CREATE VECTOR INDEX donvinoidung_embedding IF NOT EXISTS FOR (n:DonViNoiDung) ON (n.embedding) "
                                 "OPTIONS {indexConfig: {`vector.dimensions`: 1024, `vector.similarity_function`: 'cosine'}}")
        self.assertIn("db.awaitIndex('donvinoidung_embedding'", session.calls[-1][0])

    def test_wrong_shape_or_ids_are_refused(self):
        session = FakeSession()
        with self.assertRaises(ValueError):
            ld.load_embeddings(session, self.graph, self.ids, np.zeros((192, 768), dtype=np.float32))
        with self.assertRaises(ValueError):
            ld.load_embeddings(session, self.graph, self.ids[:-1], np.zeros((191, 1024), dtype=np.float32))
        self.assertEqual(session.calls, [])


class SharedQueriesTests(unittest.TestCase):
    def test_check_queries_are_read_only(self):
        for query in (q.COUNT_DIEU, q.COUNT_NODES_BY_LABEL, q.COUNT_EDGES_BY_TYPE, q.COUNT_DAN_CHIEU_INTERNAL, q.WHO_REFERENCES,
                      q.COUNT_EMBEDDED, q.SHOW_VECTOR_INDEXES):
            self.assertFalse(any(word in query.upper() for word in ("CREATE", "MERGE", "DELETE", "SET ")), query)
        self.assertIn("$target", q.WHO_REFERENCES)


if __name__ == "__main__":
    unittest.main()
