"""Cypher shared by the loader, the structure check script and the tests (schema section 1, PLAN T3 "Xong khi")."""

NODE_LABELS = ("VanBan", "Chuong", "Dieu", "Khoan", "Diem", "DonViNoiDung")
VECTOR_INDEX_NAME = "donvinoidung_embedding"
EMBEDDING_PROPERTY = "embedding"
EMBEDDING_DIM = 1024  # bge-m3 (decision #4)

CREATE_CONSTRAINT = "CREATE CONSTRAINT {name} IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE"
CREATE_VECTOR_INDEX = (
    f"CREATE VECTOR INDEX {VECTOR_INDEX_NAME} IF NOT EXISTS FOR (n:DonViNoiDung) ON (n.{EMBEDDING_PROPERTY}) "
    f"OPTIONS {{indexConfig: {{`vector.dimensions`: {EMBEDDING_DIM}, `vector.similarity_function`: 'cosine'}}}}"
)
AWAIT_VECTOR_INDEX = f"CALL db.awaitIndex('{VECTOR_INDEX_NAME}', 300)"

MERGE_NODES = "UNWIND $rows AS row MERGE (n:{label} {{id: row.id}}) SET n += row.props{extra_labels}"
MERGE_EDGES = ("UNWIND $rows AS row MATCH (a:{from_label} {{id: row.src}}) MATCH (b:{to_label} {{id: row.dst}}) "
               "MERGE (a)-[r:{type}]->(b) SET r += row.props")
SET_EMBEDDINGS = (f"UNWIND $rows AS row MATCH (n:DonViNoiDung {{id: row.id}}) "
                  f"CALL db.create.setNodeVectorProperty(n, '{EMBEDDING_PROPERTY}', row.embedding)")

# "Xong khi" checks
COUNT_DIEU = "MATCH (d:Dieu) RETURN count(d) AS n"
COUNT_NODES_BY_LABEL = "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS n ORDER BY label"
COUNT_EDGES_BY_TYPE = "MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS n ORDER BY type"
COUNT_DAN_CHIEU_INTERNAL = "MATCH ()-[r:DAN_CHIEU]->(t) WHERE t.id STARTS WITH 'qc3626.' RETURN count(r) AS n"
WHO_REFERENCES = "MATCH (s)-[:DAN_CHIEU]->(t) WHERE t.id = $target RETURN s.id AS source ORDER BY source"
COUNT_EMBEDDED = (f"MATCH (n:DonViNoiDung) WHERE n.{EMBEDDING_PROPERTY} IS NOT NULL RETURN count(n) AS n")
SHOW_VECTOR_INDEXES = ("SHOW INDEXES YIELD name, type, state, labelsOrTypes, properties WHERE type = 'VECTOR' "
                       "RETURN name, state, labelsOrTypes, properties")
