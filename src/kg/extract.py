"""Semantic-layer extraction (T4): one LLM call per content unit, validated, turned into a graph fragment.

Run from the repo root (exactly one of --articles / --all is required):
    python src/kg/extract.py --articles 26,28,43,44,46 --dry-run
    python src/kg/extract.py --articles 26,28 [--role extract|extract_sonnet] [--run 0] [--yes]
    python src/kg/extract.py --all --dry-run

Scope (schema Q9 = B): the content units (:DonViNoiDung, data/processed/qc3626_content_units.jsonl) of 34 articles.
For each unit:
  (a) build_unit_text(): article heading + the unit's text, each clause/point/paragraph prefixed with its [id];
  (b) complete(role, system, messages, json_schema, run) - src/llm caches successful replies on disk;
  (c) semantic_schema.validate(); a unit that fails is saved with status "invalid" and its errors, never dropped;
  (d) build_fragment(): local ids -> global ids (semantic_ids), nguon_loai = "trich_xuat", nodes keep their nguon list;
  (e) write data/processed/semantic/<role>/<unit_id>.json (run 0; run N > 0 adds ".runN" before ".json").
A record already on disk for the same role, run, model, prompt, schema and input is reused without calling the LLM
(its stored reply is validated again with the current code).
The six clauses holding a classification table (Q10) are still sent, with an instruction to skip the table rows.
"""

import argparse
import hashlib
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.kg import semantic_ids as sid  # noqa: E402
from src.kg import semantic_schema as ss  # noqa: E402
from src.kg.structure import load_content_units  # noqa: E402

DOC_ID = "qc3626"
PROCESSED_JSON = REPO_ROOT / "data" / "processed" / "qc3626.json"
CONTENT_UNITS = REPO_ROOT / "data" / "processed" / "qc3626_content_units.jsonl"
OUT_ROOT = REPO_ROOT / "data" / "processed" / "semantic"
PROMPT_PATH = REPO_ROOT / "src" / "kg" / "prompts" / "extract_v1.md"
EXAMPLES_PATH = REPO_ROOT / "src" / "kg" / "prompts" / "extract_v1_examples.json"
LLM_CONFIG = REPO_ROOT / "configs" / "llm.toml"
PROMPT_VERSION = "extract_v1"
RECORD_FORMAT = 1
VN_TZ = timezone(timedelta(hours=7))

# Schema Q9 = B (chốt 27/10/2026): articles 4-8, 13, 14, 16-29, 32, 35-39, 41-47
SCOPE_ARTICLES = (4, 5, 6, 7, 8, 13, 14, *range(16, 30), 32, 35, 36, 37, 38, 39, *range(41, 48))
# Haiku vs Sonnet trial (PLAN #2, schema section 5 item 6). Few-shot examples must not come from these.
TRIAL_ARTICLES = (26, 28, 43, 44, 46)
# The six classification tables (F1, Q10 = B): rows are loaded by script; the LLM only extracts rules outside the table
TABLE_UNITS = tuple(f"{DOC_ID}.{u}" for u in ("d17.k2", "d32.k2", "d41.k2", "d43.k1", "d45.k2", "d47.k5"))
CHARS_PER_TOKEN = 3  # rough input estimate for Vietnamese text
DEFAULT_EST_OUTPUT_TOKENS = 2000  # per unit, thinking included; an assumption, not a measurement
STATUSES = ("ok", "invalid", "error")


# --- Input text -------------------------------------------------------------------------------------------------

def load_regulation(path: Path = PROCESSED_JSON) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    units = json.loads(path.read_text(encoding="utf-8"))["units"]
    by_id = {u["id"]: u for u in units}
    children: dict[str, list[dict]] = {}
    for u in units:
        children.setdefault(u["parent_id"], []).append(u)
    return by_id, children


def article_heading(dieu: dict) -> str:
    return f"Điều {dieu['number']}. {dieu['title']}"


