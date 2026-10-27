"""Load LLM role settings from configs/llm.toml."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "configs" / "llm.toml"

PROVIDERS = ("anthropic", "nvidia", "gemini")


@dataclass(frozen=True)
class RetryConfig:
    max_retries: int = 6
    base_delay_s: float = 2.0
    max_delay_s: float = 60.0
    retry_after_cap_s: float = 120.0  # a longer Retry-After means quota exhausted: fail instead of sleeping


@dataclass(frozen=True)
class RoleConfig:
    name: str
    provider: str
    model: str
    max_tokens: int
    timeout_s: float
    base_url: str | None = None
    params: dict = field(default_factory=dict)
    max_retries: int | None = None  # per-role override of [retry].max_retries


@dataclass(frozen=True)
class LLMConfig:
    retry: RetryConfig
    roles: dict[str, RoleConfig]

    def role(self, name: str) -> RoleConfig:
        if name not in self.roles:
            raise KeyError(f"unknown LLM role {name!r}; configured: {sorted(self.roles)}")
        return self.roles[name]


def load_config(path: Path = CONFIG_PATH) -> LLMConfig:
    """Parse the TOML file; read on every call so edits take effect without restarting."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    roles = {}
    for name, raw in data.get("roles", {}).items():
        if raw["provider"] not in PROVIDERS:
            raise ValueError(f"role {name!r}: unknown provider {raw['provider']!r}")
        roles[name] = RoleConfig(
            name=name,
            provider=raw["provider"],
            model=raw["model"],
            max_tokens=int(raw["max_tokens"]),
            timeout_s=float(raw["timeout_s"]),
            base_url=raw.get("base_url"),
            params=dict(raw.get("params", {})),
            max_retries=int(raw["max_retries"]) if "max_retries" in raw else None,
        )
    return LLMConfig(retry=RetryConfig(**data.get("retry", {})), roles=roles)
