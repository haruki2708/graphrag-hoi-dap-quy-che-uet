"""Agreement between the project owner's hand labels and the GLM judge on the 30 calibration answers.

Run from the repo root:
  python eval/judge_agreement.py <filled sheet.xlsx> [--manifest <json>] [--out <json>] [--overwrite]
                                 [--judged <system>=<judged csv> ...] [--label <name>]

Inputs: the graded sheet (sheet "cham", column nhan), data/eval/calibration/judge_calib_v1_manifest.json (stt ->
system, id, run) and the two *_judged.csv files named in the manifest; the GLM labels are joined back from the CSVs.
--judged system=path (one per system in the manifest, all or none) replaces those files, e.g. with the v2-prompt
results of eval/judge_answers.py --manifest; the csv only needs the manifest's rows.
Prints and writes eval/results/<date>_judge_agreement_<label>.json (label defaults to v1): 3-label agreement (the PLAN #2 threshold metric),
Cohen's kappa (unweighted and linear-weighted, order sai < mot_phan < dung), binary agreement (dung vs not dung),
confusion matrix (human x GLM), per-system results and the list of disagreements.
An empty or misspelt nhan cell, or a sheet that does not match the manifest, is reported by stt and nothing is computed.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import csv  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

from eval import retrieval_metrics as rm  # noqa: E402
from eval.convert_questions import read_xlsx  # noqa: E402

CALIB_DIR = REPO_ROOT / "data" / "eval" / "calibration"
MANIFEST_PATH = CALIB_DIR / "judge_calib_v1_manifest.json"
RESULTS_DIR = REPO_ROOT / "eval" / "results"
VN_TZ = timezone(timedelta(hours=7))
ORDER = ("sai", "mot_phan", "dung")  # ordinal scale used for the weighted kappa and the leniency direction
THRESHOLDS = (0.85, 0.90)            # PLAN #2: agreement with hand grading >= ~85-90%
REQUIRED_COLUMNS = ("stt", "cau_hoi", "cau_tra_loi", "nhan", "ghi_chu")


class SheetError(Exception):
    """The graded sheet cannot be used; .problems is a list of (stt or None, message)."""

    def __init__(self, problems: list[tuple]):
        super().__init__(f"{len(problems)} problem(s)")
        self.problems = problems


# --------------------------------------------------------------------------- statistics

def confusion(pairs: list[tuple[str, str]]) -> list[list[int]]:
    """Matrix[i][j] = number of samples with human label ORDER[i] and GLM label ORDER[j]."""
    matrix = [[0] * len(ORDER) for _ in ORDER]
    for human, glm in pairs:
        matrix[ORDER.index(human)][ORDER.index(glm)] += 1
    return matrix


def cohen_kappa(matrix: list[list[int]], weighted: bool = False) -> float | None:
    """Cohen's kappa from a confusion matrix; weighted = linear weights |i-j|/(k-1). None when undefined."""
    k = len(matrix)
    n = sum(map(sum, matrix))
    if n == 0:
        return None
    rows = [sum(matrix[i]) for i in range(k)]
    cols = [sum(matrix[i][j] for i in range(k)) for j in range(k)]
    weight = (lambda i, j: abs(i - j) / (k - 1)) if weighted else (lambda i, j: 0.0 if i == j else 1.0)
    observed = sum(weight(i, j) * matrix[i][j] for i in range(k) for j in range(k)) / n
    expected = sum(weight(i, j) * rows[i] * cols[j] for i in range(k) for j in range(k)) / (n * n)
    if expected == 0:
        return None
    return 1 - observed / expected


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (centre - half, centre + half)


