import {$, el, fmt} from "../lib/dom.js";
import {verdict} from "./components.js";

function rollup(scores, system) {
  const rows = scores.filter((s) => s.system === system);
  const bugs = rows.filter((s) => s.has_bug), clean = rows.filter((s) => !s.has_bug);
  const executed = clean.filter((s) => system === "baseline_a_static" || s.schedules_executed > 0);
  const firsts = bugs.map((s) => s.schedules_to_first_failure).filter((v) => v).sort((a, b) => a - b);
  const median = firsts.length ? (firsts.length % 2 ? firsts[(firsts.length - 1) / 2] : (firsts[firsts.length / 2 - 1] + firsts[firsts.length / 2]) / 2) : null;
  const skipped = clean.length - executed.length;
  return {
    runs: rows.length, found: bugs.filter((s) => s.flagged).length, bugs: bugs.length,
    falseFlags: executed.filter((s) => s.flagged).length, executed: executed.length, skipped, median,
    valid: rows.reduce((n, s) => n + (s.tests_valid || 0), 0), proposed: rows.reduce((n, s) => n + (s.tests_proposed || 0), 0),
    first: rows.reduce((n, s) => n + (s.first_attempt_valid || 0), 0), firstOf: rows.reduce((n, s) => n + (s.first_attempt_proposed || 0), 0),
    schedules: rows.reduce((n, s) => n + (s.schedules_executed || 0), 0), replayed: bugs.filter((s) => s.replay_ok).length,
    requests: rows.reduce((n, s) => n + (s.requests || 0), 0), seconds: rows.reduce((n, s) => n + (s.inference_seconds || 0), 0),
    cost: rows.reduce((n, s) => n + (s.approx_cost_usd || 0), 0),
  };
}

const SYSTEMS = [["ai", "Muse Spark (recorded)"], ["baseline_b", "Baseline B (non-LLM)"], ["baseline_c", "Baseline C (non-LLM, added later)"], ["hand", "Hand-authored"], ["baseline_a_static", "Static rules"]];

function summaryTable(scores, title) {
  const heads = ["System", "Defects found", "Replayed", "False flags", "Median schedules to failure", "Valid tests", "Schedules", "Requests", "Inference", "Est. cost"];
  return el("div", {}, el("h3", {text: title}), el("div", {class: "table-scroll", tabindex: "0"}, el("table", {class: "data"},
    el("thead", {}, el("tr", {}, ...heads.map((h) => el("th", {text: h})))),
    el("tbody", {}, ...SYSTEMS.map(([id, name]) => {
      const r = rollup(scores, id);
      if (!r.runs) return null;
      const isStatic = id === "baseline_a_static";
      return el("tr", {},
        el("td", {text: name}), el("td", {class: "num", text: `${r.found}/${r.bugs}${isStatic ? " (warnings)" : ""}`}),
        el("td", {class: "num", text: isStatic ? "not executed" : `${r.replayed}/${r.found}`}),
        el("td", {class: "num", text: `${r.falseFlags}/${r.executed}${r.skipped ? ` (${r.skipped} not executed)` : ""}`}),
        el("td", {class: "num", text: fmt(r.median)}),
        el("td", {class: "num", text: r.proposed ? `${r.valid}/${r.proposed}` : "n/a"}),
        el("td", {class: "num", text: r.schedules}),
        el("td", {class: "num", text: r.requests || "none"}),
        el("td", {class: "num", text: r.requests ? `${r.seconds.toFixed(0)}s` : "none"}),
        el("td", {class: "num", text: r.requests ? `$${r.cost.toFixed(2)}` : "none"}));
    })))));
}

