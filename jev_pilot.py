"""JEV pilot CLI (backend.md §7): select, blind label, score, report — over HTTP only, never SQLite.

    python jev_pilot.py select --n 50 --seed 7 [--out FILE]
    python jev_pilot.py label  --sample FILE --labeler NAME [--notes] [--revisit-skipped]
    python jev_pilot.py score  --sample FILE --labeler NAME [--allow-unlabelled]
    python jev_pilot.py report --sample FILE --labeler NAME [--out FILE]

The token comes from INGEST_TOKEN. Sample files and reports hold refs and numbers, never prompt text.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import httpx

import pilot_metrics

RUBRIC = "difficulty-v0"
EVALUATOR_PROJECT = "token-inspector"
COMPLETED = {"session_end", "next_task", "inferred"}
MIN_RETENTION = timedelta(days=5)
DEFAULT_DIR = Path("~/.local/share/token-inspector/pilot").expanduser()
_BIDI = {0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)}
TERMINAL = {"done", "stopped", "aborted"}


def _parse(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


# ---- pure logic -----------------------------------------------------------------------------


def is_eligible(item: dict, now: datetime) -> bool:
    expires = _parse(item.get("prompt_expires_at"))
    return (
        item.get("hierarchy_status") == "root"
        and item.get("completion") in COMPLETED
        and item.get("prompt_state") == "retained"
        and expires is not None and expires > now + MIN_RETENTION
        and item.get("start_complexity") is not None
        and item.get("start_complexity_method") == "request-shape-v1"
        and item.get("project_name") != EVALUATOR_PROJECT
    )


def select_sample(candidates: list[dict], n: int, seed: int) -> dict:
    """Stratified by RS-v1 level, equal allocation, shortfall and remainder from the leftover pool."""
    rng = random.Random(seed)
    strata: dict[int, list[str]] = {}
    completion: dict[str, str] = {}
    for item in sorted(candidates, key=lambda c: c["task_ref"]):
        strata.setdefault(int(item["start_complexity"]), []).append(item["task_ref"])
        completion[item["task_ref"]] = item["completion"]
    non_empty = sorted(k for k, refs in strata.items() if refs)
    chosen: list[str] = []
    per_stratum = {}
    allocation = n // len(non_empty) if non_empty else 0
    for level in non_empty:
        refs = strata[level]
        take = rng.sample(refs, min(allocation, len(refs)))
        chosen += take
        per_stratum[level] = {"population": len(refs), "sample": len(take)}
    leftover = sorted(set(r for refs in strata.values() for r in refs) - set(chosen))
    extra = rng.sample(leftover, min(n - len(chosen), len(leftover))) if n > len(chosen) else []
    chosen += extra
    for level in non_empty:
        per_stratum[level]["sample"] = sum(1 for r in chosen if r in strata[level])
    counts: dict[str, int] = {}
    for ref in chosen:
        counts[completion[ref]] = counts.get(completion[ref], 0) + 1
    return {
        "seed": seed,
        "n_requested": n,
        "rules": "root; completion in session_end/next_task/inferred; prompt retained > 5 d; "
                 "RS-v1 method request-shape-v1; not token-inspector",
        "strata": {str(k): v for k, v in per_stratum.items()},
        "completion_counts": counts,
        "task_refs": chosen,
    }


def safe_terminal(value: str) -> str:
    """Escape control and bidi/format characters so no ANSI or reordering reaches the terminal."""
    out = []
    for ch in value:
        code = ord(ch)
        if ch in "\n\t":
            out.append(ch)
        elif unicodedata.category(ch) == "Cc":
            out.append(f"\\x{code:02x}" if code <= 0xFF else f"\\u{code:04x}")
        elif code in _BIDI:
            out.append(f"\\u{code:04x}")
        else:
            out.append(ch)
    return "".join(out)


def labelling_progress(evaluations: list[dict], labeler: str) -> str:
    """labelled | skipped_by_labeler | not_labelled — from this labeller's human rows only."""
    for row in evaluations:
        if row.get("evaluator") == "human" and row.get("labeler") == labeler and row.get("rubric_version") == RUBRIC:
            return "labelled" if row.get("status") == "ok" else "skipped_by_labeler"
    return "not_labelled"


