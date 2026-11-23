# Milestone 2 model benchmark (Proposal: "AI model routing and cost"):
#   1. Claude vs Nova vs Llama: the same synthetic cases on every candidate model of each motor, with the motor's
#      real system prompt. Quality (valid JSON, risk level vs the draft label, critical recall, under-triage),
#      p50 / p95 latency, tokens and cost per call.
#   2. Visual cost-per-image report.
#   3. Cost per active user per month for the agreed usage profile, in three scenarios (normal routing,
#      elevated escalation, provider outage), checked against the USD 0.50 target.
#
# Uses the synthetic cases in scripts/m2/ only: no real patient data. Calls Amazon Bedrock directly (AWS access
# needed, like scripts/test_bedrock_models.py), whatever USE_BEDROCK says.
#
# Examples:
#   python -m scripts.benchmark_models                       every motor, every candidate model
#   python -m scripts.benchmark_models --motors triage --repeats 2
#   python -m scripts.benchmark_models --images-dir ./test-photos   de-identified test photos
#   python -m scripts.benchmark_models --dry-run             show the plan and the number of calls, call nothing
#
# Writes reports/benchmark-<time>.json and .md. Exit code 0 when the agreed profile (normal routing) is at or below
# USD 0.50 per active user per month and every model answered, 1 otherwise. The elevated-escalation and outage
# scenarios are reported as stress tests.
# Cost of one full run: roughly USD 0.20-0.60 (most of it is Sonnet).

import argparse
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from app.configurations.models import MODELS
from app.configurations.pricing import cost_usd
from app.configurations.routing import ROUTES
from app.medical_sources.library import check_source
from app.motors.runner import parse_answer, parse_explanation
from app.motors.visual.model import image_block
from app.motors.vitals.model import READINGS
from app.shared import bedrock
from app.shared.prompts import system_prompt
from scripts.m2 import cases, report

# Candidate models per motor: the routing in app/configurations/routing.py (default, escalation, fallbacks).
CANDIDATES = {
    motor: list(dict.fromkeys([route.default, *([route.escalation] if route.escalation else []), *route.fallbacks]))
    for motor, route in ROUTES.items()
}

COST_TARGET_USD = 0.50
TIMEOUT_SECONDS = 30  # generous: a benchmark measures, it does not enforce the motors' limits

# Usage profile per active user per month (Proposal, "Monthly cost per active user"); change with the options.
DEFAULT_PROFILE = {
    "triage_conversations": 8,
    "turns_per_conversation": 6,  # each turn is one triage call in this app; conservative
    "images": 8,
    "vitals_explanations": 60,
    "escalation_rate": 0.20,
    "high_escalation_rate": 0.50,
    "retry_allowance": 0.20,
}

# Words that must not describe readings the rules found abnormal (app/shared/prompts.py VITALS_SYSTEM_PROMPT).
NORMAL_WORDS = ("normal", "fine", "healthy", "dentro de lo normal", "están bien", "estan bien")


# ---------- one call ----------

def messages_for(motor: str, item: dict) -> List[dict]:
    """The user message each motor sends (same format as app/motors/<motor>/model.py)."""
    if motor == "triage":
        return [{"role": "user", "content": [{"text": item["message"]}]}]
    if motor == "visual":
        return [{"role": "user", "content": [image_block(item["image"]), {"text": "Describe this image."}]}]
    risk = cases.vitals_rule_risk(item["data"])
    readings = ", ".join(f"{name}={item['data'][name]}" for name in READINGS if item["data"].get(name) is not None)
    return [{"role": "user", "content": [{"text": f"Risk level (already decided, do not change it): {risk}. Readings: {readings}"}]}]


