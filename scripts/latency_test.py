# Milestone 2 latency test: how fast each motor answers, against the spec targets.
#   Triage:             p95 under 3 s end to end (Proposal Week 3; Spec, Latency targets)
#   Vitals alert:       p95 under 1 s (the rules decide the alert; the AI only explains it afterwards)
#   Vitals explanation, visual: reported against their time limits in app/config.py (no spec target)
#
# Uses the synthetic cases in scripts/m2/ only: no real patient data.
#
# Two modes:
#   motor (default)  calls the motors in this process, through the real routing (default model, escalation,
#                    fallbacks), so it measures the motors and Bedrock without the network hop to the server.
#                    Needs USE_BEDROCK=true and AWS access (same as scripts/test_bedrock_models.py).
#   http             calls POST /ai/assess on a running server, end to end (gateway, orchestrator, motors).
#                    Needs a patient access token whose user has given the consents:
#                    LATENCY_TEST_TOKEN=<token> python -m scripts.latency_test --mode http --url https://ai.dev.vitals-signs.com
#
# Examples:
#   python -m scripts.latency_test                          20 runs per motor, motor mode
#   python -m scripts.latency_test --runs 50 --motors triage,vitals
#   python -m scripts.latency_test --images-dir ./test-photos   de-identified test photos instead of the synthetic one
#
# Writes reports/latency-<time>.json and .md. Exit code 0 when the targets are met, 1 otherwise.
# Cost: one triage run is about USD 0.003-0.03 with Bedrock (more when Sonnet escalates). 20 runs of each motor
# cost well under USD 1.

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

import httpx

from app import config
from app.motors.triage import logic as triage_logic
from app.motors.visual import logic as visual_logic
from app.motors.vitals import logic as vitals_logic
from scripts.m2 import cases, report

MOTORS = ["triage", "visual", "vitals"]

# p95 targets in milliseconds. None = no spec target: reported against the motor's time limit instead.
TARGETS_MS = {
    "triage": 3000,  # Spec: conversational triage < 3 s end to end
    "vitals_alert": 1000,  # Spec: vital-sign alert < 1 s
    "vitals": None,  # explanation: background, after the alert
    "visual": None,  # no spec target (README open TODO)
}


def now_ms() -> float:
    return time.perf_counter() * 1000


# ---------- one call per motor ----------

def call_motor(motor: str, item: dict) -> dict:
    """One in-process motor call. Returns {"ms", "ok", "model", "escalated", "fallback", "risk_level"}."""
    started = now_ms()
    if motor == "triage":
        output = triage_logic.analyze({"message": item["message"]}, country=item["country"])
    elif motor == "visual":
        output = visual_logic.analyze({"image": item["image"]}, country=item.get("country", "US"))
    else:
        output = vitals_logic.analyze(dict(item["data"]), country=item["country"])
    ms = now_ms() - started
    return {
        "ms": ms,
        "ok": output.finding_type != "ai_unavailable",  # no AI answer: counted as a failure, not a latency
        "model": output.model_name,
        "escalated": output.escalated,
        "fallback": output.fallback_used,
        "risk_level": output.risk_level,
    }


def call_http(client: httpx.Client, url: str, token: str, motor: str, item: dict) -> dict:
    """One POST /ai/assess with only this motor's input. Returns the same fields as call_motor."""
    body: dict = {"context": {"country": item.get("country", "US"), "app_version": "latency-test"}}
    if motor == "triage":
        body["symptoms"] = item["message"]
    elif motor == "visual":
        body["image"] = item["image"]
    else:
        body["vitals"] = {**item["data"], "client_reading_id": f"latency-{item['id']}-{time.time_ns()}"}
    started = now_ms()
    response = client.post(f"{url.rstrip('/')}/ai/assess", json=body, headers={"Authorization": f"Bearer {token}"})
    ms = now_ms() - started
    if response.status_code != 200:
        return {"ms": ms, "ok": False, "error": f"HTTP {response.status_code}: {response.text[:200]}"}
    data = response.json()
    return {
        "ms": ms,
        "ok": motor in data.get("motors_used", []),
        "risk_level": data.get("risk_level"),
        "error": None if motor in data.get("motors_used", []) else f"motor failed: {data.get('motors_failed')}",
    }