def _with_moved(parts: list[tuple[str, str]], unit: dict, children: list[dict], prefix_id: str) -> list[tuple[str, str]]:
    """Children in order, each followed by the doan_khong_so paragraphs placed after it (schema 1.1, S1 = B).
    A paragraph belongs to the unit that holds it, so it carries that unit's id, not the child's."""
    moved: dict[str, list[str]] = {}
    for d in unit.get("doan_khong_so", []):
        moved.setdefault(d["sau"], []).append(d["text"])
    known = {c["id"] for c in children}
    if set(moved) - known:
        raise ValueError(f"{unit['id']}: doan_khong_so after unknown child {sorted(set(moved) - known)}")
    for child in children:
        parts.append((child["id"], child["text"]))
        parts += [(prefix_id, text) for text in moved.get(child["id"], [])]
    return parts


def unit_parts(unit_id: str, by_id: dict, children: dict) -> list[tuple[str, str]]:
    """(citation id, text) pieces of a content unit in reading order; joined with newlines they give the unit's text
    in qc3626_content_units.jsonl."""
    unit = by_id[unit_id]
    if unit["type"] == "khoan":
        kids = children.get(unit_id, [])
        if any(c["type"] != "diem" for c in kids):
            raise ValueError(f"{unit_id}: unexpected non-Diem child")
        return _with_moved([(unit_id, unit["own_text"])], unit, kids, unit_id)
    if unit["type"] == "dieu":
        heading = article_heading(unit)
        if not unit["own_text"].startswith(heading):
            raise ValueError(f"{unit_id}: own_text does not start with {heading!r}")
        lead = unit["own_text"][len(heading):].strip()
        if any(c["type"] != "khoan" for c in children.get(unit_id, [])):
            raise ValueError(f"{unit_id}: non-Khoan child under a Dieu is not handled")
        parts = [(unit_id, lead)] if lead else []
        # article-level paragraphs come after the clauses, which are not part of this unit
        return parts + [(unit_id, d["text"]) for d in unit.get("doan_khong_so", [])]
    raise ValueError(f"{unit_id}: not a content unit (type {unit['type']})")


def build_unit_text(unit_id: str, by_id: dict, children: dict) -> str:
    """Article heading, then every piece of the unit prefixed with its citation id: "[qc3626.d44.k1.a] a) ..."."""
    unit = by_id[unit_id]
    dieu = unit if unit["type"] == "dieu" else by_id[unit["parent_id"]]
    lines = [article_heading(dieu)] + [f"[{code}] {text}" for code, text in unit_parts(unit_id, by_id, children)]
    return "\n".join(lines)


def allowed_nguon(unit_id: str, by_id: dict, children: dict) -> set[str]:
    """Ids a node or edge of this unit may cite: the unit and its Diem children; a Dieu-lead unit only itself."""
    if by_id[unit_id]["type"] == "dieu":
        return {unit_id}
    return {unit_id} | {c["id"] for c in children.get(unit_id, []) if c["type"] == "diem"}


def build_user_message(unit_id: str, by_id: dict, children: dict) -> str:
    unit = by_id[unit_id]
    if unit["type"] == "dieu":
        header = f"Đơn vị: {unit_id} (phần dẫn của Điều {unit['number']}; các khoản của điều được trích riêng)."
    else:
        header = f"Đơn vị: {unit_id}."
    parts = [header]
    if unit_id in TABLE_UNITS:
        parts.append("Lưu ý: đơn vị này có bảng phân loại (do script nhập riêng). BỎ QUA các dòng/mức của bảng; "
                     "chỉ trích quy tắc nằm ngoài bảng.")
    parts += ["", build_unit_text(unit_id, by_id, children), "",
              "Mã được phép dùng cho nguon: " + ", ".join(sorted(allowed_nguon(unit_id, by_id, children)))]
    return "\n".join(parts)


# --- Prompt -----------------------------------------------------------------------------------------------------

