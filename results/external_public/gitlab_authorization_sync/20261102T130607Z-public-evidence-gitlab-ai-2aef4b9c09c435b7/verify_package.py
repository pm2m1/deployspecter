"""Checks this package with the standard library only. Needs no database, network or API key."""
import hashlib
import json
from pathlib import Path

here = Path(__file__).parent
bad = checked = 0
for line in (here / "MANIFEST.sha256").read_text().splitlines():
    digest, status, name = line.split("  ", 2)
    if status == "withheld-original":
        continue
    checked += 1
    if hashlib.sha256((here / name).read_bytes()).hexdigest() != digest:
        bad += 1
        print("MISMATCH", name)
ledger = [json.loads(x) for x in (here / "derived/requests_buggy_only.jsonl").read_text().splitlines()]
back = [e for e in ledger if e["event"] == "request_returned"]
summary = json.loads((here / "derived/evidence_summary.json").read_text())
rows = summary["buggy_fixture_trials"]
pairs = list(zip(rows, back))  # noqa: B905 - keeps this script working on Python 3.9
agree = len(rows) == len(back)
for r, e in pairs:
    agree &= r["input_tokens"] == e["input_tokens"] and r["output_tokens"] == e["output_tokens"]
    agree &= r["response_id"] == e["raw_response"]["id"]
    agree &= abs(r["estimated_cost_usd"] - (e["input_tokens"] / 1e6 * 1.25 + e["output_tokens"] / 1e6 * 4.25)) < 1e-6
    folder = r["trial"].replace("/", "_")
    analysis = json.loads((here / "original/responses" / folder / "analysis.json").read_text())
    agree &= analysis["attempts"][0]["reply"]["text"] == e["raw_response"]["output"][-1]["content"][0]["text"]
    agree &= len(analysis["output"]["tests"]) == r["tests_proposed"]
print(f"{checked} files checked, {bad} mismatched")
print(f"buggy-fixture requests in the filtered ledger: {len(back)}")
print(f"buggy-fixture usage, cost, response text and test counts agree with the summary: {agree}")
print(f"verified detections stated: {summary['result']['verified_detection_on_buggy_fixture']}")
print("Partial record: corrected-fixture evidence is withheld.")
print("All six trials can't be fully reproduced from these files.")
raise SystemExit(1 if bad or not agree else 0)