def summarize_pairs(pairs: list[tuple[str, str]]) -> dict:
    n = len(pairs)
    agree = sum(h == g for h, g in pairs)
    binary_agree = sum((h == "dung") == (g == "dung") for h, g in pairs)
    matrix = confusion(pairs)
    low, high = wilson_interval(agree, n)
    return {
        "n": n,
        "agree": agree,
        "agreement": agree / n if n else None,
        "agreement_ci95": [round(low, 4), round(high, 4)],
        "kappa": cohen_kappa(matrix),
        "kappa_linear_weighted": cohen_kappa(matrix, weighted=True),
        "binary_agree": binary_agree,
        "binary_agreement": binary_agree / n if n else None,
        "glm_more_lenient": sum(ORDER.index(g) > ORDER.index(h) for h, g in pairs),
        "glm_stricter": sum(ORDER.index(g) < ORDER.index(h) for h, g in pairs),
        "confusion_human_x_glm": matrix,
    }


def threshold_report(agree: int, n: int) -> dict:
    """Pass/fail of the 3-label agreement at 85% and 90%, with the number of matches that would be needed."""
    return {f"{round(t * 100)}%": {"pass": agree * 100 >= round(t * 100) * n, "needed": math.ceil(round(t * 100) * n / 100)}
            for t in THRESHOLDS}


# --------------------------------------------------------------------------- reading and joining

def normalize_label(raw) -> str | None:
    """Trim + lower-case; only dung / mot_phan / sai are accepted (no accents, no synonyms)."""
    text = str(raw if raw is not None else "").strip().lower()
    return text if text in ORDER else None


def squash(text) -> str:
    """Whitespace-insensitive form for comparing sheet text with the source text (Excel may change line breaks)."""
    return " ".join(str(text or "").split())


def read_graded_sheet(path: Path) -> list[dict]:
    sheets = read_xlsx(path)
    if "cham" not in sheets or not sheets["cham"]:
        raise SheetError([(None, 'thiếu sheet "cham"')])
    header = [str(h or "").strip().lower() for h in sheets["cham"][0]]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise SheetError([(None, f'sheet "cham" thiếu cột {missing}')])
    col = {name: header.index(name) for name in REQUIRED_COLUMNS}
    rows = []
    for line in sheets["cham"][1:]:
        cell = lambda name: line[col[name]] if col[name] < len(line) else None  # noqa: E731
        if all(v in (None, "") for v in line):
            continue
        rows.append({name: cell(name) for name in REQUIRED_COLUMNS})
    return rows


def load_judged(manifest: dict, overrides: dict[str, Path] | None = None) -> dict:
    """{(system, id, run): csv row} from the CSVs named in the manifest, or from `overrides` ({system: path})."""
    overrides = overrides or {}
    if overrides and set(overrides) != set(manifest["sources"]):
        raise ValueError(f"--judged must cover exactly the systems {sorted(manifest['sources'])}, got {sorted(overrides)}")
    rows = {}
    for system, info in manifest["sources"].items():
        with (overrides.get(system) or REPO_ROOT / info["file"]).open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rows[(system, row["id"], int(row["run"]))] = row
    return rows


