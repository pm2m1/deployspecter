import {$, el, fmtRows, getJson} from "../lib/dom.js";
import {replayControl, rowsTable, runCard, sourcePanel, timelineTable, variantCards, verdict} from "./components.js";

function documentedResult(view, runId) {
  const f = view.documented;
  const box = el("div", {});
  if (view.error || !f) { box.append(verdict(view)); return box; }
  const lost = f.destination_row_disappeared.length > 0;
  box.append(lost
    ? el("div", {class: "verdict bad"},
        el("strong", {class: "big", text: "The destination row disappeared while a source row remains"}),
        el("div", {class: "compare"},
          el("div", {}, el("span", {text: `Removed from ${f.destination_table}`}), fmtRows(f.destination_row_disappeared)),
          el("div", {}, el("span", {text: `Still in ${f.source_table}`}), fmtRows(f.source_rows_remaining)),
          el("div", {}, el("span", {text: "Requirement"}), view.violations ? view.violations[0].requirement : "n/a")))
    : el("div", {class: "verdict good"},
        el("strong", {class: "big", text: "The destination row was kept"}),
        el("div", {class: "compare"},
          el("div", {}, el("span", {text: `In ${f.destination_table}`}), fmtRows(f.destination_after)),
          el("div", {}, el("span", {text: `Still in ${f.source_table}`}), fmtRows(f.source_after))),
        el("div", {text: `No stated requirement violated in this test's ${f.schedules} schedules. This is not a proof of safety.`})));
  box.append(el("h3", {text: "Timeline"}), timelineTable(view));
  box.append(el("div", {class: "cols-2"},
    el("div", {}, el("h3", {text: `${f.source_table} (source)`}), el("p", {class: "note", text: "Before"}), rowsTable(f.source_before), el("p", {class: "note", text: "After"}), rowsTable(f.source_after)),
    el("div", {}, el("h3", {text: `${f.destination_table} (destination)`}), el("p", {class: "note", text: "Before"}), rowsTable(f.destination_before, f.destination_row_disappeared), el("p", {class: "note", text: "After"}), rowsTable(f.destination_after))));
  if (f.fails_in_serial_order) {
    box.append(el("h3", {text: "Why this does not need concurrency"}),
      el("p", {text: `Measured here: the smallest failing test is ${f.actors_in_minimal_failure} actor and ${f.transactions_in_minimal_failure} transaction, with no other session involved. ${f.violating_schedules} of ${f.schedules} schedules fail, including serial orders. The trigger's delete branch removes the pair without checking for remaining source rows, so ordering plays no part. This makes it a migration semantics defect rather than a race.`}));
  }
  if (view.replay) box.append(el("h3", {text: "Replay"}), replayControl(runId), el("pre", {class: "code", tabindex: "0", text: view.replay_command}));
  return box;
}

export async function renderGitlab() {
  const tab = $("tab-gitlab");
  const [info] = await getJson("/external-cases");
  const src = await getJson(`/external-cases/${info.id}/source`);
  const results = el("div", {class: "result-grid"});
  const run = el("button", {class: "btn primary", text: "Run the documented defect on both versions"});
  run.onclick = async () => {
    run.disabled = true;
    results.replaceChildren();
    const url = `/external-cases/${info.id}/documented-defect/run`;
    try {
      for (const [variant, card] of variantCards(results)) {
        card.querySelector("h2").textContent = variant === "buggy" ? "Original trigger" : "GitLab's corrected logic";
        await runCard(url, {variant}, card, documentedResult);
      }
    } finally { run.disabled = false; }
  };
  tab.replaceChildren(
    el("div", {class: "page-head"}, el("h1", {text: info.title}), el("p", {text: "A serial migration defect, published by GitLab and reproduced in an independently written minimal fixture. It needs no concurrency, unlike the stale-backfill walkthrough."})),
    el("div", {class: "panel"},
      el("h2", {}, "What GitLab documented ", el("span", {class: "badge info", text: "External case, separate from the benchmark"})),
      el("ul", {class: "tight"}, ...info.documented_by_gitlab.map((t) => el("li", {text: t}))),
      el("p", {}, ...info.links.flatMap((l, i) => [i ? " · " : "", el("a", {href: l.url, target: "_blank", rel: "noreferrer", text: l.label})]))),
    el("div", {class: "panel"},
      el("h2", {text: "Minimal fixture"}),
      el("p", {class: "note", text: "Independently written tables and trigger that model the documented behaviour. Each file is shown exactly as it runs."}),
      ...sourcePanel(src, ["Original trigger", "GitLab's corrected logic"])),
    el("div", {class: "panel"},
      el("h2", {text: "Reproduce it"}),
      el("p", {class: "note", text: "Runs on PostgreSQL: seeds two access levels for one user and project, deletes one, then reads both tables."}),
      run),
    results,
    el("div", {class: "panel"},
      el("h2", {text: "Limits of this example"}),
      el("ul", {class: "tight"}, ...info.limitations.map((t) => el("li", {text: t})))));
}