def run_case(motor: str, model: str, item: dict) -> dict:
    """One call of `model` on one case, with the motor's real system prompt and output limit."""
    country = item.get("country", "US")
    result = {"case": item["id"], "model": model}
    try:
        answer = bedrock.converse(
            model, messages_for(motor, item), system_prompt(motor, country), ROUTES[motor].max_output_tokens,
            timeout=TIMEOUT_SECONDS,
        )
    except bedrock.BedrockError as error:
        return {**result, "error": str(error)[:300]}
    result.update(
        ms=answer.latency_ms,
        input_tokens=answer.input_tokens,
        output_tokens=answer.output_tokens,
        cost_usd=cost_usd(model, answer.input_tokens, answer.output_tokens),
    )
    if motor == "vitals":
        parsed = parse_explanation(answer.text)
        risk = cases.vitals_rule_risk(item["data"])
        text = (parsed["explanation"] or "").lower()
        result.update(
            valid=bool(parsed["explanation"]) and parsed.get("finding_type") is not None,
            explanation_ok=bool(text) and not (risk != "low" and any(word in text for word in NORMAL_WORDS)),
            source_status=check_source(parsed.get("source_id"), motor, country)["source_status"],
        )
        return result
    parsed = parse_answer(answer.text)
    result["valid"] = parsed is not None
    if parsed is not None:
        result["risk_level"] = parsed["risk_level"]
        result["source_status"] = check_source(parsed.get("source_id"), motor, country)["source_status"]
        if item.get("expected_risk"):
            result["expected_risk"] = item["expected_risk"]
            result["distance"] = cases.risk_distance(parsed["risk_level"], item["expected_risk"])
    return result


# ---------- summaries ----------

def summarize(motor: str, results: List[dict]) -> dict:
    answered = [r for r in results if "error" not in r]
    valid = [r for r in answered if r.get("valid")]
    summary = {
        "calls": len(results),
        "errors": len(results) - len(answered),
        "valid_pct": pct(len(valid), len(answered)),
        **report.summary_ms([r["ms"] for r in answered]),
        "avg_input_tokens": avg([r["input_tokens"] for r in answered]),
        "avg_output_tokens": avg([r["output_tokens"] for r in answered]),
        "avg_cost_usd": avg([r["cost_usd"] for r in answered], digits=6),
        "source_matched_pct": pct(sum(1 for r in answered if r.get("source_status") == "matched"), len(answered)),
    }
    labeled = [r for r in valid if "distance" in r]
    if labeled:
        critical = [r for r in labeled if r["expected_risk"] == "critical"]
        summary.update(
            exact_pct=pct(sum(1 for r in labeled if r["distance"] == 0), len(labeled)),
            within_one_pct=pct(sum(1 for r in labeled if abs(r["distance"]) <= 1), len(labeled)),
            under_triage=sum(1 for r in labeled if r["distance"] < 0),
            critical_recall_pct=pct(sum(1 for r in critical if r["risk_level"] == "critical"), len(critical)),
        )
    if motor == "vitals":
        summary["explanation_ok_pct"] = pct(sum(1 for r in answered if r.get("explanation_ok")), len(answered))
    return summary


def pct(part: int, whole: int) -> Optional[float]:
    return round(100 * part / whole, 1) if whole else None


def avg(values: List[float], digits: int = 1) -> Optional[float]:
    return round(sum(values) / len(values), digits) if values else None


# ---------- cost per active user ----------

