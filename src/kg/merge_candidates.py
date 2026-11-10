"""Candidate entity merges for the project owner to review (T4 PR 5a; schema Q6 = A + B).

Run from the repo root (after src/kg/combine.py):
    python src/kg/merge_candidates.py [--threshold 0.85] [--min-contain-tokens 2]

Reads data/processed/semantic/combined_v1.json and writes data/processed/semantic/gop_ung_vien_v1.csv. NOTHING is merged
here: the owner fills quyet_dinh (gop / khong) in a copy of the rows he approves and saves the approved rows to
data/manual/gop_thuc_the_v1.csv (columns label, id_giu, id_bo, quyet_dinh, ghi_chu), which src/kg/combine.py applies.

Only the four mergeable labels are compared (KhaiNiem, DoiTuong, HoatDong, HeQua), and only inside one label. A pair is a
candidate when at least one of these holds:
- search_key: the search keys (semantic_ids.search_key) of the ten/bi_danh of the two nodes are equal, or the tokens of
  one key appear whole and in a row inside the other key (a key shorter than --min-contain-tokens tokens never counts
  as "contained", so "sinh viên" does not match every student node);
- cosine: the bge-m3 cosine between the two `ten` is at least --threshold (model and pinned revision from
  configs/retrieval.toml, the same as dense.py; vectors cached in data/cache/embeddings/);
- schema: pairs the schema says must be merged (docs/schema_v1.md 2.3 pattern D and 2.2): the variants of "cảnh báo học
  vụ / học tập / kết quả học tập"; a table node and an LLM node of "năm đào tạo", "số tín chỉ tích lũy", "điểm trung bình
  chung tích lũy"; the variants of one training programme (chuẩn / chất lượng cao / tài năng, never across programmes).
These rows come first. canh_bao marks pairs that differ in a word that changes the quantity or the programme: the pairs
that are easy to merge by mistake.
"""

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# Importing dense.py runs load_dotenv (HF_HOME) before any HuggingFace library is imported
from src.retrieval import dense  # noqa: E402
from src.kg.semantic_ids import MERGEABLE_LABELS, search_key  # noqa: E402

SEMANTIC_DIR = REPO_ROOT / "data" / "processed" / "semantic"
GRAPH_PATH = SEMANTIC_DIR / "combined_v1.json"
OUT_PATH = SEMANTIC_DIR / "gop_ung_vien_v1.csv"
NAME_CACHE_DIR = REPO_ROOT / "data" / "cache" / "embeddings"
DEFAULT_THRESHOLD = 0.85
DEFAULT_MIN_CONTAIN_TOKENS = 2
CSV_FIELDS = ("label", "id_a", "ten_a", "bi_danh_a", "so_nguon_a", "id_b", "ten_b", "bi_danh_b", "so_nguon_b",
              "cosine", "ly_do", "nguon_vi_du_a", "nguon_vi_du_b", "goi_y_id_giu", "canh_bao", "quyet_dinh", "ghi_chu")

# Words that change the quantity a name stands for (checked on the search keys, so "tích luỹ" = "tích lũy")
DISCRIMINATOR_PHRASES = ("học kỳ", "tích lũy", "các học phần", "tất cả", "tối thiểu", "tối đa")
NUMBER_WORDS = {"một", "nhất", "nhì", "hai", "ba", "tư", "bốn", "năm", "sáu", "bảy", "tám", "chín", "mười"}
# Rank words of the learning-level and graduation-rank scales ("học lực giỏi" / "học lực khá")
LEVEL_WORDS = {"giỏi", "khá", "yếu", "kém", "xuất"}
PROGRAMS = {"chuẩn": ("chuẩn",), "chất lượng cao": ("chất", "lượng", "cao"), "tài năng": ("tài", "năng")}
PROGRAM_MARKERS = (("chương", "trình"), ("ctđt",), ("ct", "đào", "tạo"))
SCHEMA_CONCEPTS = ("năm đào tạo", "số tín chỉ tích lũy", "điểm trung bình chung tích lũy")

_TOKEN = re.compile(r"\d+(?:[.,]\d+)?|\w+\+?")
_GRADE = re.compile(r"^[a-f]\+?$")


# --- Keys and tokens --------------------------------------------------------------------------------------------

def tokens(key: str) -> tuple[str, ...]:
    return tuple(_TOKEN.findall(key))