def to_label(sample_refs: list[str], progress: dict[str, str], seed: int, revisit_skipped: bool) -> list[str]:
    order = list(sample_refs)
    random.Random(seed).shuffle(order)
    wanted = {"not_labelled", "skipped_by_labeler"} if revisit_skipped else {"not_labelled"}
    return [r for r in order if progress.get(r, "not_labelled") in wanted]


def missing_labels(sample_refs: list[str], progress: dict[str, str]) -> list[str]:
    return [r for r in sample_refs if progress.get(r, "not_labelled") == "not_labelled"]


def _latest(evaluations: list[dict], evaluator: str) -> Optional[dict]:
    rows = [e for e in evaluations if e.get("evaluator") == evaluator and e.get("rubric_version") == RUBRIC]
    return rows[0] if rows else None  # the API returns evaluations newest first


def build_report(sample: dict, details: dict[str, dict], labeler: str) -> tuple[str, dict]:
    pairs, exclusions, other_labelers = [], {}, set()
    for ref in sample["task_refs"]:
        detail = details.get(ref) or {}
        evaluations = detail.get("evaluations", [])
        other_labelers |= {e["labeler"] for e in evaluations if e.get("evaluator") == "human"
                           and e.get("labeler") and e["labeler"] != labeler}
        human = next((e for e in evaluations if e.get("evaluator") == "human" and e.get("labeler") == labeler
                      and e.get("rubric_version") == RUBRIC), None)
        jev_ok = next((e for e in evaluations if e.get("evaluator") == "jev" and e.get("status") == "ok"
                       and e.get("rubric_version") == RUBRIC), None)
        jev_latest = _latest([e for e in evaluations if e.get("status") != "skipped"], "jev")
        if human is None:
            reason = "not_labelled"
        elif human.get("status") != "ok":
            reason = "skipped_by_labeler"
        elif detail.get("start_complexity") is None or detail.get("start_complexity_method") != "request-shape-v1":
            reason = "rs1_missing"
        elif jev_ok is None:
            if jev_latest is not None and jev_latest.get("status") in ("error", "rate_limited", "deferred"):
                reason = f"jev_{jev_latest['status']}"
            elif detail.get("prompt_state") != "retained":
                reason = "prompt_expired_before_scoring"
            else:
                reason = "jev_error"
        else:
            reason = None
        if reason:
            exclusions[reason] = exclusions.get(reason, 0) + 1
            continue
        pairs.append({"ref": ref, "human": int(human["label"]), "jev": float(jev_ok["raw_score"]),
                      "rs1": int(detail["start_complexity"]) - 1, "confidence": jev_ok.get("confidence") or 0.0,
                      "provider": jev_ok.get("provider_used"), "cost": jev_ok.get("cost_usd")})
    metrics = pilot_metrics.cohort_metrics(pairs, sample["seed"])
    total = len(sample["task_refs"])

    def fmt(value):
        return "undefined" if value is None else f"{value:.3f}"

    def ci(name):
        info = metrics["ci"][name]
        return "unavailable" if not info["available"] else f"[{info['ci'][0]:.3f}, {info['ci'][1]:.3f}]"

    lines = [
        "# JEV pilot report",
        "",
        f"Pre-registered: cohort ≥ {pilot_metrics.MIN_PAIRS} pairs; PASS iff ρ_JEV ≥ {pilot_metrics.RHO_MIN}, "
        f"ρ_JEV − ρ_RS1 ≥ {pilot_metrics.RHO_ADVANTAGE_MIN} and κ_JEV ≥ {pilot_metrics.KAPPA_MIN}.",
        f"Labeller: `{labeler}` (other labellers present: {len(other_labelers)}). Sample seed {sample['seed']}.",
        "",
        f"## Verdict: {metrics['verdict']}",
        "",
        f"Cohort {metrics['n']} / sample {total} (coverage {metrics['n'] / total:.0%})." if total else "Empty sample.",
        "",
        "| Metric | Value | 95% bootstrap CI |",
        "|---|---|---|",
        f"| Spearman ρ JEV vs human | {fmt(metrics['rho_jev'])} | {ci('rho_jev')} |",
        f"| Spearman ρ RS-v1 vs human | {fmt(metrics['rho_rs1'])} | |",
        f"| ρ_JEV − ρ_RS1 | {fmt(metrics['rho_delta'])} | {ci('rho_delta')} |",
        f"| Quadratic-weighted κ JEV | {fmt(metrics['kappa_jev'])} | {ci('kappa_jev')} |",
        f"| MAE JEV / RS-v1 | {fmt(metrics['mae_jev'])} / {fmt(metrics['mae_rs1'])} | |",
        f"| Exact / within-1 agreement | {fmt(metrics['exact'])} / {fmt(metrics['within1'])} | |",
        f"| Mean confidence, abs(error) ≤ 0.5 / > 0.5 | {fmt(metrics['confidence_close'])} / "
        f"{fmt(metrics['confidence_far'])} | |",
        f"| JEV cost (USD) | {metrics['cost_usd']:.6f} | |",
        "",
        f"Bootstrap resamples dropped as undefined: ρ {metrics['ci']['rho_jev']['dropped']}, "
        f"κ {metrics['ci']['kappa_jev']['dropped']} of {pilot_metrics.BOOTSTRAP_RESAMPLES}.",
        "",
        "## Exclusions",
        "",
        "| Reason | Count |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in sorted(exclusions.items())],
        "",
        "## Sample",
        "",
        "| RS-v1 stratum | Population | Sample |",
        "|---|---|---|",
        *[f"| {k} | {v['population']} | {v['sample']} |" for k, v in sorted(sample.get("strata", {}).items())],
        "",
        "Completion types in the sample: "
        + ", ".join(f"{k} {v}" for k, v in sorted(sample.get("completion_counts", {}).items())),
        "",
        "## Confusion (rows human 0–4, columns JEV rounded 0–4)",
        "",
        *[" ".join(f"{c:3d}" for c in row) for row in metrics["confusion"]],
        "",
        "Providers: " + ", ".join(f"{k} {v}" for k, v in sorted(metrics["providers"].items())),
        "",
        "## Pairs",
        "",
        "| Task | Human | JEV | RS-v1 − 1 |",
        "|---|---|---|---|",
        *[f"| {p['ref'][:8]} | {p['human']} | {p['jev']:.2f} | {p['rs1']} |" for p in pairs],
        "",
    ]
    return "\n".join(lines), {"metrics": metrics, "exclusions": exclusions}