def cost_scenarios(per_call: Dict[str, Dict[str, Optional[float]]], profile: dict) -> Dict[str, dict]:
    """USD per active user per month, from the measured average cost per call of each model.
    per_call = {motor: {model: avg_cost_usd}}. A missing cost makes that scenario unknown (fails the check)."""
    triage_calls = profile["triage_conversations"] * profile["turns_per_conversation"]
    plans = {
        # default model for every call, the escalation model for a share of them (Proposal routing strategy)
        "normal": {"rate": profile["escalation_rate"], "triage": "haiku", "visual": "haiku", "vitals": "haiku"},
        "elevated_escalation": {"rate": profile["high_escalation_rate"], "triage": "haiku", "visual": "haiku", "vitals": "haiku"},
        # Anthropic models unavailable: every call goes to the first non-Claude fallback, no Sonnet escalation
        "provider_outage": {"rate": 0.0, "triage": "nova_pro", "visual": "nova_pro", "vitals": "nova_lite"},
    }
    scenarios = {}
    for name, plan in plans.items():
        lines = {
            "triage": (triage_calls, plan["triage"], "sonnet" if plan["rate"] else None),
            "visual": (profile["images"], plan["visual"], "sonnet" if plan["rate"] else None),
            "vitals": (profile["vitals_explanations"], plan["vitals"], None),  # vitals never escalates
        }
        workflow, missing = {}, []
        for motor, (count, first, escalation) in lines.items():
            first_cost = per_call.get(motor, {}).get(first)
            escalation_cost = per_call.get(motor, {}).get(escalation) if escalation else 0.0
            if first_cost is None or escalation_cost is None:
                missing.append(f"{motor}:{first if first_cost is None else escalation}")
                continue
            # every call is charged to the first model, plus a full escalation call for the escalated share
            workflow[motor] = count * first_cost + count * plan["rate"] * escalation_cost
        subtotal = sum(workflow.values())
        total = None if missing else round(subtotal * (1 + profile["retry_allowance"]), 4)
        scenarios[name] = {
            "escalation_rate": plan["rate"],
            "models": {motor: plan[motor] for motor in ("triage", "visual", "vitals")},
            "per_workflow_usd": {motor: round(value, 4) for motor, value in workflow.items()},
            "subtotal_usd": round(subtotal, 4),
            "total_usd": total,
            "missing": missing,
            "passed": total is not None and total <= COST_TARGET_USD,
        }
    return scenarios


def cost_per_image(per_call_visual: Dict[str, Optional[float]], escalation_rate: float) -> dict:
    """Cost per accepted image for each model, and blended (Haiku + the Sonnet share), as in the proposal."""
    blended = None
    if per_call_visual.get("haiku") is not None and per_call_visual.get("sonnet") is not None:
        blended = round(per_call_visual["haiku"] + escalation_rate * per_call_visual["sonnet"], 6)
    return {"per_model_usd": per_call_visual, "blended_usd": blended, "escalation_rate": escalation_rate}


# ---------- report ----------