def time_vitals_alert(item: dict) -> float:
    """Milliseconds the vitals rules take to decide the alert level (no AI call)."""
    started = now_ms()
    cases.vitals_rule_risk(item["data"])
    return now_ms() - started


# ---------- the run ----------

def inputs_for(motor: str, images_dir: Optional[Path]) -> List[dict]:
    if motor == "triage":
        return cases.triage_cases()
    if motor == "visual":
        return [{**photo, "country": "US"} for photo in cases.photos(images_dir)]
    return cases.VITALS_CASES


def run_motor(motor: str, runs: int, warmup: int, call: Callable[[str, dict], dict], items: List[dict]) -> dict:
    """warmup calls (not counted, the first Bedrock call can be slow), then `runs` calls cycling through items."""
    for index in range(warmup):
        try:
            call(motor, items[index % len(items)])
        except Exception:
            pass
    results = []
    for index in range(runs):
        item = items[index % len(items)]
        try:
            result = call(motor, item)
        except Exception as error:  # a crash is a failed run, not the end of the test
            result = {"ms": None, "ok": False, "error": f"{type(error).__name__}: {error}"}
        result["case"] = item["id"]
        results.append(result)
        status = "ok" if result["ok"] else "FAIL"
        ms = "-" if result.get("ms") is None else f"{result['ms']:.0f} ms"
        print(f"  {motor:<7} {index + 1:>3}/{runs}  {item['id']:<16} {ms:>9}  {status}")
    latencies = [result["ms"] for result in results if result["ok"]]
    return {
        **report.summary_ms(latencies),
        "runs": runs,
        "failures": sum(1 for result in results if not result["ok"]),
        "escalated": sum(1 for result in results if result.get("escalated")),
        "fallbacks": sum(1 for result in results if result.get("fallback")),
        "models": sorted({result.get("model") for result in results if result.get("model")}),
        "calls": results,
    }


def verdict(name: str, stats: dict) -> dict:
    """{"target_ms", "passed"} for one measured line. No target -> compared with the motor's time limit."""
    target = TARGETS_MS.get(name)
    limit_ms = config.MOTOR_TIMEOUTS[name] * 1000 if target is None and name in config.MOTOR_TIMEOUTS else None
    reference = target if target is not None else limit_ms
    p95 = stats.get("p95_ms")
    passed = p95 is not None and reference is not None and p95 < reference and stats.get("failures", 0) == 0
    return {"target_ms": target, "time_limit_ms": limit_ms, "passed": passed}