# ---- HTTP -------------------------------------------------------------------------------------


class Api:
    def __init__(self, url: str, token: Optional[str], client: Optional[httpx.Client] = None) -> None:
        self.client = client or httpx.Client(base_url=url, timeout=30.0)
        self.headers = {"X-Ingest-Token": token} if token else {}

    def _get(self, path: str, **params) -> dict:
        response = self.client.get(path, params=params, headers=self.headers)
        response.raise_for_status()
        return response.json()

    def all_root_tasks(self) -> list[dict]:
        items, page = [], 1
        while True:
            body = self._get("/api/tasks", days=3650, root_only="true", page=page, page_size=200)
            items += body["items"]
            if page * body["page_size"] >= body["total"]:
                return items
            page += 1

    def detail(self, ref: str, include_prompt: bool = False) -> dict:
        return self._get(f"/api/tasks/{ref}", include_prompt=str(include_prompt).lower())

    def label(self, ref: str, labeler: str, label: Optional[int], note: Optional[str]) -> None:
        body = {"labeler": labeler, "rubric_version": RUBRIC, "note": note}
        body.update({"label": label} if label is not None else {"skipped": True})
        self.client.post(f"/api/tasks/{ref}/labels", json=body, headers=self.headers).raise_for_status()

    def evaluate(self, refs: list[str]) -> dict:
        response = self.client.post("/api/tasks/evaluate", json={"task_refs": refs}, headers=self.headers)
        response.raise_for_status()
        return response.json()

    def run(self, run_id: str) -> dict:
        return self._get(f"/api/tasks/evaluator-runs/{run_id}")


# ---- commands -----------------------------------------------------------------------------------


def cmd_select(api: Api, args) -> int:
    now = datetime.now(timezone.utc)
    candidates = [c for c in api.all_root_tasks() if is_eligible(c, now)]
    sample = select_sample(candidates, args.n, args.seed)
    out = Path(args.out) if args.out else DEFAULT_DIR / f"sample-seed{args.seed}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(sample, indent=2), encoding="utf-8")
    print(f"eligible {len(candidates)}; sampled {len(sample['task_refs'])} -> {out}")
    return 0


