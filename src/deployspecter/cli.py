"""`uv run deployspecter ...`: explore, replay, benchmark, serve."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import PROJECT_ROOT, load_settings


def _cmd_explore(args) -> int:
    from .analyzer import PROMPT_VERSION
    from .database import Sandbox
    from .meta_client import MetaClient, MissingApiKey
    from .pipeline import run_ai, run_baseline, run_hand
    from .replay import load_artifact
    from .report import render_markdown
    from .scenarios import load_case
    from .scheduler import ScheduleRunner

    settings = load_settings()
    case = load_case(settings.scenarios_dir, args.scenario, args.variant)
    artifacts = settings.results_dir / "failures"
    with Sandbox(settings) as sandbox, ScheduleRunner(sandbox) as runner:
        shared = (sandbox, runner, case, args.max_schedules, args.max_seconds, artifacts)
        if args.generator == "hand":
            run = run_hand(*shared)
        elif args.generator == "baseline":
            run = run_baseline(*shared)
        else:
            client = None
            if args.max_requests:
                try:
                    client = MetaClient(
                        settings, max_requests=args.max_requests, max_estimated_usd=args.max_estimated_usd
                    )
                except MissingApiKey as exc:
                    print(exc, file=sys.stderr)
                    return 2
            name = f"{case.case_id.replace('/', '__')}__r0.json"
            cache = settings.results_dir / "ai_responses" / PROMPT_VERSION / name
            run = run_ai(*shared, client, cache, reuse=not args.fresh)
    print(f"case: {case.case_id}   generator: {run.system}")
    print(f"tests valid: {run.tests_valid}/{run.tests_proposed}   schedules executed: {run.schedules_executed}")
    for rejection in run.rejections:
        print(f"  rejected {rejection.test}: {rejection.reasons}")
    for t in run.runs:
        e = t.exploration
        print(f"  {e.spec_name}: {e.executed}/{e.space_size} schedules, {e.classifications}, "
              f"violations={e.violating_total}, deferred={e.deferred_schedules}, "
              f"non-serial final states={e.non_serial_final_states}")
    if run.error:
        print(f"error: {run.error}", file=sys.stderr)
        return 2
    if not run.detected:
        print("no violation of the stated requirements in the schedules executed "
              "(this is not a proof of correctness)")
        return 0
    print(f"VIOLATION after {run.schedules_to_first_failure} schedules ({run.seconds_to_first_failure}s); "
          f"replay reproduced: {run.replay.reproduced} ({run.replay.fingerprints_match}/{run.replay.attempts})")
    m = run.minimized
    print(f"minimized {m.before} -> {m.after}; replay reproduced: {run.minimized_replay.reproduced}")
    print(f"artifact: {run.minimized_artifact_path}\n")
    print(render_markdown(load_artifact(PROJECT_ROOT / run.minimized_artifact_path), run.minimized_artifact_path))
    return 1 if args.fail_on_violation else 0


def _cmd_replay(args) -> int:
    from .database import Sandbox
    from .replay import load_artifact, replay
    from .scheduler import ScheduleRunner

    artifact = load_artifact(Path(args.artifact))
    with Sandbox(load_settings()) as sandbox, ScheduleRunner(sandbox) as runner:
        report = replay(artifact, runner, attempts=args.attempts)
    print(report.model_dump_json(indent=2))
    for v in artifact.verdict.violations:
        print(f"{v.requirement_id}: expected {v.expected!r}, actual {v.actual!r}")
    return 0 if report.reproduced else 1


def _cmd_benchmark(args) -> int:
    from .benchmark import run_benchmark
    from .benchmark_report import write_report

    settings = load_settings()
    out_dir = Path(args.out_dir) if args.out_dir else None
    report = run_benchmark(
        settings, args.split, args.systems.split(","), ai_repeats=args.ai_repeats,
        budget_schedules=args.max_schedules, budget_seconds=args.max_seconds, reuse_ai=not args.fresh,
        max_requests=args.max_requests, max_estimated_usd=args.max_estimated_usd, allow_repair=args.allow_repair,
        out_dir=out_dir,
    )
    path = write_report(report, out_dir or settings.results_dir / "benchmark", update_latest=args.update_latest)
    print(json.dumps(report["summary"], indent=2))
    print(f"\nwritten: {path}")
    return 0


def _cmd_documented(args) -> int:
    from .documented import write_documented_report

    report, path = write_documented_report(load_settings())
    for variant, data in report["variants"].items():
        facts = data["facts"]
        print(f"{variant}: {data['violating_schedules']}/{data['schedules']} schedules violate; "
              f"class={facts['failure_class']}; destination before={facts['destination_before']} "
              f"after={facts['destination_after']}; source after={facts['source_after']}")
        if data.get("artifact"):
            print(f"  replay reproduced: {data['replay_reproduced']}; "
                  f"uv run deployspecter replay {data['artifact']}")
    print(f"written: {path}")
    return 0


def _cmd_build_demo(args) -> int:
    from .guided import write_recording
    from .site import write_site_data

    settings = load_settings()
    print(f"written: {write_recording(settings)}")
    print(f"written: {write_site_data(settings)}")
    return 0


def _cmd_export_site(args) -> int:
    from .site import export_static_site

    files = export_static_site(Path(args.out))
    print(f"exported {len(files)} files to {args.out}")
    return 0


def _cmd_serve(args) -> int:
    import uvicorn

    uvicorn.run("deployspecter.api:app", host="127.0.0.1", port=args.port)
    return 0


def _cmd_usage(args) -> int:
    from .benchmark_report import usage_ledger

    print(json.dumps(usage_ledger(load_settings().results_dir / "ai_responses"), indent=2))
    return 0


def _add_spending_limits(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--max-requests", type=int, default=0, help="Meta requests allowed; 0 (default) uses recorded responses only"
    )
    parser.add_argument("--max-estimated-usd", type=float, default=0.0, help="hard estimated spend limit")


def main() -> None:
    parser = argparse.ArgumentParser(prog="deployspecter")
    sub = parser.add_subparsers(dest="command", required=True)

    explore = sub.add_parser("explore", help="find violations in one scenario")
    explore.add_argument("scenario")
    explore.add_argument("variant", choices=["buggy", "corrected"])
    explore.add_argument("--generator", choices=["hand", "baseline", "ai"], default="hand")
    explore.add_argument("--max-schedules", type=int, default=300)
    explore.add_argument("--max-seconds", type=float, default=60)
    explore.add_argument("--fresh", action="store_true", help="ignore a recorded model response")
    _add_spending_limits(explore)
    explore.add_argument("--fail-on-violation", action="store_true")
    explore.set_defaults(func=_cmd_explore)

    rep = sub.add_parser("replay", help="re-execute a saved failure")
    rep.add_argument("artifact")
    rep.add_argument("--attempts", type=int, default=3)
    rep.set_defaults(func=_cmd_replay)

    bench = sub.add_parser("benchmark", help="compare systems on a split")
    bench.add_argument("--split", choices=["dev", "heldout", "all"], default="dev")
    bench.add_argument("--systems", default="static,hand,baseline_b,ai")
    bench.add_argument("--ai-repeats", type=int, default=3)
    bench.add_argument("--max-schedules", type=int, default=300)
    bench.add_argument("--max-seconds", type=float, default=60)
    bench.add_argument("--fresh", action="store_true")
    bench.add_argument("--out-dir", help="write reports and artifacts here instead of results/benchmark")
    bench.add_argument("--update-latest", action="store_true", help="make this report the one the interface shows")
    bench.add_argument("--allow-repair", action="store_true", help="spend one extra request on an unusable response")
    _add_spending_limits(bench)
    bench.set_defaults(func=_cmd_benchmark)

    documented = sub.add_parser(
        "documented-defect", help="run the published GitLab defect on PostgreSQL; makes no model requests"
    )
    documented.set_defaults(func=_cmd_documented)

    build = sub.add_parser(
        "build-demo-data", help="record the walkthrough on PostgreSQL and rebuild site data; makes no model requests"
    )
    build.set_defaults(func=_cmd_build_demo)

    export = sub.add_parser("export-site", help="write the recorded site as static files; needs no database")
    export.add_argument("--out", default="dist/site")
    export.set_defaults(func=_cmd_export_site)

    usage = sub.add_parser("usage", help="summarize recorded model usage; makes no requests")
    usage.set_defaults(func=_cmd_usage)

    serve = sub.add_parser("serve", help="start the local web interface")
    serve.add_argument("--port", type=int, default=8765)
    serve.set_defaults(func=_cmd_serve)

    args = parser.parse_args()
    raise SystemExit(args.func(args))