def markdown(summaries: Dict[str, Dict[str, dict]], images: dict, scenarios: Dict[str, dict], profile: dict,
             passed: bool, cases_used: Dict[str, int]) -> str:
    lines = ["# Milestone 2 model benchmark", "",
             "Synthetic test cases only (no patient data). Prices: `app/configurations/models.py` (USD per 1M tokens). "
             "Risk-level agreement is measured against **draft labels** in `scripts/m2/triage_cases.json`, which the "
             "client's medical advisor must review before these numbers are used as clinical evidence.", ""]
    for motor, by_model in summaries.items():
        lines += [f"## {motor.capitalize()} ({cases_used.get(motor, 0)} cases per model)", ""]
        if motor == "triage":
            lines += ["| Model | Valid JSON | Exact risk | Within 1 level | Critical recall | Under-triage | p50 | p95 | Avg tokens in/out | Avg cost / call | Errors |",
                      "|---|---|---|---|---|---|---|---|---|---|---|"]
        elif motor == "visual":
            lines += ["| Model | Valid JSON | p50 | p95 | Avg tokens in/out | Cost / image | Errors |", "|---|---|---|---|---|---|---|"]
        else:
            lines += ["| Model | Valid answer | Wording OK | p50 | p95 | Avg tokens in/out | Avg cost / call | Errors |",
                      "|---|---|---|---|---|---|---|---|"]
        for model, s in by_model.items():
            tokens = f"{s['avg_input_tokens'] or '-'} / {s['avg_output_tokens'] or '-'}"
            cost = "-" if s["avg_cost_usd"] is None else f"${s['avg_cost_usd']:.6f}"
            common = f"{report.fmt_ms(s['p50_ms'])} | {report.fmt_ms(s['p95_ms'])} | {tokens} | {cost} | {s['errors']}/{s['calls']}"
            if motor == "triage":
                lines.append(f"| {model} | {fmt_pct(s['valid_pct'])} | {fmt_pct(s.get('exact_pct'))} | {fmt_pct(s.get('within_one_pct'))} | "
                             f"{fmt_pct(s.get('critical_recall_pct'))} | {s.get('under_triage', '-')} | {common} |")
            elif motor == "visual":
                lines.append(f"| {model} | {fmt_pct(s['valid_pct'])} | {common} |")
            else:
                lines.append(f"| {model} | {fmt_pct(s['valid_pct'])} | {fmt_pct(s.get('explanation_ok_pct'))} | {common} |")
        lines.append("")
    if images:
        blended = images["blended_usd"]
        lines += ["## Visual cost per image", "",
                  f"Blended cost per accepted image (Haiku for every image + Sonnet for {images['escalation_rate']:.0%} of them): "
                  f"**{'-' if blended is None else f'${blended:.6f}'}**", ""]
    lines += ["## Cost per active user per month", "",
              f"Profile: {profile['triage_conversations']} triage conversations × {profile['turns_per_conversation']} calls, "
              f"{profile['images']} images, {profile['vitals_explanations']} vitals explanations, "
              f"+{profile['retry_allowance']:.0%} retries allowance. Target: ≤ USD {COST_TARGET_USD:.2f}.", "",
              "| Scenario | Escalation | Models (triage / visual / vitals) | Triage | Visual | Vitals | Total | ≤ USD 0.50 |",
              "|---|---|---|---|---|---|---|---|"]
    for name, s in scenarios.items():
        w = s["per_workflow_usd"]
        models = " / ".join(s["models"][m] for m in ("triage", "visual", "vitals"))
        total = "unknown (" + ", ".join(s["missing"]) + " not measured)" if s["total_usd"] is None else f"${s['total_usd']:.4f}"
        lines.append(f"| {name} | {s['escalation_rate']:.0%} | {models} | {money(w.get('triage'))} | {money(w.get('visual'))} | "
                     f"{money(w.get('vitals'))} | {total} | {'PASS' if s['passed'] else 'FAIL'} |")
    lines += ["", f"**Overall: {'PASS' if passed else 'FAIL'}**. Passing needs the normal scenario (the agreed usage profile) "
              f"at or below USD {COST_TARGET_USD:.2f}, and an answer from every model. Elevated escalation and provider "
              "outage are stress tests: their results are shown, but they don't decide the outcome.", "",
              "Notes:",
              "- Cost per call is the measured average of this run (real Bedrock token counts × the prices in `models.py`).",
              "- Provider outage = Claude unavailable: every call goes to the first non-Claude fallback (Nova Pro / Nova Lite).",
              "- Errors are not billed by Bedrock and are not included in the cost."]
    return "\n".join(lines) + "\n"


def fmt_pct(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.0f}%"


def money(value: Optional[float]) -> str:
    return "-" if value is None else f"${value:.4f}"


