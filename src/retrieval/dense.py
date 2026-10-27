"""Dense retrieval over the 192 :DonViNoiDung content units (schema S2 = B).

    from src.retrieval.dense import DenseIndex
    index = DenseIndex.from_config()      # decision #4, from configs/retrieval.toml
    index.search("câu hỏi", k=10)         # -> [(unit_id, cosine score), ...]

Unit embeddings are cached in data/cache/embeddings/, keyed by model id, model revision (Hub commit),
with_heading, sha256 of the units file and max_seq_length, so a rerun does not re-encode.
"""

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
# HF_HOME comes from .env; it must be set before any HuggingFace library is imported
load_dotenv(REPO_ROOT / ".env")

import numpy as np  # noqa: E402

import tomllib  # noqa: E402

UNITS_PATH = REPO_ROOT / "data" / "processed" / "qc3626_content_units.jsonl"
RETRIEVAL_CONFIG = REPO_ROOT / "configs" / "retrieval.toml"
CACHE_DIR = REPO_ROOT / "data" / "cache" / "embeddings"
BATCH_SIZE = 8  # small batches for a 4 GB laptop GPU


@dataclass(frozen=True)
class ModelSpec:
    """How each model is used, taken from its Hub model card."""
    model_id: str
    max_seq_length: int
    query_prefix: str = ""   # neither card asks for a query instruction
    passage_prefix: str = ""


MODEL_SPECS = {
    # Card: "Maximum Sequence Length: 2048 tokens"; usage code sets model.max_seq_length = 2048 and
    # encodes raw sentences (no prefix).
    "AITeamVN/Vietnamese_Embedding": ModelSpec("AITeamVN/Vietnamese_Embedding", 2048),
    # Card: up to 8192 tokens; "the BGE-M3 model no longer requires adding instructions to the queries".
    "BAAI/bge-m3": ModelSpec("BAAI/bge-m3", 8192),
}


def load_retrieval_config(path: Path = RETRIEVAL_CONFIG) -> dict:
    """The [dense] table of configs/retrieval.toml (decision #4)."""
    with open(path, "rb") as f:
        return tomllib.load(f)["dense"]


def hf_hub_cache() -> str:
    from huggingface_hub import constants

    return str(constants.HF_HUB_CACHE)


def load_units(path: Path = UNITS_PATH) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def unit_text(unit: dict, with_heading: bool) -> str:
    return f"{unit['heading']}\n{unit['text']}" if with_heading else unit["text"]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def model_revision(model_id: str) -> str:
    """Commit hash of the model on the Hub (also checks that the model id exists)."""
    from huggingface_hub import HfApi

    return HfApi().model_info(model_id).sha


class DenseIndex:
    def __init__(self, model_id: str, with_heading: bool, units_path: Path = UNITS_PATH,
                 batch_size: int = BATCH_SIZE, revision: str | None = None, max_seq_length: int | None = None,
                 query_prefix: str | None = None):
        """revision/max_seq_length/query_prefix default to the Hub head and MODEL_SPECS (model card values)."""
        import torch
        from sentence_transformers import SentenceTransformer

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is not available; refusing to fall back to CPU")
        base = MODEL_SPECS[model_id]
        self.spec = ModelSpec(
            model_id,
            max_seq_length if max_seq_length is not None else base.max_seq_length,
            base.query_prefix if query_prefix is None else query_prefix,
            base.passage_prefix,
        )
        self.model_id = model_id
        self.with_heading = with_heading
        self.units = load_units(units_path)
        self.unit_ids = [u["id"] for u in self.units]
        self.revision = revision or model_revision(model_id)
        self.units_sha256 = file_sha256(units_path)

        # Pin the exact revision; fp16 on the GPU
        self.model = SentenceTransformer(model_id, revision=self.revision, device="cuda")
        self.model.half()
        self.model.max_seq_length = self.spec.max_seq_length

        texts = [self.spec.passage_prefix + unit_text(u, with_heading) for u in self.units]
        lengths = [len(ids) for ids in self.model.tokenizer(texts, add_special_tokens=True)["input_ids"]]
        self.max_unit_tokens = max(lengths)
        self.n_truncated = sum(n > self.spec.max_seq_length for n in lengths)

        key_src = json.dumps([model_id, self.revision, with_heading, self.units_sha256, self.spec.max_seq_length])
        self.cache_key = hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:24]
        cache_file = CACHE_DIR / f"{self.cache_key}.npy"
        if cache_file.exists():
            self.matrix = np.load(cache_file)
            self.from_cache = True
            self.encode_seconds = 0.0
        else:
            torch.cuda.synchronize()
            t0 = time.perf_counter()
            self.matrix = self.model.encode(texts, batch_size=batch_size, normalize_embeddings=True,
                                            convert_to_numpy=True, show_progress_bar=False).astype(np.float32)
            torch.cuda.synchronize()
            self.encode_seconds = time.perf_counter() - t0
            self.from_cache = False
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            np.save(cache_file, self.matrix)
            (CACHE_DIR / f"{self.cache_key}.json").write_text(json.dumps({
                "model_id": model_id, "revision": self.revision, "with_heading": with_heading,
                "units_sha256": self.units_sha256, "max_seq_length": self.spec.max_seq_length,
                "n_units": len(self.units), "shape": list(self.matrix.shape),
            }, indent=2), encoding="utf-8")
        if self.matrix.shape[0] != len(self.units):
            raise ValueError(f"cached matrix has {self.matrix.shape[0]} rows, expected {len(self.units)}")

    @classmethod
    def from_config(cls, path: Path = RETRIEVAL_CONFIG) -> "DenseIndex":
        """The index chosen in decision #4, entirely from configs/retrieval.toml."""
        cfg = load_retrieval_config(path)
        index = cls(cfg["model_id"], bool(cfg["with_heading"]), revision=cfg["revision"],
                    max_seq_length=int(cfg["max_seq_length"]), query_prefix=cfg.get("query_prefix", ""))
        index.top_k = int(cfg["top_k"])
        return index

    def encode_query(self, query: str) -> np.ndarray:
        vector = self.model.encode([self.spec.query_prefix + query], normalize_embeddings=True,
                                   convert_to_numpy=True, show_progress_bar=False)[0]
        return vector.astype(np.float32)

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        """Top-k units by cosine similarity (embeddings are L2-normalised, so a dot product)."""
        scores = self.matrix @ self.encode_query(query)
        order = np.argsort(-scores, kind="stable")[:k]
        return [(self.unit_ids[i], float(scores[i])) for i in order]

    def metadata(self) -> dict:
        return {
            "model_id": self.model_id, "revision": self.revision, "with_heading": self.with_heading,
            "query_prefix": self.spec.query_prefix, "passage_prefix": self.spec.passage_prefix,
            "max_seq_length": self.spec.max_seq_length, "max_unit_tokens": self.max_unit_tokens,
            "n_truncated_units": self.n_truncated, "units_sha256": self.units_sha256,
            "embedding_cache_key": self.cache_key, "from_cache": self.from_cache,
            "dtype": "float16 (model), float32 (stored)", "batch_size": BATCH_SIZE,
            "hf_hub_cache": hf_hub_cache(), "hf_home_env": os.getenv("HF_HOME"),
        }