def load_examples(path: Path = EXAMPLES_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["examples"]


def build_system_prompt(by_id: dict, children: dict, template_path: Path = PROMPT_PATH,
                        examples_path: Path = EXAMPLES_PATH) -> str:
    """Prompt template with the few-shot examples rendered: input built by the same code as real inputs."""
    blocks = []
    for i, ex in enumerate(load_examples(examples_path), 1):
        blocks.append(f"## Ví dụ {i}\n\nĐầu vào:\n```\n{build_user_message(ex['unit'], by_id, children)}\n```\n\n"
                      f"Đầu ra:\n```json\n{json.dumps(ex['output'], ensure_ascii=False)}\n```")
    template = template_path.read_text(encoding="utf-8")
    if "{{EXAMPLES}}" not in template:
        raise ValueError(f"{template_path}: missing {{{{EXAMPLES}}}} placeholder")
    return template.replace("{{EXAMPLES}}", "\n\n".join(blocks))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Context:
    by_id: dict
    children: dict
    existing_ids: set
    system_prompt: str
    prompt_sha256: str
    schema: dict
    schema_sha256: str


def make_context(processed_json: Path = PROCESSED_JSON, template_path: Path = PROMPT_PATH,
                 examples_path: Path = EXAMPLES_PATH) -> Context:
    by_id, children = load_regulation(processed_json)
    system = build_system_prompt(by_id, children, template_path, examples_path)
    schema = ss.json_schema()
    return Context(by_id, children, set(by_id), system, sha256_text(system), schema, ss.schema_sha256(schema))


# --- Unit selection ---------------------------------------------------------------------------------------------

def parse_articles(text: str) -> list[int]:
    try:
        numbers = sorted({int(x) for x in text.split(",") if x.strip()})
    except ValueError:
        raise ValueError(f"--articles expects numbers separated by commas, got {text!r}") from None
    outside = [n for n in numbers if n not in SCOPE_ARTICLES]
    if not numbers or outside:
        raise ValueError(f"articles outside the Q9 scope: {outside or text!r} (scope: {list(SCOPE_ARTICLES)})")
    return numbers


def select_units(articles, content_units_path: Path = CONTENT_UNITS) -> list[dict]:
    """Content units of the given articles, in reading order."""
    wanted = set(articles)
    return [u for u in load_content_units(content_units_path).values() if u["dieu_number"] in wanted]


# --- Fragment ---------------------------------------------------------------------------------------------------

def _merge_unique(items: list, more: list) -> list:
    return items + [x for x in more if x not in items]


def build_fragment(output: dict, unit_id: str) -> dict:
    """Graph fragment of a validated reply: same layout as qc3626_graph.json ({"meta", "nodes", "edges"}).

    Global ids follow semantic_ids.global_id. Two nodes of one reply that get the same global id are merged
    (nguon and bi_danh joined; a different ten is kept as bi_danh; other differing values are listed in
    meta.merge_conflicts and the first value is kept). Exact duplicate edges are dropped.
    """
    id_map, nodes, conflicts = {}, {}, []
    for label in ss.NODE_LABELS:
        for node in output[label]:
            gid = sid.global_id(label, node["ten"], unit_id, node["id"])
            id_map[node["id"]] = gid
            props = {k: v for k, v in node.items() if k not in ("id", "ten", "bi_danh", "nguon")}
            if gid not in nodes:
                nodes[gid] = {"id": gid, "labels": [label], "props": {
                    "id": gid, "ten": node["ten"], "bi_danh": list(dict.fromkeys(node["bi_danh"])),
                    "nguon_loai": "trich_xuat", "nguon": list(dict.fromkeys(node["nguon"])), **props}}
                continue
            kept = nodes[gid]["props"]
            kept["nguon"] = _merge_unique(kept["nguon"], node["nguon"])
            extra_names = [node["ten"]] if node["ten"] != kept["ten"] else []
            kept["bi_danh"] = _merge_unique(kept["bi_danh"], extra_names + node["bi_danh"])
            for key, value in props.items():
                if key not in kept:
                    kept[key] = value
                elif kept[key] != value:
                    conflicts.append({"id": gid, "property": key, "kept": kept[key], "dropped": value,
                                      "local_id": node["id"]})
    edges, seen = [], set()
    for edge in output["edges"]:
        props = {k: v for k, v in edge.items() if k not in ("type", "from", "to")}
        if edge["type"] == "TINH_VAO" and props.get("boi_canh") is not None:
            props["boi_canh"] = id_map[props["boi_canh"]]
        out = {"type": edge["type"], "from": id_map[edge["from"]], "to": id_map[edge["to"]], "props": props}
        key = json.dumps(out, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key)
            edges.append(out)
    return {"meta": {"unit": unit_id, "nguon_loai": "trich_xuat", "id_map": id_map, "merge_conflicts": conflicts},
            "nodes": list(nodes.values()), "edges": edges}


# --- One unit ---------------------------------------------------------------------------------------------------

def record_path(out_dir: Path, role: str, unit_id: str, run: int) -> Path:
    suffix = "" if run == 0 else f".run{run}"
    return out_dir / role / f"{unit_id}{suffix}.json"


def _loose_json(text: str):
    """The first {...} object in the reply, or None (replies may carry code fences or prose)."""
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def evaluate_reply(text: str, parsed, finish_reason: str | None, truncated: bool, unit_id: str,
                   ctx: Context) -> dict:
    """Status, errors, warnings and fragment for a reply (pure: no I/O)."""
    output = parsed if isinstance(parsed, dict) else _loose_json(text)
    errors, warnings = [], []
    if finish_reason == "refusal":
        errors.append("model refused (finish_reason = refusal)")
    if truncated:
        errors.append(f"reply hit max_tokens (finish_reason = {finish_reason}); output may be cut")
    if output is None:
        errors.append("no JSON object found in the reply")
    else:
        report = ss.validate(output, allowed_nguon(unit_id, ctx.by_id, ctx.children), ctx.existing_ids)
        errors += report.errors
        warnings += report.warnings
    fragment = build_fragment(output, unit_id) if not errors else None
    return {"status": "ok" if not errors else "invalid", "errors": errors, "warnings": warnings,
            "fragment": fragment}


def _default_complete():
    from src.llm.core import complete  # imported lazily: tests and dry runs need no provider packages

    return complete


def _reusable(path: Path, meta_now: dict) -> dict | None:
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    meta = record.get("metadata") or {}
    keys = ("role", "run", "model_requested", "prompt_sha256", "schema_sha256", "input_sha256")
    if record.get("status") == "error" or meta.get("truncated") or any(meta.get(k) != meta_now[k] for k in keys):
        return None
    return record


def write_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, ensure_ascii=False, indent=1) + "\n")


