# Tests for the Milestone 2 evidence scripts (scripts/latency_test.py, scripts/benchmark_models.py).
# No AWS: bedrock.converse is replaced by fake_bedrock (tests/test_model_routing.py). Reports go to tmp_path.
# Run with: pytest

import json

import pytest

from scripts import benchmark_models, latency_test
from scripts.m2 import cases, report
from tests.test_assess import PATIENT, client, motor_inputs, queued_jobs  # noqa: F401  (fixtures)
from tests.test_model_routing import fake_bedrock, use_bedrock  # noqa: F401  (fixtures)


def answer(risk_level, **extra):
    return json.dumps({"risk_level": risk_level, "confidence_score": 0.95, "explanation": "Worth reviewing.", **extra})


# ---------- shared helpers ----------

@pytest.mark.parametrize("values, pct, expected", [
    ([], 95, None),
    ([5], 95, 5),
    (list(range(1, 21)), 95, 19),  # nearest rank: 19th of 20
    (list(range(1, 21)), 50, 10),
    (list(range(1, 101)), 95, 95),
])
def test_percentile(values, pct, expected):
    assert report.percentile(values, pct) == expected


def test_triage_cases_are_complete():
    data = cases.triage_cases()
    assert len({case["id"] for case in data}) == len(data) == 24
    for level in ("low", "medium", "high", "critical"):
        assert sum(1 for case in data if case["expected_risk"] == level) == 6
    assert {case["country"] for case in data} == {"US", "MX"}


def test_vitals_cases_cover_every_level():
    assert {cases.vitals_rule_risk(case["data"]) for case in cases.VITALS_CASES} == {"low", "medium", "high", "critical"}


def test_synthetic_photo_is_a_readable_900px_png():
    from app.shared.images import decode_image

    raw, image_format = decode_image(cases.synthetic_skin_photo())
    assert image_format == "png" and raw[16:24] == (900).to_bytes(4, "big") * 2  # IHDR width and height


def test_risk_distance():
    assert cases.risk_distance("high", "critical") == -1  # under-triage
    assert cases.risk_distance("critical", "medium") == 2


# ---------- latency test ----------

def test_latency_refuses_stub_answers(tmp_path, capsys):
    assert latency_test.main(["--out-dir", str(tmp_path)]) == 2
    assert "USE_BEDROCK is false" in capsys.readouterr().out


def test_latency_motor_mode_passes_and_writes_reports(use_bedrock, fake_bedrock, tmp_path):
    code = latency_test.main(["--runs", "4", "--warmup", "0", "--motors", "triage,vitals", "--out-dir", str(tmp_path)])

    assert code == 0  # fake Bedrock answers in no time
    (json_file,) = tmp_path.glob("latency-*.json")
    data = json.loads(json_file.read_text())
    assert data["passed"] is True
    assert set(data["results"]) == {"triage", "vitals", "vitals_alert"}
    assert data["results"]["triage"]["count"] == 4 and data["results"]["triage"]["verdict"]["target_ms"] == 3000
    assert data["results"]["vitals_alert"]["verdict"]["target_ms"] == 1000
    assert list(tmp_path.glob("latency-*.md"))


def test_latency_fails_when_triage_is_too_slow(use_bedrock, fake_bedrock, tmp_path, monkeypatch):
    clock = iter(range(0, 10**9, 4000))  # every reading of the clock moves 4 s
    monkeypatch.setattr(latency_test, "now_ms", lambda: next(clock))
    assert latency_test.main(["--runs", "2", "--warmup", "0", "--motors", "triage", "--out-dir", str(tmp_path)]) == 1


def test_latency_counts_a_failed_motor(use_bedrock, fake_bedrock, tmp_path):
    fake_bedrock.failing = {"haiku", "sonnet", "nova_pro", "llama"}  # triage answers "ai_unavailable"
    code = latency_test.main(["--runs", "2", "--warmup", "0", "--motors", "triage", "--out-dir", str(tmp_path)])
    data = json.loads(next(tmp_path.glob("latency-*.json")).read_text())
    assert code == 1 and data["results"]["triage"]["failures"] == 2