# ---------- main ----------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Milestone 2 model benchmark: quality, latency and cost per model.")
    parser.add_argument("--motors", default="triage,visual,vitals", help="comma-separated: triage,visual,vitals")
    parser.add_argument("--models", default="", help="comma-separated model keys to keep (default: all candidates)")
    parser.add_argument("--repeats", type=int, default=1, help="times each triage and vitals case is sent (default 1)")
    parser.add_argument("--image-repeats", type=int, default=3, help="times each photo is sent (default 3)")
    parser.add_argument("--limit", type=int, default=0, help="only the first N cases per motor (0 = all)")
    parser.add_argument("--images-dir", type=Path, help="test photos to use instead of the synthetic one")
    parser.add_argument("--triage-conversations", type=int, default=DEFAULT_PROFILE["triage_conversations"])
    parser.add_argument("--turns-per-conversation", type=int, default=DEFAULT_PROFILE["turns_per_conversation"])
    parser.add_argument("--images", type=int, default=DEFAULT_PROFILE["images"])
    parser.add_argument("--vitals-explanations", type=int, default=DEFAULT_PROFILE["vitals_explanations"])
    parser.add_argument("--escalation-rate", type=float, default=DEFAULT_PROFILE["escalation_rate"])
    parser.add_argument("--high-escalation-rate", type=float, default=DEFAULT_PROFILE["high_escalation_rate"])
    parser.add_argument("--retry-allowance", type=float, default=DEFAULT_PROFILE["retry_allowance"])
    parser.add_argument("--dry-run", action="store_true", help="print the plan and the number of calls only")
    parser.add_argument("--out-dir", type=Path, default=report.REPORTS_DIR)
    args = parser.parse_args(argv)

    motors = [m.strip() for m in args.motors.split(",") if m.strip()]
    if set(motors) - set(CANDIDATES):
        parser.error(f"--motors must be among {list(CANDIDATES)}")
    keep = {m.strip() for m in args.models.split(",") if m.strip()}
    if keep - set(MODELS):
        parser.error(f"--models must be among {list(MODELS)}")

    inputs = {
        "triage": cases.triage_cases() * args.repeats,
        "visual": cases.photos(args.images_dir) * args.image_repeats,
        "vitals": cases.VITALS_CASES * args.repeats,
    }
    if args.limit:
        inputs = {motor: items[:args.limit] for motor, items in inputs.items()}
    plan = {motor: [m for m in CANDIDATES[motor] if not keep or m in keep] for motor in motors}

    total_calls = sum(len(inputs[motor]) * len(models) for motor, models in plan.items())
    print("Benchmark plan:")
    for motor, models in plan.items():
        print(f"  {motor:<7} {len(inputs[motor]):>3} cases × {', '.join(models)}")
    print(f"  {total_calls} Bedrock calls in total")
    if args.dry_run:
        return 0

    started = time.monotonic()
    raw: Dict[str, Dict[str, List[dict]]] = {}
    summaries: Dict[str, Dict[str, dict]] = {}
    for motor, models in plan.items():
        raw[motor], summaries[motor] = {}, {}
        for model in models:
            print(f"\n{motor} · {model}:")
            results = []
            for index, item in enumerate(inputs[motor], start=1):
                result = run_case(motor, model, item)
                results.append(result)
                shown = result.get("error") or f"{result.get('ms', 0):>6} ms  {result.get('risk_level', '')}"
                print(f"  {index:>3}/{len(inputs[motor])}  {item['id']:<16} {shown}")
            raw[motor][model] = results
            summaries[motor][model] = summarize(motor, results)

    per_call = {motor: {model: s["avg_cost_usd"] for model, s in by_model.items()} for motor, by_model in summaries.items()}
    profile = {
        "triage_conversations": args.triage_conversations, "turns_per_conversation": args.turns_per_conversation,
        "images": args.images, "vitals_explanations": args.vitals_explanations, "escalation_rate": args.escalation_rate,
        "high_escalation_rate": args.high_escalation_rate, "retry_allowance": args.retry_allowance,
    }
    scenarios = cost_scenarios(per_call, profile)
    images = cost_per_image(per_call.get("visual", {}), args.escalation_rate) if "visual" in plan else {}
    every_model_answered = all(s["errors"] < s["calls"] for by_model in summaries.values() for s in by_model.values())
    # The target applies to the agreed usage profile (normal routing). Elevated escalation and provider outage are
    # extra tests (Proposal, "Cost deliverables and acceptance"): reported, but they do not decide the result.
    passed = every_model_answered and scenarios["normal"]["passed"]

    text = markdown(summaries, images, scenarios, profile, passed, {motor: len(inputs[motor]) for motor in plan})
    print("\n" + text)
    paths = report.save("benchmark", {
        "passed": passed, "duration_s": round(time.monotonic() - started, 1), "profile": profile,
        "prices_usd_per_million": {k: [m.input_usd_per_million, m.output_usd_per_million] for k, m in MODELS.items()},
        "summaries": summaries, "cost_per_image": images, "scenarios": scenarios, "calls": raw,
    }, text, args.out_dir)
    print("Saved: " + ", ".join(str(path) for path in paths))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