def extract_unit(unit_id: str, role: str, run: int, ctx: Context, model_requested: str,
                 complete_fn=None, out_dir: Path = OUT_ROOT) -> dict:
    """Extract one unit and write its record; returns the record. Never raises on an LLM failure."""
    user_message = build_user_message(unit_id, ctx.by_id, ctx.children)
    meta = {"unit": unit_id, "role": role, "run": run, "model_requested": model_requested,
            "prompt_version": PROMPT_VERSION, "prompt_sha256": ctx.prompt_sha256,
            "schema_sha256": ctx.schema_sha256, "input_sha256": sha256_text(user_message)}
    path = record_path(out_dir, role, unit_id, run)
    old = _reusable(path, meta)
    if old is not None:
        raw = old["raw"]
        verdict = evaluate_reply(raw["text"], raw["parsed"], old["metadata"].get("finish_reason"),
                                 bool(old["metadata"].get("truncated")), unit_id, ctx)
        record = {**old, **verdict}
        record["metadata"] = {**old["metadata"], "reused_record": True}
        write_record(path, record)
        return record

    complete_fn = complete_fn or _default_complete()
    try:
        result = complete_fn(role, ctx.system_prompt, [{"role": "user", "content": user_message}],
                             json_schema=ctx.schema, run=run)
    except Exception as e:  # LLMError and anything the provider raises: keep a record, do not skip silently
        record = {"format": RECORD_FORMAT, "unit": unit_id, "status": "error",
                  "errors": [f"{type(e).__name__}: {e}"], "warnings": [], "raw": None, "fragment": None,
                  "metadata": {**meta, "called_at": datetime.now(VN_TZ).isoformat(timespec="seconds")}}
        write_record(path, record)
        return record

    verdict = evaluate_reply(result.text, result.parsed, result.finish_reason, result.truncated, unit_id, ctx)
    meta.update(provider=result.provider, model_served=result.model_served, input_tokens=result.input_tokens,
                output_tokens=result.output_tokens, finish_reason=result.finish_reason, truncated=result.truncated,
                structured_output=result.structured_output, from_cache=result.from_cache,
                called_at=result.called_at, latency_s=result.latency_s, attempts=result.attempts,
                params=result.params, reused_record=False)
    if meta["model_requested"] != result.model_requested:
        verdict["errors"].append(f"role resolved to {result.model_requested}, expected {model_requested}")
        verdict["status"], verdict["fragment"] = "invalid", None
    record = {"format": RECORD_FORMAT, "unit": unit_id, **verdict,
              "raw": {"text": result.text, "parsed": result.parsed}, "metadata": meta}
    record = {k: record[k] for k in ("format", "unit", "status", "errors", "warnings", "raw", "fragment", "metadata")}
    write_record(path, record)
    return record


