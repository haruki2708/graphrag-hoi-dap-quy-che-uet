"""Cypher shared by the loaders, the check scripts and the tests (schema sections 1 and 2, PLAN T3/T4 "Xong khi")."""

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

# --- Semantic layer (schema section 2; src/kg/load_semantic.py, scripts/check_semantic.py) ----------------------
# The semantic labels and edge types are passed as $labels / $types parameters; only {label} is put into the text
# (after a whitelist check in the loader).
SOURCE_EDGE = "NGUON"
SOURCE_LABELS = ("Dieu", "Khoan", "Diem")
FIND_UNITS = "UNWIND $ids AS id MATCH (u:{label} {{id: id}}) RETURN u.id AS id"
RESET_SEMANTIC = "MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) DETACH DELETE n RETURN count(*) AS n"
SEMANTIC_NODE_IDS = "MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) RETURN n.id AS id, labels(n) AS labels"
SEMANTIC_EDGES = ("MATCH (a)-[r]->(b) WHERE type(r) IN $types "
                  "RETURN a.id AS src, type(r) AS type, b.id AS dst, r.nguon AS nguon")
SEMANTIC_NODES_WITHOUT_NGUON = ("MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) AND NOT (n)-[:NGUON]->() "
                                "RETURN n.id AS id ORDER BY id")
# Nodes whose NGUON targets are not exactly their nguon list
SEMANTIC_NGUON_MISMATCH = ("MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) "
                           "OPTIONAL MATCH (n)-[:NGUON]->(u) WITH n, collect(u.id) AS got "
                           "WHERE size(got) <> size(n.nguon) OR any(x IN n.nguon WHERE NOT x IN got) "
                           "RETURN n.id AS id ORDER BY id")
NGUON_BAD_ENDS = ("MATCH (s)-[:NGUON]->(u) WHERE NOT (u:Dieu OR u:Khoan OR u:Diem) "
                  "OR NOT any(l IN labels(s) WHERE l IN $labels) RETURN s.id AS src, u.id AS dst")
SEMANTIC_EDGES_WITHOUT_NGUON = ("MATCH (a)-[r]->(b) WHERE type(r) IN $types AND (r.nguon IS NULL OR size(r.nguon) = 0) "
                                "RETURN a.id AS src, type(r) AS type, b.id AS dst")

# Test question 7 (schema 2.4), with parameters $tc (credits, already net of the TINH_VAO {co: false} courses),
# $tbc (cumulative GPA) and $ct (programme name). The query does not subtract anything itself.
# Step 1 places the student in a training year with the d17.k2 table (pattern A); step 2 finds the groups r2 whose
# year condition contains that year's thu_tu and whose GPA threshold holds (pattern B).
# Inheritance (schema 2.3 pattern B, decision of 23/11/2026): the consequence (DAN_DEN) and the delegation
# (GIAO_QUY_DINH) are taken from r2 itself or from an ancestor group reached through GOM, provided every group on
# that path except r2 is an OR group. An OR ancestor's consequence holds for each of its alternatives; an AND
# ancestor's consequence needs its other members too, so it is not inherited (the path is blocked).
_Q7_STEP1_MATCH = ('MATCH (r1:NhomDieuKien)-[:AP_DUNG_CHO]->(:DoiTuong {ten: $ct}),\n'
                   '      (r1)-[:GOM]->(c1:DieuKien)-[:XET]->(:KhaiNiem {ten: "số tín chỉ tích lũy"}),\n'
                   '      (r1)-[:DAN_DEN]->(nam:KhaiNiem {loai: "muc_phan_loai", thang: "nam_dao_tao"})')
_Q7_STEP1_WHERE = ('WHERE (c1.min IS NULL OR $tc > c1.min OR (c1.min_incl AND $tc = c1.min))\n'
                   '  AND (c1.max IS NULL OR $tc < c1.max OR (c1.max_incl AND $tc = c1.max))')
_Q7_STEP2_MATCH = ('MATCH (r2:NhomDieuKien)-[:GOM]->(cn:DieuKien {thang: "nam_dao_tao"}),\n'
                   '      (r2)-[:GOM]->(c2:DieuKien)-[:XET]->(:KhaiNiem {ten: "điểm trung bình chung tích lũy"})')
_Q7_STEP2_WHERE_YEAR = ('WHERE (cn.min IS NULL OR nam.thu_tu > cn.min OR (cn.min_incl AND nam.thu_tu = cn.min))\n'
                        '  AND (cn.max IS NULL OR nam.thu_tu < cn.max OR (cn.max_incl AND nam.thu_tu = cn.max))')
_Q7_STEP2_AND_GPA = '  AND c2.max IS NOT NULL AND ($tbc < c2.max OR (c2.max_incl AND $tbc = c2.max))'
# nodes(path)[..-1] = the groups on the path except r2 (empty when h = r2)
_Q7_CONSEQUENCE_MATCH = 'MATCH inh = (h:NhomDieuKien)-[:GOM*0..]->(r2), (h)-[:DAN_DEN]->(hq:HeQua)'
_Q7_OR_ONLY = 'WHERE all(x IN nodes(inh)[..-1] WHERE x.ket_hop = "OR")'
_Q7_TAIL = ('OPTIONAL MATCH dq = (p:NhomDieuKien)-[:GOM*0..]->(r2), (p)-[g:GIAO_QUY_DINH]->(ai:DoiTuong)\n'
            '  WHERE all(x IN nodes(dq)[..-1] WHERE x.ket_hop = "OR")\n'
            'MATCH (r1)-[:NGUON]->(s1), (r2)-[:NGUON]->(s2)')
