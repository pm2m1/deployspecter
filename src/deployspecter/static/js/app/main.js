import {$, el, getJson} from "../lib/dom.js";
import {mountGuided} from "../lib/guided.js";
import {renderComparison} from "./comparison.js";
import {renderGitlab} from "./gitlab.js";
import {renderPipeline} from "./pipeline.js";
import {renderScenarios, setSavedReports} from "./scenarios.js";

const TABS = [
  ["guided", "Stale-backfill demo"],
  ["scenarios", "Benchmark scenarios"],
  ["gitlab", "GitLab-derived case"],
  ["pipeline", "Muse Spark pipeline"],
  ["comparison", "AI vs non-AI"],
];
const ids = TABS.map(([id]) => id);

function selectTab(name, {focus = false} = {}) {
  for (const id of ids) {
    const selected = id === name;
    $(`tab-${id}`).hidden = !selected;
    const button = $(`btn-${id}`);
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
    if (selected && focus) button.focus();
  }
  if (location.hash.slice(1) !== name) history.replaceState(null, "", `#${name}`);
}

function buildTabs() {
  for (const [id, title] of TABS) {
    $("tabs").append(el("button", {id: `btn-${id}`, type: "button", role: "tab", "aria-controls": `tab-${id}`, text: title, onclick: () => selectTab(id)}));
  }
  $("tabs").onkeydown = (event) => {
    const forward = ["ArrowDown", "ArrowRight"].includes(event.key), back = ["ArrowUp", "ArrowLeft"].includes(event.key);
    if (!forward && !back) return;
    event.preventDefault();
    const current = ids.indexOf(location.hash.slice(1));
    selectTab(ids[(current + (forward ? 1 : ids.length - 1)) % ids.length], {focus: true});
  };
  window.addEventListener("hashchange", () => {
    const name = location.hash.slice(1);
    if (ids.includes(name)) selectTab(name);
  });
  const start = location.hash.slice(1);
  selectTab(ids.includes(start) ? start : "guided");
}

function panelError(id, error) {
  $(`tab-${id}`).replaceChildren(el("div", {class: "verdict warn", role: "alert"}, el("span", {class: "big", text: "This section could not load"}), el("p", {text: error.message})));
}

async function checkHealth() {
  const dot = $("health-dot");
  try {
    const health = await getJson("health");
    const up = health.database.reachable;
    dot.className = `health-dot ${up ? "ok" : "down"}`;
    $("health").textContent = up ? `PostgreSQL ${health.database.server_version.split(" ")[0]} reachable` : "PostgreSQL is not reachable. Recorded views still work.";
  } catch {
    dot.className = "health-dot down";
    $("health").textContent = "Server status unknown";
  }
}

async function init() {
  buildTabs();
  checkHealth();
  $("tab-guided").replaceChildren(
    el("div", {class: "page-head"}, el("h1", {text: "A background job erases a committed update"}), el("p", {text: "Walk through one concurrency failure from source code to a replayed result. Use recorded data, or run each step on your local PostgreSQL."})),
    el("div", {id: "guided-host"}));
  mountGuided($("guided-host"), {allowLive: true});
  const load = async (id, work) => { try { await work(); } catch (error) { panelError(id, error); } };
  const [list, data, usage] = await Promise.all([getJson("scenarios"), getJson("comparison"), getJson("usage")]);
  setSavedReports(data);
  await Promise.all([
    load("comparison", async () => renderComparison(data, usage)),
    load("gitlab", renderGitlab),
    load("scenarios", () => renderScenarios(list)),
    load("pipeline", () => renderPipeline(list)),
  ]);
}
init().catch((error) => { $("health").textContent = `The workbench could not load: ${error.message}`; });