# --- Estimate and CLI -------------------------------------------------------------------------------------------

def load_pricing(model: str, path: Path = LLM_CONFIG) -> dict | None:
    return tomllib.loads(path.read_text(encoding="utf-8")).get("pricing", {}).get(model)


def estimate(unit_ids: list[str], ctx: Context, est_output_tokens: int, pricing: dict | None,
             reusable: set[str] = frozenset()) -> dict:
    """Rough token/cost estimate (characters / CHARS_PER_TOKEN for input; a fixed output per unit)."""
    rows = []
    for unit_id in unit_ids:
        msg = build_user_message(unit_id, ctx.by_id, ctx.children)
        rows.append({"unit": unit_id, "unit_chars": len(build_unit_text(unit_id, ctx.by_id, ctx.children)),
                     "message_chars": len(msg), "reused": unit_id in reusable})
    to_call = [r for r in rows if not r["reused"]]
    input_chars = sum(len(ctx.system_prompt) + r["message_chars"] for r in to_call)
    tokens_in = input_chars // CHARS_PER_TOKEN
    tokens_out = est_output_tokens * len(to_call)
    cost = None
    if pricing:
        cost = round(tokens_in / 1e6 * pricing["input_per_mtok"] + tokens_out / 1e6 * pricing["output_per_mtok"], 4)
    return {"rows": rows, "n_units": len(rows), "n_to_call": len(to_call),
            "unit_chars": sum(r["unit_chars"] for r in rows), "system_chars": len(ctx.system_prompt),
            "input_chars": input_chars, "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost}


def print_estimate(est: dict, role: str, model: str, est_output_tokens: int, pricing: dict | None) -> None:
    for r in est["rows"]:
        flag = " (đã có bản ghi, không gọi lại)" if r["reused"] else ""
        table = " [có bảng: bỏ qua dòng bảng]" if r["unit"] in TABLE_UNITS else ""
        print(f"  {r['unit']}: {r['unit_chars']} ký tự{table}{flag}")
    print(f"Số đơn vị: {est['n_units']} (cần gọi LLM: {est['n_to_call']}) | tổng ký tự nội dung: {est['unit_chars']} "
          f"| system prompt: {est['system_chars']} ký tự/lời gọi")
    print(f"ƯỚC THÔ (role {role}, model {model}): đầu vào ≈ {est['input_chars']} ký tự / {CHARS_PER_TOKEN} "
          f"≈ {est['tokens_in']} token; đầu ra giả định {est_output_tokens} token/đơn vị (gồm thinking) "
          f"≈ {est['tokens_out']} token")
    if est["cost_usd"] is None:
        print(f"  Chi phí: chưa có giá cho {model} trong configs/llm.toml [pricing]")
    else:
        print(f"  Chi phí ≈ ${est['cost_usd']} (giá {pricing['input_per_mtok']}/{pricing['output_per_mtok']} USD "
              f"mỗi 1M token vào/ra, nguồn {pricing.get('source')}, kiểm {pricing.get('checked')}). "
              f"Chỉ là ước thô, không phải số đo.")


def extract_roles(config_path: Path = LLM_CONFIG) -> list[str]:
    roles = tomllib.loads(config_path.read_text(encoding="utf-8")).get("roles", {})
    return sorted(name for name in roles if name.startswith("extract"))


def run_units(unit_ids: list[str], role: str, run: int, ctx: Context, model: str, complete_fn=None,
              out_dir: Path = OUT_ROOT) -> list[dict]:
    records = []
    for unit_id in unit_ids:
        record = extract_unit(unit_id, role, run, ctx, model, complete_fn=complete_fn, out_dir=out_dir)
        meta = record["metadata"]
        source = "bản ghi cũ" if meta.get("reused_record") else ("cache" if meta.get("from_cache") else "gọi mới")
        print(f"  {unit_id}: {record['status']} ({source}; lỗi {len(record['errors'])}, "
              f"cảnh báo {len(record['warnings'])})")
        records.append(record)
    return records


def main(argv=None, complete_fn=None, out_dir: Path = OUT_ROOT, input_fn=input) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    pick = parser.add_mutually_exclusive_group(required=True)
    pick.add_argument("--articles", help="article numbers inside the Q9 scope, e.g. 26,28")
    pick.add_argument("--all", action="store_true", help=f"all {len(SCOPE_ARTICLES)} articles of the Q9 scope")
    parser.add_argument("--role", default="extract", help="LLM role in configs/llm.toml (extract, extract_sonnet)")
    parser.add_argument("--run", type=int, default=0, help="repetition index; part of the LLM cache key")
    parser.add_argument("--dry-run", action="store_true", help="list units and a rough cost estimate; no LLM call")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation before calling the LLM")
    parser.add_argument("--est-output-tokens", type=int, default=DEFAULT_EST_OUTPUT_TOKENS,
                        help="assumed output tokens per unit for the estimate")
    args = parser.parse_args(argv)

    roles = extract_roles()
    if args.role not in roles:
        print(f"LỖI: role {args.role!r} không có trong configs/llm.toml (các role trích xuất: {roles}).", file=sys.stderr)
        return 2
    try:
        articles = list(SCOPE_ARTICLES) if args.all else parse_articles(args.articles)
    except ValueError as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 2
    model = tomllib.loads(LLM_CONFIG.read_text(encoding="utf-8"))["roles"][args.role]["model"]
    pricing = load_pricing(model)
    ctx = make_context()
    unit_ids = [u["id"] for u in select_units(articles)]
    reusable = set()
    for unit_id in unit_ids:
        meta = {"role": args.role, "run": args.run, "model_requested": model, "prompt_sha256": ctx.prompt_sha256,
                "schema_sha256": ctx.schema_sha256,
                "input_sha256": sha256_text(build_user_message(unit_id, ctx.by_id, ctx.children))}
        if _reusable(record_path(out_dir, args.role, unit_id, args.run), meta) is not None:
            reusable.add(unit_id)

    print(f"Điều: {', '.join(map(str, articles))} | role {args.role} | run {args.run} | prompt {PROMPT_VERSION} "
          f"sha256 {ctx.prompt_sha256[:12]} | schema sha256 {ctx.schema_sha256[:12]}")
    est = estimate(unit_ids, ctx, args.est_output_tokens, pricing, reusable)
    print_estimate(est, args.role, model, args.est_output_tokens, pricing)
    if args.dry_run:
        print("Dry-run: không gọi LLM, không ghi file.")
        return 0
    if est["n_to_call"] and not args.yes:
        if input_fn("Gọi LLM cho các đơn vị trên? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Đã hủy, không gọi LLM.")
            return 1
    records = run_units(unit_ids, args.role, args.run, ctx, model, complete_fn=complete_fn, out_dir=out_dir)
    counts = {s: sum(r["status"] == s for r in records) for s in STATUSES}
    tokens_in = sum((r["metadata"].get("input_tokens") or 0) for r in records
                    if not r["metadata"].get("from_cache") and not r["metadata"].get("reused_record"))
    tokens_out = sum((r["metadata"].get("output_tokens") or 0) for r in records
                     if not r["metadata"].get("from_cache") and not r["metadata"].get("reused_record"))
    print(f"Kết quả: ok {counts['ok']}, invalid {counts['invalid']}, error {counts['error']} | token lời gọi mới: "
          f"vào {tokens_in}, ra {tokens_out} | ghi {(out_dir / args.role).as_posix()}")
    return 0 if counts["ok"] == len(records) else 1


if __name__ == "__main__":
    sys.exit(main())