QUERY7 = "\n".join([
    _Q7_STEP1_MATCH, _Q7_STEP1_WHERE, _Q7_STEP2_MATCH, _Q7_STEP2_WHERE_YEAR, _Q7_STEP2_AND_GPA,
    _Q7_CONSEQUENCE_MATCH, _Q7_OR_ONLY, _Q7_TAIL,
    "RETURN nam.ten AS nam, nam.thu_tu AS thu_tu, r2.id AS nhom, cn.min AS cn_min, cn.max AS cn_max, "
    "c2.max AS nguong, h.id AS nhom_he_qua, hq.ten AS he_qua, p.id AS nhom_giao_quyen, g.noi_dung AS giao_quyen, "
    "ai.ten AS giao_cho, s1.id AS nguon_1, s2.id AS nguon_2",
])


def _q7_count(*parts: str, sample: str) -> str:
    return "\n".join([*parts, f"RETURN count(*) AS n, collect(DISTINCT {sample})[..8] AS mau"])


_Q7_STEPS_1_2 = (_Q7_STEP1_MATCH, _Q7_STEP1_WHERE, _Q7_STEP2_MATCH, _Q7_STEP2_WHERE_YEAR, _Q7_STEP2_AND_GPA)

# QUERY7 cut into cumulative pieces, to find where it breaks when it returns nothing (name, query); each returns
# n (rows) and mau (a sample). Same pieces as QUERY7, so a step that drops to 0 points at the missing node, edge or
# WHERE condition. The step marked "chẩn đoán" drops the OR-only condition, to show consequences that an AND
# ancestor blocks; it is not part of QUERY7.
QUERY7_STEPS = (
    ("B1.0 DoiTuong {ten: $ct}", "MATCH (d:DoiTuong {ten: $ct}) RETURN count(*) AS n, collect(d.id) AS mau"),
    ("B1.0 KhaiNiem 'số tín chỉ tích lũy'",
     'MATCH (k:KhaiNiem {ten: "số tín chỉ tích lũy"}) RETURN count(*) AS n, collect(k.id) AS mau'),
    ("B1.1 r1 -AP_DUNG_CHO-> $ct",
     _q7_count("MATCH (r1:NhomDieuKien)-[:AP_DUNG_CHO]->(:DoiTuong {ten: $ct})", sample="r1.id")),
    ("B1.2 + r1 -GOM-> c1 -XET-> 'số tín chỉ tích lũy'",
     _q7_count(_Q7_STEP1_MATCH.rsplit(",\n", 1)[0], sample="r1.id")),
    ("B1.3 + r1 -DAN_DEN-> mức nam_dao_tao", _q7_count(_Q7_STEP1_MATCH, sample="nam.ten")),
    ("B1.4 + WHERE khoảng tín chỉ chứa $tc", _q7_count(_Q7_STEP1_MATCH, _Q7_STEP1_WHERE, sample="nam.ten")),
    ("B2.0 KhaiNiem 'điểm trung bình chung tích lũy'",
     'MATCH (k:KhaiNiem {ten: "điểm trung bình chung tích lũy"}) RETURN count(*) AS n, collect(k.id) AS mau'),
    ("B2.1 r2 -GOM-> cn {thang: nam_dao_tao}",
     _q7_count('MATCH (r2:NhomDieuKien)-[:GOM]->(cn:DieuKien {thang: "nam_dao_tao"})', sample="r2.id")),
    ("B2.2 + r2 -GOM-> c2 -XET-> 'điểm TBC tích lũy'", _q7_count(_Q7_STEP2_MATCH, sample="r2.id")),
    ("B2.3 bước 1 + bước 2, WHERE năm chứa nam.thu_tu",
     _q7_count(*_Q7_STEPS_1_2[:4], sample="r2.id")),
    ("B2.4 + WHERE ngưỡng TBC thỏa $tbc", _q7_count(*_Q7_STEPS_1_2, sample="r2.id")),
    ("B2.5 (chẩn đoán) + DAN_DEN từ r2 hoặc tổ tiên bất kỳ, chưa lọc OR",
     _q7_count(*_Q7_STEPS_1_2, _Q7_CONSEQUENCE_MATCH, sample="h.id")),
    ("B2.6 + chỉ kế thừa qua tổ tiên OR",
     _q7_count(*_Q7_STEPS_1_2, _Q7_CONSEQUENCE_MATCH, _Q7_OR_ONLY, sample="hq.ten")),
    ("B3 + giao quyền (OPTIONAL, qua tổ tiên OR) và NGUON",
     _q7_count(*_Q7_STEPS_1_2, _Q7_CONSEQUENCE_MATCH, _Q7_OR_ONLY, _Q7_TAIL, sample="s2.id")),
)