def has_run(haystack: tuple, needle: tuple) -> bool:
    """True when needle appears as a contiguous run of whole tokens inside haystack."""
    n = len(needle)
    return n > 0 and any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))


def node_keys(node: dict) -> list[str]:
    props = node["props"]
    keys = [search_key(props["ten"])] + [search_key(b) for b in props.get("bi_danh", [])]
    return [k for k in dict.fromkeys(keys) if k]


def keys_match(keys_a: list[str], keys_b: list[str], min_tokens: int) -> bool:
    """Equal keys, or one key whole inside another (the contained one needs at least min_tokens tokens)."""
    for a in keys_a:
        for b in keys_b:
            if a == b:
                return True
            ta, tb = tokens(a), tokens(b)
            short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
            if len(short) >= min_tokens and has_run(long_, short):
                return True
    return False


def programs_in(key: str) -> set[str]:
    toks = tokens(key)
    found = set()
    for name, run in PROGRAMS.items():
        for i in range(len(toks) - len(run) + 1):
            # "tiêu chuẩn", "đạt chuẩn", "điểm chuẩn" are not the standard programme
            if toks[i:i + len(run)] == run and not (name == "chuẩn" and i > 0 and toks[i - 1] in ("tiêu", "đạt", "điểm")):
                found.add(name)
    return found


def has_program_marker(key: str) -> bool:
    toks = tokens(key)
    return any(has_run(toks, m) for m in PROGRAM_MARKERS)


def warnings_for(ten_a: str, ten_b: str) -> list[str]:
    """Why two names that look alike should probably NOT be merged (differences of quantity, number or programme)."""
    ka, kb = search_key(ten_a), search_key(ten_b)
    ta, tb = tokens(ka), tokens(kb)
    out = []
    for phrase in DISCRIMINATOR_PHRASES:
        in_a, in_b = has_run(ta, tokens(phrase)), has_run(tb, tokens(phrase))
        if in_a != in_b:
            out.append(f"khác đại lượng: chỉ {'A' if in_a else 'B'} có \"{phrase}\"")
    only_a, only_b = Counter(ta) - Counter(tb), Counter(tb) - Counter(ta)
    differing = set(only_a) | set(only_b)

    def numeric(tok):
        return tok in NUMBER_WORDS or tok in LEVEL_WORDS or bool(re.match(r"^\d", tok)) or bool(_GRADE.match(tok))

    if any(numeric(t) for t in differing):
        out.append("khác số/mức: A có " + (" ".join(sorted(only_a)) or "-") + "; B có " + (" ".join(sorted(only_b)) or "-"))
    pa, pb = programs_in(ka), programs_in(kb)
    if pa != pb:
        out.append(f"khác chương trình: A {sorted(pa) or '-'}; B {sorted(pb) or '-'}")
    return out


# --- Embeddings -------------------------------------------------------------------------------------------------

def embed_names(names: list[str], device: str = "cuda") -> tuple["dense.np.ndarray", dict]:
    """bge-m3 vectors (L2-normalised) of the names, loaded like DenseIndex: pinned revision, fp16, max_seq_length from
    configs/retrieval.toml. Cached as one file keyed by model, revision, max_seq_length and the sha256 of the names."""
    cfg = dense.load_retrieval_config()
    key_src = json.dumps([cfg["model_id"], cfg["revision"], int(cfg["max_seq_length"]),
                          hashlib.sha256(json.dumps(names, ensure_ascii=False).encode("utf-8")).hexdigest()])
    key = hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:24]
    cache_file = NAME_CACHE_DIR / f"names_{key}.npy"
    meta = {"model_id": cfg["model_id"], "revision": cfg["revision"], "n_names": len(names), "cache_key": key}
    if cache_file.exists():
        return dense.np.load(cache_file), {**meta, "from_cache": True}
    import torch
    from sentence_transformers import SentenceTransformer

    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available; pass --device cpu to encode the (short) names on the CPU")
    model = SentenceTransformer(cfg["model_id"], revision=cfg["revision"], device=device)
    if device == "cuda":
        model.half()
    model.max_seq_length = int(cfg["max_seq_length"])
    vectors = model.encode(names, batch_size=32, normalize_embeddings=True, convert_to_numpy=True,
                           show_progress_bar=False).astype(dense.np.float32)
    NAME_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dense.np.save(cache_file, vectors)
    return vectors, {**meta, "from_cache": False}


