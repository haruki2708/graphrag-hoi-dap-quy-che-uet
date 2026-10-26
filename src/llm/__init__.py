"""Single place to call LLMs: provider/model from configs/llm.toml, retries, disk cache, metadata.

Usage (from code under src/, or from an entry script that put the repo root on sys.path):
    from src.llm import complete
    result = complete("answer", system="...", messages=[{"role": "user", "content": "..."}])
"""

from .core import LLMError, LLMResult, complete

__all__ = ["LLMError", "LLMResult", "complete"]