export function renderComparison(data, usage) {
  const tab = $("tab-comparison");
  if (!Object.keys(data).length) { tab.replaceChildren(el("div", {class: "panel", text: "No benchmark has been run yet."})); return; }
  const cScores = data.baseline_c ? data.baseline_c.scores.filter((s) => s.system === "baseline_c") : [];
  const split = (name) => [...((data[name] || {}).scores || []), ...cScores.filter((s) => s.split === name)];
  const scores = [...split("heldout"), ...split("dev")];
  const ai = rollup(scores, "ai"), best = Math.max(...["baseline_b", "baseline_c"].map((s) => { const r = rollup(scores, s); return r.bugs ? r.found / r.bugs : 0; }));
  const scenarios = [...new Set(scores.filter((s) => s.has_bug).map((s) => s.case_id.split("/")[0]))];
  const perScenario = el("div", {class: "table-scroll", tabindex: "0"}, el("table", {class: "data"},
    el("thead", {}, el("tr", {}, ...["Scenario", "Split", "Muse Spark", "Baseline B", "Baseline C", "Hand-authored"].map((h) => el("th", {text: h})))),
    el("tbody", {}, ...scenarios.map((sc) => {
      const cell = (system) => {
        const rows = scores.filter((s) => s.system === system && s.case_id === `${sc}/buggy`);
        if (!rows.length) return "n/a";
        const firsts = [...new Set(rows.map((s) => s.schedules_to_first_failure))].join(", ");
        const manner = rows.some((s) => s.matches_reference_manifestation === false) ? " (differs from reference)" : "";
        return `${firsts}${manner}`;
      };
      const row = scores.find((s) => s.case_id === `${sc}/buggy`);
      return el("tr", {}, el("td", {text: sc}), el("td", {text: row.split}), ...["ai", "baseline_b", "baseline_c", "hand"].map((s) => el("td", {class: "num", text: cell(s)})));
    }))));
  const v1 = usage.versions.v1, v2 = usage.versions.v2;
  const limits = [
    `First-response test validity: ${ai.first} of ${ai.firstOf} across recorded runs.`,
    data.heldout ? `Held-out run: ${data.heldout.scores.filter((s) => s.system === "ai" && !s.schedules_executed).map((s) => s.case_id).join(", ") || "every case"} ${data.heldout.scores.some((s) => s.system === "ai" && !s.schedules_executed) ? "produced no executable test, because actor names were rejected at the time." : "executed."}` : null,
    v1 ? `${v1.requests} earlier requests were void because of a bug in this project's response schema, not the model. They are kept as a usage record.` : null,
    v2 ? `Recorded usage for valid prompts: ${v2.requests} requests, ${v2.output_tokens} output tokens, $${v2.estimated_usd.toFixed(2)} estimated.` : null,
    ...(data.dev && data.dev.notes ? data.dev.notes : []),
  ].filter(Boolean);
  tab.replaceChildren(
    el("div", {class: "page-head"}, el("h1", {text: "AI versus non-AI"}), el("p", {text: "Measured results from saved benchmark files. Nothing on this page runs a test or a model."})),
    el("div", {class: "panel"},
      el("h2", {}, "Benchmark comparison ", el("span", {class: "badge recorded", text: "Saved benchmark data"})),
      ai.bugs && ai.found / ai.bugs <= best ? el("div", {class: "verdict warn"}, el("strong", {class: "big", text: "No measured detection advantage for Muse Spark"}),
        el("div", {text: "Muse Spark found no greater share of the seeded defects than the non-LLM baselines. Its runs count per repeat, so its totals include repeated cases."})) : null,
      data.heldout ? summaryTable(split("heldout"), `Held-out cases, ${data.heldout.budget.schedules_per_case} schedules per case`) : null,
      data.dev ? summaryTable(split("dev"), `Development cases, ${data.dev.budget.schedules_per_case} schedules per case`) : null,
      el("h3", {text: "Schedules to first failure, by scenario"}), perScenario,
      el("p", {class: "note", text: "A smaller number costs milliseconds, while a model response takes about a minute. Baseline C was added after the original evaluation and adjusted with these fixtures in view, so its results are not blind."})),
    el("div", {class: "panel"}, el("h2", {text: "Recorded limitations"}),
      el("ul", {class: "tight"}, ...limits.map((t) => el("li", {text: t}))),
      el("p", {class: "note", text: "Tokens are as reported by the API in recorded responses. Costs are estimates at published rates, not billing data. Small, author-designed benchmark: results do not generalize."})));
}