# --- Candidates -------------------------------------------------------------------------------------------------

def is_table_node(node: dict) -> bool:
    return "bang" in _loai_list(node)


def _loai_list(node: dict) -> list:
    value = node["props"].get("nguon_loai", [])
    return value if isinstance(value, list) else [value]


def schema_pairs(by_label: dict[str, list[dict]], keys: dict[str, list[str]]) -> tuple[dict, dict]:
    """{frozenset({id_a, id_b}): note} for the pairs the schema says to merge, and a report of what was found."""
    pairs, report = {}, {}

    def add(a, b, note):
        if a["id"] != b["id"]:
            pairs.setdefault(frozenset((a["id"], b["id"])), note)

    # Pattern D: every variant of "cảnh báo học vụ / học tập / kết quả học tập", inside one label
    for label in MERGEABLE_LABELS:
        group = [n for n in by_label[label]
                 if any(has_run(tokens(k), ("cảnh", "báo")) and (has_run(tokens(k), ("học", "vụ")) or has_run(tokens(k), ("học", "tập")))
                        for k in keys[n["id"]])]
        report[f"cảnh báo ({label})"] = [n["id"] for n in group]
        for a, b in combinations(group, 2):
            add(a, b, "canh_bao_hoc_vu")
    # Quantities that a table node and an LLM node may both name
    for concept in SCHEMA_CONCEPTS:
        run = tokens(concept)
        group = [n for n in by_label["KhaiNiem"] if any(has_run(tokens(k), run) for k in keys[n["id"]])]
        table = [n for n in group if is_table_node(n)]
        llm = [n for n in group if not is_table_node(n)]
        report[f"{concept} (bảng/LLM)"] = {"bang": [n["id"] for n in table], "llm": [n["id"] for n in llm]}
        for a in table:
            for b in llm:
                add(a, b, concept)
    # Variants of one programme, never across programmes
    for name in PROGRAMS:
        group = [n for n in by_label["DoiTuong"]
                 if any(programs_in(k) == {name} and has_program_marker(k) for k in keys[n["id"]])]
        report[f"chương trình {name}"] = [n["id"] for n in group]
        for a, b in combinations(group, 2):
            add(a, b, f"chuong_trinh_{name}")
    return pairs, report


def suggest_keep(a: dict, b: dict) -> str:
    """Prefer a table node, then the node with more sources, then the shorter id, then the id itself."""
    def rank(n):
        return (not is_table_node(n), -len(n["props"]["nguon"]), len(n["id"]), n["id"])

    return min((a, b), key=rank)["id"]


