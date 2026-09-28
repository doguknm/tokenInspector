"""Pilot CLI and statistics (AC8a-AC8d)."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import jev_pilot
import pilot_metrics as pm

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


# --- statistics ------------------------------------------------------------------------------


def test_ranks_spearman_kappa_and_rounding():
    assert pm.average_ranks([10, 20, 20, 30]) == [1.0, 2.5, 2.5, 4.0]
    assert pm.spearman([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert pm.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert pm.spearman([1, 1, 1], [1, 2, 3]) is None  # constant -> undefined
    assert pm.weighted_kappa([0, 1, 2, 3, 4], [0, 1, 2, 3, 4]) == pytest.approx(1.0)
    assert pm.weighted_kappa([2, 2, 2], [2, 2, 2]) is None  # expected disagreement 0
    assert pm.round_half_up(2.5) == 3 and pm.round_half_up(1.49) == 1 and pm.round_half_up(0.5) == 1
    assert pm.confusion([0, 1], [1, 1])[0][1] == 1


@pytest.mark.parametrize(
    ("n", "rho", "rho_rs1", "kappa", "expected"),
    [
        (39, 0.9, 0.1, 0.9, "INCONCLUSIVE — insufficient evidence"),
        (40, None, 0.1, 0.9, "INCONCLUSIVE — insufficient evidence"),
        (40, 0.9, 0.1, None, "INCONCLUSIVE — insufficient evidence"),
        (40, 0.6, 0.45, 0.5, "PASS"),
        (40, 0.6, 0.55, 0.5, "FAIL"),
        (40, 0.45, 0.1, 0.5, "FAIL"),
        (40, 0.6, 0.1, 0.39, "FAIL"),
    ],
)
def test_verdict_rules(n, rho, rho_rs1, kappa, expected):
    assert pm.verdict(n, rho, rho_rs1, kappa) == expected


def test_bootstrap_unavailable_when_most_resamples_are_undefined():
    constant = [{"human": 2, "jev": 2.0}] * 50
    result = pm.bootstrap(constant, lambda s: pm.spearman([x["jev"] for x in s], [x["human"] for x in s]), seed=1)
    assert result["available"] is False and result["dropped"] == pm.BOOTSTRAP_RESAMPLES


# --- select ------------------------------------------------------------------------------------


def _candidate(i, level, **extra):
    item = {"task_ref": f"{i:032x}", "hierarchy_status": "root", "completion": "next_task", "prompt_state": "retained",
            "prompt_expires_at": (NOW + timedelta(days=20)).isoformat(), "start_complexity": level,
            "start_complexity_method": "request-shape-v1", "project_name": "hermes"}
    item.update(extra)
    return item


@pytest.mark.parametrize(
    ("extra", "eligible"),
    [
        ({}, True),
        ({"hierarchy_status": "unknown"}, False),
        ({"completion": "open"}, False),
        ({"completion": "inferred"}, True),
        ({"prompt_state": "expired"}, False),
        ({"prompt_expires_at": (NOW + timedelta(days=4)).isoformat()}, False),
        ({"start_complexity": None}, False),
        ({"start_complexity_method": "request-shape-v1-inferred"}, False),
        ({"project_name": "token-inspector"}, False),
    ],
)
def test_eligibility(extra, eligible):
    assert jev_pilot.is_eligible(_candidate(1, 3, **extra), NOW) is eligible


def test_stratified_selection_is_deterministic_and_fills_shortfall():
    pool = [_candidate(i, 1 + i % 5) for i in range(40)] + [_candidate(100 + i, 5) for i in range(30)]
    pool = [c for c in pool if not (c["start_complexity"] == 2 and int(c["task_ref"], 16) > 6)]  # stratum 2 small
    first = jev_pilot.select_sample(pool, 50, seed=7)
    assert first == jev_pilot.select_sample(list(reversed(pool)), 50, seed=7)
    assert first != jev_pilot.select_sample(pool, 50, seed=8)
    assert len(first["task_refs"]) == 50 and len(set(first["task_refs"])) == 50
    assert first["strata"]["2"] == {"population": 2, "sample": 2}  # smaller than allocation: all of it
    assert all(v["sample"] >= min(10, v["population"]) for v in first["strata"].values())
    assert sum(first["completion_counts"].values()) == 50
    assert "prompt" not in json.dumps(first).replace("prompt retained", "")


# --- label / score ------------------------------------------------------------------------------


def test_safe_terminal_escapes_controls_and_bidi():
    assert jev_pilot.safe_terminal("a\x1b[31mred\x07\nb\tc") == "a\\x1b[31mred\\x07\nb\tc"
    assert jev_pilot.safe_terminal("x‮y⁦z") == "x\\u202ey\\u2066z"


def test_progress_resume_and_other_labellers():
    evals = [{"evaluator": "human", "labeler": "ann", "status": "ok", "rubric_version": "difficulty-v0"},
             {"evaluator": "human", "labeler": "bob", "status": "skipped", "rubric_version": "difficulty-v0"}]
    assert jev_pilot.labelling_progress(evals, "ann") == "labelled"
    assert jev_pilot.labelling_progress(evals, "bob") == "skipped_by_labeler"
    assert jev_pilot.labelling_progress(evals, "cem") == "not_labelled"
    progress = {"r1": "labelled", "r2": "skipped_by_labeler", "r3": "not_labelled"}
    assert jev_pilot.to_label(["r1", "r2", "r3"], progress, 1, revisit_skipped=False) == ["r3"]
    assert sorted(jev_pilot.to_label(["r1", "r2", "r3"], progress, 1, revisit_skipped=True)) == ["r2", "r3"]
    assert jev_pilot.missing_labels(["r1", "r2", "r3"], progress) == ["r3"]


class FakeApi:
    def __init__(self, details):
        self.details = details
        self.labels = []
        self.evaluated = []

    def detail(self, ref, include_prompt=False):
        detail = dict(self.details[ref])
        if not include_prompt:
            detail.pop("prompt_text", None)
        return detail

    def label(self, ref, labeler, label, note):
        self.labels.append((ref, labeler, label, note))
        status = "skipped" if label is None else "ok"
        self.details[ref].setdefault("evaluations", []).insert(
            0, {"evaluator": "human", "labeler": labeler, "status": status, "label": label,
                "rubric_version": "difficulty-v0"})

    def evaluate(self, refs):
        self.evaluated.append(refs)
        return {"run_id": "run", "retry_not_before": None}

    def run(self, run_id):
        return {"status": "done", "stop_reason": None, "retry_not_before": None}


def _sample_file(tmp_path, refs):
    path = tmp_path / "sample.json"
    path.write_text(json.dumps({"seed": 3, "task_refs": refs, "strata": {}, "completion_counts": {}}),
                    encoding="utf-8")
    return path


def test_label_is_blind_persists_skips_and_resumes(tmp_path):
    refs = ["r1", "r2", "r3"]
    details = {r: {"project_name": "hermes", "prompt_text": f"prompt {r} \x1b[2J",
                   "prompt_expires_at": (datetime.now(timezone.utc) + timedelta(days=9)).isoformat(),
                   "start_complexity": 3, "evaluations": []} for r in refs}
    api = FakeApi(details)
    path = _sample_file(tmp_path, refs)
    keys = iter(["9", "2", "s", "q"])
    args = SimpleNamespace(sample=str(path), labeler="ann", notes=False, revisit_skipped=False)
    jev_pilot.cmd_label(api, args, ask=lambda _prompt: next(keys))
    assert [(label) for _, _, label, _ in api.labels] == [2, None]
    keys = iter(["4"])
    jev_pilot.cmd_label(api, args, ask=lambda _prompt: next(keys))  # resumes with the one left
    assert len(api.labels) == 3 and {r for r, *_ in api.labels} == set(refs)


def test_score_refuses_until_this_labeller_has_a_row_for_every_task(tmp_path):
    details = {"r1": {"evaluations": [{"evaluator": "human", "labeler": "bob", "status": "ok",
                                       "rubric_version": "difficulty-v0"}]}}
    api = FakeApi(details)
    args = SimpleNamespace(sample=str(_sample_file(tmp_path, ["r1"])), labeler="ann", allow_unlabelled=False)
    assert jev_pilot.cmd_score(api, args, wait=lambda s: None) == 2 and api.evaluated == []
    args.labeler = "bob"
    assert jev_pilot.cmd_score(api, args, wait=lambda s: None) == 0 and api.evaluated == [["r1"]]


# --- report ------------------------------------------------------------------------------------------


def _detail(human=None, human_status="ok", jev=None, jev_status="ok", rs1=3, state="retained", other=False):
    evaluations = []
    if human is not None or human_status == "skipped":
        evaluations.append({"evaluator": "human", "labeler": "ann", "status": human_status, "label": human,
                            "rubric_version": "difficulty-v0"})
    if other:
        evaluations.append({"evaluator": "human", "labeler": "bob", "status": "ok", "label": 1,
                            "rubric_version": "difficulty-v0"})
    if jev is not None or jev_status != "ok":
        evaluations.append({"evaluator": "jev", "status": jev_status, "raw_score": jev, "confidence": 0.8,
                            "provider_used": "typesafe-ai", "cost_usd": 0.00002, "rubric_version": "difficulty-v0"})
    return {"start_complexity": rs1, "start_complexity_method": "request-shape-v1" if rs1 else None,
            "prompt_state": state, "evaluations": evaluations}


def test_report_exclusions_and_inconclusive_below_minimum():
    details = {
        "a" * 32: _detail(human=2, jev=2.2, other=True),
        "b" * 32: _detail(human=None),
        "c" * 32: _detail(human_status="skipped"),
        "d" * 32: _detail(human=1, jev=None, jev_status="rate_limited"),
        "e" * 32: _detail(human=1, rs1=None),
        "f" * 32: _detail(human=1, state="purged"),
    }
    sample = {"seed": 1, "task_refs": list(details), "strata": {"3": {"population": 6, "sample": 6}},
              "completion_counts": {"next_task": 6}}
    markdown, data = jev_pilot.build_report(sample, details, "ann")
    assert data["exclusions"] == {"not_labelled": 1, "skipped_by_labeler": 1, "jev_rate_limited": 1,
                                  "rs1_missing": 1, "prompt_expired_before_scoring": 1}
    assert data["metrics"]["n"] == 1
    assert "INCONCLUSIVE" in markdown and "other labellers present: 1" in markdown
    assert "aaaaaaaa" in markdown and "a" * 32 not in markdown


def test_report_pass_on_a_strong_cohort():
    details, levels = {}, [0, 1, 2, 3, 4] * 9
    for i, level in enumerate(levels):
        details[f"{i:032x}"] = _detail(human=level, jev=level + 0.1, rs1=((level + 2) % 5) + 1)
    sample = {"seed": 4, "task_refs": list(details), "strata": {}, "completion_counts": {}}
    markdown, data = jev_pilot.build_report(sample, details, "ann")
    assert data["metrics"]["n"] == 45
    assert data["metrics"]["verdict"] == "PASS" and "## Verdict: PASS" in markdown


# --- code-review r1: P3-F2, P6-F4 ---------------------------------------------------------------------------


def test_unavailable_prompt_is_persisted_as_a_skip(tmp_path):
    details = {"r1": {"project_name": "hermes", "prompt_text": None, "evaluations": []}}
    api = FakeApi(details)
    args = SimpleNamespace(sample=str(_sample_file(tmp_path, ["r1"])), labeler="ann", notes=False,
                           revisit_skipped=False)
    jev_pilot.cmd_label(api, args, ask=lambda _prompt: "q")
    assert api.labels == [("r1", "ann", None, None)]
    score = SimpleNamespace(sample=args.sample, labeler="ann", allow_unlabelled=False)
    assert jev_pilot.cmd_score(api, score, wait=lambda s: None) == 0  # no longer stalls on it


def test_label_output_is_blind_to_scores(tmp_path, capsys):
    details = {"r1": {"project_name": "hermes", "prompt_text": "Refactor the parser",
                      "prompt_expires_at": (datetime.now(timezone.utc) + timedelta(days=9)).isoformat(),
                      "start_complexity": 4, "start_complexity_method": "request-shape-v1",
                      "jev": {"raw_score": 3.77, "display_score": 4.77, "confidence": 0.61}, "total_tokens": 98765,
                      "cost_usd": 0.4321, "evaluations": []}}
    api = FakeApi(details)
    args = SimpleNamespace(sample=str(_sample_file(tmp_path, ["r1"])), labeler="ann", notes=False,
                           revisit_skipped=False)
    jev_pilot.cmd_label(api, args, ask=lambda _prompt: "2")
    out = capsys.readouterr().out
    assert "Refactor the parser" in out
    for leaked in ("3.77", "4.77", "0.61", "98765", "0.4321", "request-shape", "C4"):
        assert leaked not in out


class DeferringApi(FakeApi):
    def __init__(self, details, request_time=False):
        super().__init__(details)
        self.request_time = request_time

    def evaluate(self, refs):
        if self.request_time:
            return {"run_id": None, "retry_not_before": "2026-09-27T13:00:00Z"}
        return {"run_id": "run", "retry_not_before": None}

    def run(self, run_id):
        return {"status": "stopped", "stop_reason": "deferred_rate_limited",
                "retry_not_before": "2026-09-27T13:05:00Z"}


@pytest.mark.parametrize("request_time", [True, False])
def test_score_exits_non_zero_when_deferred(tmp_path, capsys, request_time):
    details = {"r1": {"evaluations": [{"evaluator": "human", "labeler": "ann", "status": "ok",
                                       "rubric_version": "difficulty-v0"}]}}
    args = SimpleNamespace(sample=str(_sample_file(tmp_path, ["r1"])), labeler="ann", allow_unlabelled=False)
    assert jev_pilot.cmd_score(DeferringApi(details, request_time), args, wait=lambda s: None) == 3
    assert "2026-09-27T13:0" in capsys.readouterr().err


# --- O10 Q3: select asks only for allowlisted projects ------------------------------------------------


def test_select_requests_allowed_only():
    import httpx

    seen = []

    def handler(request):
        seen.append(request)
        page = int(request.url.params["page"])
        return httpx.Response(200, json={"items": [{"task_ref": f"r{page}"}], "total": 2, "page_size": 1})

    client = httpx.Client(base_url="http://pilot.test", transport=httpx.MockTransport(handler))
    items = jev_pilot.Api("http://pilot.test", "tok-123", client=client).all_root_tasks()
    assert [i["task_ref"] for i in items] == ["r1", "r2"]
    assert len(seen) == 2
    for request in seen:
        assert request.url.path == "/api/tasks"
        assert request.url.params["allowed_only"] == "true" and request.url.params["root_only"] == "true"
        assert request.headers["X-Ingest-Token"] == "tok-123"