def markdown(results: Dict[str, dict], mode: str, runs: int, all_passed: bool) -> str:
    lines = [
        "# Milestone 2 latency test",
        "",
        f"Mode: **{mode}** · runs per motor: **{runs}** · models: Bedrock (`USE_BEDROCK={config.USE_BEDROCK}`) · "
        "inputs: synthetic test cases (no patient data)",
        "",
        "| Measured | p50 | p95 | Max | Failures | Target (p95) | Result |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, stats in results.items():
        check = stats["verdict"]
        target = (
            f"< {check['target_ms']:,} ms" if check["target_ms"] is not None
            else f"time limit {check['time_limit_ms']:,.0f} ms (no spec target)" if check["time_limit_ms"] else "-"
        )
        lines.append(
            f"| {name} | {report.fmt_ms(stats['p50_ms'])} | {report.fmt_ms(stats['p95_ms'])} | "
            f"{report.fmt_ms(stats['max_ms'])} | {stats.get('failures', 0)}/{stats.get('runs', stats['count'])} | "
            f"{target} | {'PASS' if check['passed'] else 'FAIL'} |"
        )
    lines += [
        "",
        f"**Overall: {'PASS' if all_passed else 'FAIL'}** (triage p95 < 3 s and vitals alert p95 < 1 s, with no failed runs).",
        "",
        "Notes:",
        "- `vitals_alert` is the rule decision that raises the critical alert; the AI explanation (`vitals`) comes after it "
        "and does not delay the alert. On the phone the alert runs on-device.",
        "- `motor` mode leaves out the network between the app and the server; `http` mode measures the full round trip.",
        "- Escalations to Sonnet and fallbacks are included in the timings (counts in the JSON report).",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Milestone 2 latency test (p50 / p95 per motor).")
    parser.add_argument("--mode", choices=["motor", "http"], default="motor")
    parser.add_argument("--runs", type=int, default=20, help="measured calls per motor (default 20)")
    parser.add_argument("--warmup", type=int, default=1, help="calls per motor before measuring, not counted (default 1)")
    parser.add_argument("--motors", default=",".join(MOTORS), help="comma-separated: triage,visual,vitals")
    parser.add_argument("--url", default=os.environ.get("LATENCY_TEST_URL"), help="http mode: server base URL")
    parser.add_argument("--token", default=os.environ.get("LATENCY_TEST_TOKEN"), help="http mode: patient access token")
    parser.add_argument("--images-dir", type=Path, help="test photos to use instead of the synthetic one")
    parser.add_argument("--allow-stub", action="store_true", help="motor mode without Bedrock (only checks the script)")
    parser.add_argument("--out-dir", type=Path, default=report.REPORTS_DIR)
    args = parser.parse_args(argv)

    motors = [motor.strip() for motor in args.motors.split(",") if motor.strip()]
    unknown = set(motors) - set(MOTORS)
    if unknown or args.runs < 1:
        parser.error(f"--motors must be among {MOTORS} and --runs at least 1")

    if args.mode == "motor":
        if not config.USE_BEDROCK and not args.allow_stub:
            print("USE_BEDROCK is false: the motors would answer with stubs and the timings would mean nothing.\n"
                  "Set USE_BEDROCK=true (AWS access needed), or pass --allow-stub to only check the script.")
            return 2
        call = call_motor
        client = None
    else:
        if not args.url or not args.token:
            parser.error("http mode needs --url and --token (or LATENCY_TEST_URL / LATENCY_TEST_TOKEN)")
        client = make_client()
        call = lambda motor, item: call_http(client, args.url, args.token, motor, item)  # noqa: E731

    print(f"Latency test · mode {args.mode} · {args.runs} runs per motor · motors: {', '.join(motors)}")
    results: Dict[str, dict] = {}
    try:
        for motor in motors:
            print(f"\n{motor}:")
            results[motor] = run_motor(motor, args.runs, args.warmup, call, inputs_for(motor, args.images_dir))
            if motor == "vitals":
                alert_times = [time_vitals_alert(cases.VITALS_CASES[i % len(cases.VITALS_CASES)]) for i in range(args.runs)]
                results["vitals_alert"] = {**report.summary_ms(alert_times), "runs": args.runs, "failures": 0}
    finally:
        if client is not None:
            client.close()

    for name, stats in results.items():
        stats["verdict"] = verdict(name, stats)
    measured_targets = [name for name in ("triage", "vitals_alert") if name in results]
    all_passed = bool(measured_targets) and all(results[name]["verdict"]["passed"] for name in measured_targets)

    text = markdown(results, args.mode, args.runs, all_passed)
    print("\n" + text)
    paths = report.save("latency", {
        "mode": args.mode, "runs": args.runs, "use_bedrock": config.USE_BEDROCK, "aws_region": config.AWS_REGION,
        "passed": all_passed, "results": results,
    }, text, args.out_dir)
    print("Saved: " + ", ".join(str(path) for path in paths))
    return 0 if all_passed else 1


def make_client() -> httpx.Client:
    # Longer than the slowest motor limit, so a slow answer is measured instead of cut off.
    return httpx.Client(timeout=config.longest_motor_timeout_seconds() + 10)


if __name__ == "__main__":
    sys.exit(main())