def join_samples(sheet_rows: list[dict], manifest: dict, judged: dict, questions: dict) -> list[dict]:
    """Join graded rows to the manifest and the GLM labels by stt; raise SheetError listing every problem found."""
    problems, seen, samples = [], set(), []
    for row in sheet_rows:
        stt_text = str(row["stt"] if row["stt"] is not None else "").strip()
        if not stt_text.isdigit():
            problems.append((None, f"dòng có stt không đọc được: {row['stt']!r}"))
            continue
        stt = int(stt_text)
        if stt in seen:
            problems.append((stt, "stt trùng"))
            continue
        seen.add(stt)
        entry = manifest["samples"].get(str(stt))
        if entry is None:
            problems.append((stt, "stt không có trong manifest"))
            continue
        human = normalize_label(row["nhan"])
        if human is None:
            shown = "để trống" if not str(row["nhan"] or "").strip() else f"không hợp lệ: {str(row['nhan'])!r}"
            problems.append((stt, f"nhan {shown} (chỉ dùng dung / mot_phan / sai)"))
        csv_row = judged.get((entry["system"], entry["id"], entry["run"]))
        question = questions.get(entry["id"])
        if csv_row is None or question is None:
            problems.append((stt, f"manifest trỏ tới ({entry['system']}, {entry['id']}, run {entry['run']}) không có trong dữ liệu"))
            continue
        if squash(row["cau_hoi"]) != squash(question["question"]) or squash(row["cau_tra_loi"]) != squash(csv_row["answer"]):
            problems.append((stt, "cau_hoi / cau_tra_loi trong phiếu không khớp manifest (phiếu và manifest không cùng bản?)"))
            continue
        glm = normalize_label(csv_row["judge_label"])
        if glm is None:
            problems.append((stt, f"CSV không có nhãn GLM hợp lệ ({csv_row['judge_label']!r})"))
            continue
        if human is None:
            continue
        samples.append({
            "stt": stt, "system": entry["system"], "id": entry["id"], "run": entry["run"], "type": question["type"],
            "human": human, "glm": glm, "glm_reason": csv_row["judge_reason"], "glm_model": csv_row["judge_model"],
            "glm_prompt": csv_row.get("judge_prompt") or "v1",
            "human_note": str(row["ghi_chu"] or "").strip(), "question": question["question"],
        })
    for stt in sorted(int(k) for k in manifest["samples"] if int(k) not in seen):
        problems.append((stt, "có trong manifest nhưng thiếu trong phiếu"))
    if problems:
        raise SheetError(sorted(problems, key=lambda p: (p[0] is None, p[0] or 0)))
    return sorted(samples, key=lambda s: s["stt"])


def analyze(samples: list[dict]) -> dict:
    pairs = [(s["human"], s["glm"]) for s in samples]
    overall = summarize_pairs(pairs)
    mismatches = [
        {"stt": s["stt"], "system": s["system"], "id": s["id"], "run": s["run"], "type": s["type"],
         "human": s["human"], "glm": s["glm"],
         "direction": "glm_more_lenient" if ORDER.index(s["glm"]) > ORDER.index(s["human"]) else "glm_stricter",
         "question": s["question"], "glm_reason": s["glm_reason"], "human_note": s["human_note"]}
        for s in samples if s["human"] != s["glm"]
    ]
    return {
        "labels_order": list(ORDER),
        "overall": overall,
        "thresholds_on_3_label_agreement": threshold_report(overall["agree"], overall["n"]),
        "by_system": {system: summarize_pairs([(s["human"], s["glm"]) for s in samples if s["system"] == system])
                      for system in sorted({s["system"] for s in samples})},
        "mismatches": mismatches,
        "judge_models": sorted({s["glm_model"] for s in samples}),
        "judge_prompt_versions": sorted({s["glm_prompt"] for s in samples}),
    }


# --------------------------------------------------------------------------- output