def _progress(api: Api, refs: list[str], labeler: str) -> dict[str, str]:
    return {ref: labelling_progress(api.detail(ref).get("evaluations", []), labeler) for ref in refs}


def cmd_label(api: Api, args, ask: Callable[[str], str] = input) -> int:
    sample = json.loads(Path(args.sample).read_text(encoding="utf-8"))
    progress = _progress(api, sample["task_refs"], args.labeler)
    todo = to_label(sample["task_refs"], progress, sample["seed"], args.revisit_skipped)
    print(f"{len(todo)} task(s) to label; keys: 0-4 label, s skip, q quit")
    now = datetime.now(timezone.utc)
    for ref in todo:
        detail = api.detail(ref, include_prompt=True)
        expires = _parse(detail.get("prompt_expires_at"))
        if detail.get("prompt_text") is None:
            print(f"[{ref[:8]}] prompt expired or purged; skipping display")
            continue
        if expires and expires < now + timedelta(hours=48):
            print(f"[{ref[:8]}] warning: prompt expires within 48 h")
        print(f"\n--- project: {safe_terminal(detail['project_name'])} ---\n{safe_terminal(detail['prompt_text'])}\n")
        while True:
            key = ask("difficulty 0-4 / s / q > ").strip().lower()
            if key == "q":
                return 0
            if key == "s" or key in {"0", "1", "2", "3", "4"}:
                break
        note = ask("note (optional) > ").strip() or None if args.notes else None
        api.label(ref, args.labeler, None if key == "s" else int(key), note)
    return 0


def cmd_score(api: Api, args, wait: Callable[[float], None] = time.sleep) -> int:
    sample = json.loads(Path(args.sample).read_text(encoding="utf-8"))
    progress = _progress(api, sample["task_refs"], args.labeler)
    missing = missing_labels(sample["task_refs"], progress)
    if missing and not args.allow_unlabelled:
        print(f"refusing: {len(missing)} sampled task(s) have no row from labeller {args.labeler}", file=sys.stderr)
        return 2
    refs = sample["task_refs"]
    for start in range(0, len(refs), 100):
        body = api.evaluate(refs[start:start + 100])
        if body.get("retry_not_before"):
            print(f"providers cooling down; retry after {body['retry_not_before']}", file=sys.stderr)
            return 3
        if not body.get("run_id"):
            continue
        while (run := api.run(body["run_id"]))["status"] not in TERMINAL:
            wait(2.0)
        if run["stop_reason"] == "deferred_rate_limited":
            print(f"deferred; rerun after {run['retry_not_before']}", file=sys.stderr)
            return 3
        if run["status"] != "done":
            print(f"run {run['status']}: {run['stop_reason']}", file=sys.stderr)
            return 4
    return 0


def cmd_report(api: Api, args) -> int:
    sample = json.loads(Path(args.sample).read_text(encoding="utf-8"))
    details = {ref: api.detail(ref) for ref in sample["task_refs"]}
    markdown, _ = build_report(sample, details, args.labeler)
    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="jev_pilot")
    parser.add_argument("--url", default="http://127.0.0.1:8100")
    sub = parser.add_subparsers(dest="command", required=True)
    select = sub.add_parser("select")
    select.add_argument("--n", type=int, default=50)
    select.add_argument("--seed", type=int, required=True)
    select.add_argument("--out")
    for name in ("label", "score", "report"):
        p = sub.add_parser(name)
        p.add_argument("--sample", required=True)
        p.add_argument("--labeler", required=True)
        if name == "label":
            p.add_argument("--notes", action="store_true")
            p.add_argument("--revisit-skipped", action="store_true")
        if name == "score":
            p.add_argument("--allow-unlabelled", action="store_true")
        if name == "report":
            p.add_argument("--out")
    args = parser.parse_args(argv)
    api = Api(args.url, os.environ.get("INGEST_TOKEN"))
    return {"select": cmd_select, "label": cmd_label, "score": cmd_score, "report": cmd_report}[args.command](api, args)


if __name__ == "__main__":
    sys.exit(main())