def test_latency_http_mode(client, fake_bedrock, use_bedrock, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(latency_test, "make_client", lambda: client)  # TestClient is an httpx.Client
    token = PATIENT["Authorization"].split()[1]

    code = latency_test.main(["--mode", "http", "--url", "http://testserver", "--token", token, "--runs", "2",
                              "--warmup", "0", "--motors", "triage,vitals", "--out-dir", str(tmp_path)])

    data = json.loads(next(tmp_path.glob("latency-*.json")).read_text())
    assert code == 0, data["results"]
    assert data["results"]["triage"]["failures"] == 0 and data["results"]["vitals"]["count"] == 2


def test_latency_http_mode_needs_url_and_token(tmp_path):
    with pytest.raises(SystemExit):
        latency_test.main(["--mode", "http", "--out-dir", str(tmp_path)])


# ---------- benchmark ----------

def test_candidates_follow_the_routing():
    assert benchmark_models.CANDIDATES == {
        "triage": ["haiku", "sonnet", "nova_pro", "llama"],
        "visual": ["haiku", "sonnet", "nova_pro"],
        "vitals": ["haiku", "nova_lite"],
    }


def test_benchmark_dry_run_calls_nothing(fake_bedrock, capsys):
    assert benchmark_models.main(["--dry-run"]) == 0
    assert fake_bedrock.calls == []
    assert "121 Bedrock calls in total" in capsys.readouterr().out


def test_cost_scenarios_math():
    per_call = {
        "triage": {"haiku": 0.001, "sonnet": 0.02, "nova_pro": 0.01},
        "visual": {"haiku": 0.003, "sonnet": 0.03, "nova_pro": 0.01},
        "vitals": {"haiku": 0.001, "nova_lite": 0.0001},
    }
    scenarios = benchmark_models.cost_scenarios(per_call, benchmark_models.DEFAULT_PROFILE)
    # normal: 48 triage calls x (0.001 + 0.2 x 0.02) + 8 images x (0.003 + 0.2 x 0.03) + 60 x 0.001, then +20%
    expected = (48 * (0.001 + 0.2 * 0.02) + 8 * (0.003 + 0.2 * 0.03) + 60 * 0.001) * 1.2  # 0.4464
    assert scenarios["normal"]["total_usd"] == round(expected, 4) and scenarios["normal"]["passed"]
    outage = (48 * 0.01 + 8 * 0.01 + 60 * 0.0001) * 1.2
    assert scenarios["provider_outage"]["total_usd"] == round(outage, 4)
    assert scenarios["elevated_escalation"]["total_usd"] > scenarios["normal"]["total_usd"]


def test_only_the_agreed_profile_decides_the_result(fake_bedrock, tmp_path, monkeypatch):
    over = {"normal": {"passed": True}, "elevated_escalation": {"passed": False}, "provider_outage": {"passed": True}}
    monkeypatch.setattr(benchmark_models, "cost_scenarios", lambda per_call, profile: {
        name: {"escalation_rate": 0, "models": {"triage": "haiku", "visual": "haiku", "vitals": "haiku"},
               "per_workflow_usd": {}, "subtotal_usd": 0, "total_usd": 0.1, "missing": [], **value}
        for name, value in over.items()
    })
    fake_bedrock.replies = {}
    assert benchmark_models.main(["--motors", "vitals", "--models", "haiku", "--limit", "1", "--out-dir", str(tmp_path)]) == 0


def test_cost_over_target_fails():
    per_call = {"triage": {"haiku": 0.02, "sonnet": 0.2, "nova_pro": 0.02}, "visual": {"haiku": 0, "sonnet": 0, "nova_pro": 0},
                "vitals": {"haiku": 0, "nova_lite": 0}}
    assert not benchmark_models.cost_scenarios(per_call, benchmark_models.DEFAULT_PROFILE)["normal"]["passed"]


def test_unmeasured_model_makes_the_scenario_unknown():
    scenarios = benchmark_models.cost_scenarios({"triage": {"haiku": 0.001}}, benchmark_models.DEFAULT_PROFILE)
    assert scenarios["normal"]["total_usd"] is None and not scenarios["normal"]["passed"]


def test_benchmark_run_with_fake_bedrock(fake_bedrock, tmp_path):
    fake_bedrock.replies = {"haiku": [answer("low", finding_type="cold"), answer("critical", finding_type="x")]}

    code = benchmark_models.main(["--limit", "2", "--image-repeats", "1", "--out-dir", str(tmp_path)])

    data = json.loads(next(tmp_path.glob("benchmark-*.json")).read_text())
    triage = data["summaries"]["triage"]
    assert set(triage) == {"haiku", "sonnet", "nova_pro", "llama"}
    assert triage["haiku"]["calls"] == 2 and triage["haiku"]["valid_pct"] == 100.0
    assert triage["haiku"]["exact_pct"] == 50.0  # T01 low -> low (exact), T02 low -> critical
    assert data["cost_per_image"]["blended_usd"] is not None
    assert set(data["scenarios"]) == {"normal", "elevated_escalation", "provider_outage"}
    # the fake reports 23,430 input tokens per call (the proposal's six-turn conversation), so the cost is far
    # over USD 0.50: the run must fail
    assert code == 1 and data["passed"] is False
    assert list(tmp_path.glob("benchmark-*.md"))


def test_benchmark_counts_errors_and_never_crashes(fake_bedrock, tmp_path):
    fake_bedrock.failing = {"llama"}
    benchmark_models.main(["--motors", "triage", "--limit", "2", "--out-dir", str(tmp_path)])
    data = json.loads(next(tmp_path.glob("benchmark-*.json")).read_text())
    assert data["summaries"]["triage"]["llama"]["errors"] == 2
    assert data["passed"] is False  # a model that never answered fails the run


def test_benchmark_flags_wrong_vitals_wording(fake_bedrock, tmp_path):
    fake_bedrock.replies = {"haiku": [json.dumps({"explanation": "Your readings are normal.", "finding_type": "spo2"})] * 8}
    benchmark_models.main(["--motors", "vitals", "--models", "haiku", "--out-dir", str(tmp_path)])
    summary = json.loads(next(tmp_path.glob("benchmark-*.json")).read_text())["summaries"]["vitals"]["haiku"]
    assert summary["explanation_ok_pct"] == 25.0  # "normal" is fine only for the 2 low-risk cases of 8