def build_candidates(nodes: list[dict], cosine_of=None, threshold: float = DEFAULT_THRESHOLD,
                     min_contain_tokens: int = DEFAULT_MIN_CONTAIN_TOKENS) -> tuple[list[dict], dict]:
    """Candidate rows (dicts keyed by CSV_FIELDS) and a report. cosine_of(id_a, id_b) -> float|None; None disables the
    cosine reason (tests, or no model)."""
    by_label = {label: sorted((n for n in nodes if n["labels"][0] == label), key=lambda n: n["id"])
                for label in MERGEABLE_LABELS}
    by_id = {n["id"]: n for label in MERGEABLE_LABELS for n in by_label[label]}
    keys = {i: node_keys(n) for i, n in by_id.items()}
    schema, schema_report = schema_pairs(by_label, keys)

    reasons: dict[frozenset, list[str]] = {}
    for label in MERGEABLE_LABELS:
        for a, b in combinations(by_label[label], 2):
            found = []
            if keys_match(keys[a["id"]], keys[b["id"]], min_contain_tokens):
                found.append("search_key")
            cos = cosine_of(a["id"], b["id"]) if cosine_of else None
            if cos is not None and cos >= threshold:
                found.append("cosine")
            if found:
                reasons[frozenset((a["id"], b["id"]))] = found
    rows = []
    for pair in set(reasons) | set(schema):
        a, b = sorted((by_id[i] for i in pair), key=lambda n: n["id"])
        ten_a, ten_b = a["props"]["ten"], b["props"]["ten"]
        cos = cosine_of(a["id"], b["id"]) if cosine_of else None
        ly_do = "schema" if pair in schema else "+".join(reasons[pair])
        rows.append({
            "label": a["labels"][0],
            "id_a": a["id"], "ten_a": ten_a, "bi_danh_a": " | ".join(a["props"]["bi_danh"]),
            "so_nguon_a": len(a["props"]["nguon"]),
            "id_b": b["id"], "ten_b": ten_b, "bi_danh_b": " | ".join(b["props"]["bi_danh"]),
            "so_nguon_b": len(b["props"]["nguon"]),
            "cosine": "" if cos is None else f"{cos:.4f}",
            "ly_do": ly_do,
            "nguon_vi_du_a": ";".join(a["props"]["nguon"][:3]), "nguon_vi_du_b": ";".join(b["props"]["nguon"][:3]),
            "goi_y_id_giu": suggest_keep(a, b),
            "canh_bao": " | ".join(warnings_for(ten_a, ten_b)),
            "quyet_dinh": "", "ghi_chu": "",
        })
    rows.sort(key=lambda r: (r["ly_do"] != "schema", r["label"], -(float(r["cosine"]) if r["cosine"] else -1.0),
                             r["id_a"], r["id_b"]))
    return rows, {"schema": schema_report, "n_schema_pairs": len(schema)}


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # BOM so Excel shows the Vietnamese text
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def print_summary(rows: list[dict], report: dict, show: int = 15) -> None:
    print(f"Ứng viên: {len(rows)}")
    print("  theo nhãn:", dict(Counter(r["label"] for r in rows)))
    print("  theo lý do:", dict(Counter(r["ly_do"] for r in rows)))
    print(f"  có cảnh báo (dễ gộp nhầm): {sum(bool(r['canh_bao']) for r in rows)}")
    print("Các cặp schema đã tìm thấy:")
    for name, found in report["schema"].items():
        if isinstance(found, dict):
            print(f"  {name}: {len(found['bang'])} nút bảng, {len(found['llm'])} nút LLM {found}")
        else:
            print(f"  {name}: {len(found)} nút {found}")
    print(f"{show} dòng đầu:")
    for r in rows[:show]:
        warn = f" [!] {r['canh_bao']}" if r["canh_bao"] else ""
        print(f"  {r['ly_do']:<22} {r['label']:<9} cos {r['cosine'] or '-':>6}  {r['ten_a']!r} ~ {r['ten_b']!r}  "
              f"(giữ {r['goi_y_id_giu'].split('.')[-1]}){warn}")


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--graph", type=Path, default=GRAPH_PATH)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="cosine threshold (default 0.85)")
    parser.add_argument("--min-contain-tokens", type=int, default=DEFAULT_MIN_CONTAIN_TOKENS)
    parser.add_argument("--device", default="cuda", choices=("cuda", "cpu"))
    parser.add_argument("--no-embeddings", action="store_true", help="skip the cosine reason (no model needed)")
    args = parser.parse_args(argv)
    if not args.graph.exists():
        print(f"LỖI: chưa có {args.graph.as_posix()}; chạy python src/kg/combine.py trước.", file=sys.stderr)
        return 1
    nodes = json.loads(args.graph.read_text(encoding="utf-8"))["nodes"]
    mergeable = [n for n in nodes if n["labels"][0] in MERGEABLE_LABELS]
    cosine_of, emb_meta = None, None
    if not args.no_embeddings:
        names = sorted({n["props"]["ten"] for n in mergeable})
        vectors, emb_meta = embed_names(names, args.device)
        index = {name: i for i, name in enumerate(names)}
        by_id = {n["id"]: index[n["props"]["ten"]] for n in mergeable}

        def cosine_of(a, b):
            return float(vectors[by_id[a]] @ vectors[by_id[b]])

        print(f"Embedding: {emb_meta['n_names']} tên, {emb_meta['model_id']}@{emb_meta['revision'][:8]}, "
              f"{'từ cache' if emb_meta['from_cache'] else 'mới mã hóa'}")
    rows, report = build_candidates(nodes, cosine_of, args.threshold, args.min_contain_tokens)
    write_csv(args.out, rows)
    print_summary(rows, report)
    print(f"Ngưỡng cosine {args.threshold}, độ dài tối thiểu của khóa được chứa {args.min_contain_tokens} từ | "
          f"ghi {args.out.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
