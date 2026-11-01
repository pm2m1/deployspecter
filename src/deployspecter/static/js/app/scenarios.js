import {$, el, fmt, getJson} from "../lib/dom.js";
import {resultBody, runCard, sourcePanel, variantCards} from "./components.js";

const GENERATORS = [["hand", "Hand-authored reference"], ["baseline", "Baseline B (non-LLM)"], ["baseline_c", "Baseline C (non-LLM, added later)"], ["ai", "Muse Spark (recorded responses)"]];

export let savedReports = {};
export function setSavedReports(data) { savedReports = data; }

function allScores(data) {
  return Object.values(data).flatMap((r) => r.scores || []).filter((s, i, a) => s.system !== "hand" || a.findIndex((x) => x.case_id === s.case_id && x.system === "hand") === i);
}

function scenarioScoreTable(scenario) {
  const rows = allScores(savedReports).filter((s) => s.case_id.startsWith(`${scenario}/`));
  if (!rows.length) return el("p", {class: "note", text: "No saved benchmark data."});
  const finding = (s) => {
    if (s.system === "baseline_a_static") return s.flagged ? "Warning, not executed" : "No warning";
    if (s.tests_proposed !== null && !s.schedules_executed) return "No test executed";
    return s.flagged ? "Violation" : "No violation";
  };
  const heads = ["Version", "System", "Run", "Finding", "Schedules to failure", "Needs concurrency", "Valid tests", "Replayed"];
  return el("div", {class: "table-scroll", tabindex: "0"}, el("table", {class: "data"},
    el("thead", {}, el("tr", {}, ...heads.map((h) => el("th", {text: h})))),
    el("tbody", {}, ...rows.map((s) => el("tr", {},
      el("td", {text: s.has_bug ? "Buggy" : "Corrected"}), el("td", {text: s.system}), el("td", {class: "num", text: s.repeat}),
      el("td", {text: finding(s)}), el("td", {class: "num", text: fmt(s.schedules_to_first_failure)}),
      el("td", {text: s.serial_order_violation === null ? "n/a" : (s.serial_order_violation ? "No" : "Yes")}),
      el("td", {class: "num", text: s.tests_proposed === null ? "n/a" : `${s.tests_valid}/${s.tests_proposed}`}),
      el("td", {text: s.replay_ok === null ? "n/a" : (s.replay_ok ? "Yes" : "No")}))))));
}

export function renderScenarios(list) {
  const tab = $("tab-scenarios");
  const scenario = el("select", {id: "scenario"}, ...list.map((s) => el("option", {value: s.id, text: `${s.title} (${s.split})`})));
  const generator = el("select", {id: "generator"}, ...GENERATORS.map(([v, t]) => el("option", {value: v, text: t})));
  const budget = el("input", {id: "budget", type: "number", min: "1", max: "2000", value: "300"});
  const run = el("button", {class: "btn primary", id: "run", text: "Run both versions"});
  const desc = el("p", {}), requirement = el("p", {class: "note"}), source = el("div", {class: "note"});
  const results = el("div", {class: "result-grid", id: "results"}), saved = el("div", {});
  const describe = async () => {
    const s = list.find((x) => x.id === scenario.value);
    desc.textContent = s.description;
    requirement.textContent = `Independent requirement, hidden from test generators: ${s.requirements.join(" ")}`;
    saved.replaceChildren(el("h2", {}, "This scenario in the benchmark ", el("span", {class: "badge recorded", text: "Saved benchmark data"})), scenarioScoreTable(s.id));
    const src = await getJson(`/scenarios/${s.id}/source`);
    source.replaceChildren(...sourcePanel(src, ["Buggy version", "Corrected version"]));
  };
  scenario.onchange = describe;
  run.onclick = async () => {
    run.disabled = true;
    results.replaceChildren();
    try {
      for (const [variant, card] of variantCards(results)) {
        await runCard(`/scenarios/${scenario.value}/run`, {variant, generator: generator.value, max_schedules: Number(budget.value)}, card, resultBody);
      }
    } finally { run.disabled = false; }
  };
  tab.replaceChildren(
    el("div", {class: "page-head"}, el("h1", {text: "Benchmark scenarios"}), el("p", {text: "Choose a scenario and a test generator, then run the buggy and corrected versions on your local PostgreSQL."})),
    el("div", {class: "panel"},
      el("div", {class: "controls"},
        el("label", {class: "field"}, "Scenario", scenario), el("label", {class: "field"}, "Test generator", generator), el("label", {class: "field"}, "Schedule budget", budget), run),
      desc, requirement),
    results,
    el("div", {class: "panel"}, el("h2", {text: "Deployment under test"}), source),
    el("div", {class: "panel"}, saved));
  return describe();
}
