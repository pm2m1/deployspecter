import {el, fmtRows, getJson, shown} from "./lib/dom.js";
import {renderCallouts, renderTimeline} from "./lib/timeline.js";

// Links to the old single-page interface used these fragments on the root URL.
const OLD_TABS = ["gitlab", "scenarios", "pipeline", "comparison"];
const followOldLink = () => { if (OLD_TABS.includes(location.hash.slice(1))) location.replace(`app${location.hash}`); };
followOldLink();
window.addEventListener("hashchange", followOldLink);

const $ = (id) => document.getElementById(id);
const SYSTEM_ORDER = ["ai", "baseline_b", "baseline_c", "hand", "baseline_a_static"];

function evidence(w) {
  const run = w.runs["reference/buggy/stale_read"];
  const v = run.violations[0];
  const explored = w.exploration["reference/buggy"];
  $("evidence-badge").textContent = `Recorded on PostgreSQL ${w.postgres_version}, ${w.generated_at}`;
  $("evidence").replaceChildren(
    renderTimeline(run, {caption: "A stale backfill overwriting a committed update"}),
    renderCallouts(run),
    el("div", {class: "compare"},
      el("div", {}, el("span", {text: "Expected display_name"}), shown(v.expected)),
      el("div", {}, el("span", {text: "Actual, read from PostgreSQL"}), shown(v.actual)),
      el("div", {}, el("span", {text: "Interleavings that fail"}), `${explored.violating} of ${explored.executed}`),
      el("div", {}, el("span", {text: "Serial orders that fail"}), explored.violates_in_serial_order ? "At least one" : "None"),
      el("div", {}, el("span", {text: "Replay"}), `${run.replay.fingerprints_match} of ${run.replay.attempts} identical`)));
}

function museExample(w) {
  const m = w.muse;
  const lines = m.proposal.actors.map((a) => `${a.name}  (${a.function})\n` + a.calls.map((c) => `  ${c.statement_id}`).join("\n")).join("\n\n");
  $("muse-example").replaceChildren(el("div", {class: "code-card"},
    el("header", {}, el("span", {text: m.proposal.name}), el("span", {class: "badge recorded", text: "Recorded response"})),
    el("pre", {class: "code", tabindex: "0", text: lines})),
  el("p", {class: "note", style: "margin-top:12px", text: m.proposal.rationale}),
  el("p", {class: "note", style: "margin-top:8px", text: `${m.model}: ${m.input_tokens} input and ${m.output_tokens} output tokens, ${Math.round(m.latency_seconds)} seconds. Validator: ${m.validator.accepted ? "accepted" : "rejected"}.`}));
}

function results(site) {
  const {benchmark} = site;
  const held = benchmark.splits.heldout.systems, dev = benchmark.splits.dev.systems;
  $("muse-validity").textContent = `On first response, ${dev.ai.first_response_valid} of ${dev.ai.first_response_proposed} development tests and ${held.ai.first_response_valid} of ${held.ai.first_response_proposed} held-out tests passed the validator.`;
  $("muse-parity").textContent = `It found ${held.ai.defects_found} of ${held.ai.defect_runs} held-out defects. So did Baseline C, a non-LLM generator, at ${held.baseline_c.defects_found} of ${held.baseline_c.defect_runs}. Muse Spark found no more.`;
  $("results-intro").textContent = `${benchmark.scenarios} synthetic scenarios, each with a buggy and a corrected version: ${benchmark.development_cases} development and ${benchmark.held_out_cases} held-out cases, at up to ${benchmark.splits.heldout.schedules_per_case} schedules per case. The table shows held-out cases.`;
  const heads = ["System", "Defects found", "Replayed", "False flags", "Median schedules to failure", "Model requests", "Est. cost"];
  $("results-table").replaceChildren(el("table", {class: "data"},
    el("thead", {}, el("tr", {}, ...heads.map((h, i) => el("th", {scope: "col", class: i ? "num" : null, text: h})))),
    el("tbody", {}, ...SYSTEM_ORDER.map((id) => {
      const s = held[id];
      return el("tr", {},
        el("td", {text: s.name}),
        el("td", {class: "num", text: `${s.defects_found}/${s.defect_runs}${s.executes ? "" : " (warnings)"}`}),
        el("td", {class: "num", text: s.executes ? `${s.replayed}/${s.defects_found}` : "not executed"}),
        el("td", {class: "num", text: `${s.false_flags}/${s.corrected_runs_executed}${s.corrected_runs_not_executed ? ` (${s.corrected_runs_not_executed} not executed)` : ""}`}),
        el("td", {class: "num", text: s.median_schedules_to_failure ?? "n/a"}),
        el("td", {class: "num", text: s.requests || "none"}),
        el("td", {class: "num", text: s.requests ? `$${s.estimated_usd.toFixed(2)}` : "none"}));
    }))));
  const soft = benchmark.by_scenario.soft_delete_resurrection.systems;
  const stale = benchmark.by_scenario.backfill_overwrite.systems;
  $("results-reading").replaceChildren(
    el("li", {text: "Muse Spark showed no detection advantage over the non-LLM baselines."}),
    el("li", {text: `It needed fewer schedules on two scenarios: ${stale.ai.schedules[0]} versus ${stale.baseline_b.schedules[0]}, and ${soft.ai.schedules[0]} versus ${soft.baseline_b.schedules[0]}. A schedule takes milliseconds, while a model response takes about a minute, so the non-LLM generators were much faster overall.`}),
    el("li", {text: "One held-out response was rejected over actor names, so that corrected case never ran with Muse Spark's tests."}),
    el("li", {text: "Baseline C was added later, with these fixtures in view, so its results are not blind."}));
  const {gitlab} = site;
  $("gitlab-stats").replaceChildren(
    el("div", {class: "stat"}, el("strong", {text: `${gitlab.buggy.violating} of ${gitlab.buggy.schedules}`}), el("span", {text: "schedules lose the row with the original trigger"})),
    el("div", {class: "stat"}, el("strong", {text: `${gitlab.corrected.violating} of ${gitlab.corrected.schedules}`}), el("span", {text: "in this test with the delete logic of GitLab's fix"})),
    el("div", {class: "stat"}, el("strong", {text: String(gitlab.buggy.actors_in_minimal_failure)}), el("span", {text: "actor in the smallest failing test"})),
    el("div", {class: "stat"}, el("strong", {style: "font-size:17px;line-height:1.4;font-weight:600", text: fmtRows(gitlab.buggy.remaining)}), el("span", {text: "source row that remains after the destination row is gone"})));
  $("gitlab-limits").replaceChildren(...gitlab.limits.map((t) => el("li", {text: t})));
  $("gitlab-links").replaceChildren(...gitlab.sources.flatMap((l, i) => [i ? " · " : "", el("a", {href: l.url, rel: "noreferrer", text: l.label})]));
}

async function init() {
  try {
    const [walkthrough, site] = await Promise.all([getJson("static/data/walkthrough.json"), getJson("static/data/site.json")]);
    evidence(walkthrough);
    museExample(walkthrough);
    results(site);
  } catch (error) {
    $("evidence").replaceChildren(el("div", {class: "verdict warn", role: "alert"}, el("span", {class: "big", text: "Recorded evidence could not load"}), el("p", {text: error.message})));
    $("evidence-badge").textContent = "Not loaded";
  }
}
init();