def fmt(value, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def print_report(result: dict) -> None:
    o = result["overall"]
    print(f"Mẫu: {o['n']} | giám khảo: {', '.join(result['judge_models'])} | prompt: {', '.join(result['judge_prompt_versions'])}")
    print(f"Độ trùng 3 nhãn: {o['agree']}/{o['n']} = {fmt(o['agreement'])} (CI95 Wilson {o['agreement_ci95'][0]:.2f}–{o['agreement_ci95'][1]:.2f})")
    print(f"Cohen's kappa: {fmt(o['kappa'])} | có trọng số tuyến tính: {fmt(o['kappa_linear_weighted'])}")
    print(f"Độ trùng nhị phân (dung / không dung): {o['binary_agree']}/{o['n']} = {fmt(o['binary_agreement'])}")
    print(f"GLM nương tay hơn người: {o['glm_more_lenient']} mẫu | khắt khe hơn: {o['glm_stricter']} mẫu")
    for name, info in result["thresholds_on_3_label_agreement"].items():
        print(f"  ngưỡng {name}: {'ĐẠT' if info['pass'] else 'KHÔNG ĐẠT'} (cần ≥ {info['needed']}/{o['n']} mẫu trùng)")
    print("Ma trận nhầm lẫn (hàng = người, cột = GLM):")
    print("  " + " " * 10 + "".join(f"{label:>10}" for label in ORDER))
    for label, line in zip(ORDER, o["confusion_human_x_glm"]):
        print(f"  {label:>10}" + "".join(f"{v:>10}" for v in line))
    for system, s in result["by_system"].items():
        print(f"Hệ {system}: n={s['n']} trùng {s['agree']}/{s['n']} = {fmt(s['agreement'])} | kappa {fmt(s['kappa'])} | "
              f"nhị phân {fmt(s['binary_agreement'])} | GLM nương tay {s['glm_more_lenient']}, khắt khe {s['glm_stricter']}")
    print(f"Mẫu lệch ({len(result['mismatches'])}):")
    for m in result["mismatches"]:
        print(f"  stt {m['stt']} [{m['system']} {m['id']} run {m['run']}, {m['type']}] người={m['human']} GLM={m['glm']} ({m['direction']})")
        print(f"      GLM: {m['glm_reason']}")
        print(f"      Người: {m['human_note'] or '(không ghi chú)'}")


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    overwrite = "--overwrite" in argv
    args = [a for a in argv if a != "--overwrite"]
    options, judged_args, bad_usage = {}, [], False
    for flag in ("--manifest", "--out", "--label", "--judged"):
        while flag in args:
            i = args.index(flag)
            if i + 1 >= len(args):
                bad_usage = True
                del args[i:]
                break
            value = args[i + 1]
            del args[i:i + 2]
            if flag == "--judged":
                judged_args.append(value)
            else:
                options[flag] = value
    overrides = {}
    for item in judged_args:
        system, sep, path = item.partition("=")
        if not (sep and system and path):
            bad_usage = True
        else:
            overrides[system] = Path(path)
    if len(args) != 1 or bad_usage:
        print("Cách dùng: python eval/judge_agreement.py <phiếu đã chấm .xlsx> [--manifest <json>] [--out <json>] "
              "[--overwrite] [--judged <hệ>=<csv> ...] [--label <tên>]", file=sys.stderr)
        return 2
    manifest_path = Path(options.get("--manifest") or MANIFEST_PATH)
    label = options.get("--label", "v1")
    date = datetime.now(VN_TZ).strftime("%Y-%m-%d")
    out_path = Path(options["--out"]) if "--out" in options else RESULTS_DIR / f"{date}_judge_agreement_{label}.json"
    if out_path.exists() and not overwrite:
        print(f"LỖI: đã có {out_path.name}; không ghi đè (dùng --overwrite).", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    questions = {q["id"]: q for q in rm.load_dev_questions()}
    try:
        samples = join_samples(read_graded_sheet(Path(args[0])), manifest, load_judged(manifest, overrides), questions)
    except (ValueError, OSError) as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 2
    except SheetError as e:
        print(f"LỖI: phiếu chưa dùng được, không tính gì ({len(e.problems)} vấn đề):", file=sys.stderr)
        for stt, message in e.problems:
            print(f"  stt {stt}: {message}" if stt is not None else f"  {message}", file=sys.stderr)
        return 2
    result = analyze(samples)
    print_report(result)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "computed_at": datetime.now(VN_TZ).isoformat(timespec="seconds"),
        "sheet": Path(args[0]).name, "manifest": manifest_path.name, "label": label,
        "judged_files": {s: str(p) for s, p in overrides.items()} or "as listed in the manifest", "manifest_source_commit": manifest["source_commit"],
        "agreement_metric": "3-label exact agreement (human vs GLM); thresholds from PLAN #2",
        **result, "samples": samples,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Ghi {out_path.relative_to(REPO_ROOT) if out_path.is_relative_to(REPO_ROOT) else out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
