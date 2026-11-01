import {el, fmt, getJson} from "../lib/dom.js";

const inlineSql = (r) => {
  let sql = r.sql || r.kind.toUpperCase();
  for (const [k, v] of Object.entries(r.params || {})) sql = sql.replaceAll(`%(${k})s`, typeof v === "string" ? `'${v}'` : fmt(v));
  return sql;
};

export function timelineTable(view) {
  const evidence = new Set((view.violations || []).flatMap((v) => v.evidence || []));
  const actors = [...new Set(view.timeline.map((r) => r.actor))];
  const head = el("tr", {}, el("th", {class: "num", text: "#"}), ...actors.map((a) => el("th", {class: "lane", text: a})));
  const rows = view.timeline.map((r) => {
    const cells = actors.map((a) => {
      if (a !== r.actor) return el("td");
      const cls = ["step"];
      if (r.kind !== "sql") cls.push("txn");
      if (r.blocked_by.length) cls.push("blocked");
      if (r.status === "error") cls.push("err");
      if (evidence.has(r.seq)) cls.push("evidence");
      const facts = [];
      if (r.kind === "sql" && r.rowcount !== null) facts.push(`rows ${r.rowcount}`);
      if (r.rows && r.rows.length) facts.push(`read ${JSON.stringify(r.rows)}`);
      if (r.blocked_by.length) facts.push(`waited for ${r.blocked_by.join(", ")}`);
      if (r.committed === true && r.kind !== "sql") facts.push("committed");
      if (r.status !== "ok") facts.push(r.status);
      if (r.error) facts.push(r.error);
      return el("td", {}, el("div", {class: cls.join(" ")}, el("code", {text: inlineSql(r)}),
        facts.length ? el("span", {class: "meta", text: facts.join(" · ")}) : null));
    });
    return el("tr", {}, el("td", {class: "num", text: r.seq}), ...cells);
  });
  return el("div", {class: "table-scroll", tabindex: "0"}, el("table", {class: "data"}, el("thead", {}, head), el("tbody", {}, ...rows)));
}

export function rowsTable(rows, goneRows = []) {
  if (!rows.length) return el("p", {class: "note", text: "No rows."});
  const cols = Object.keys(rows[0]);
  const gone = new Set(goneRows.map((r) => JSON.stringify(r)));
  return el("div", {class: "table-scroll", tabindex: "0"}, el("table", {class: "data"},
    el("thead", {}, el("tr", {}, ...cols.map((c) => el("th", {text: c})))),
    el("tbody", {}, ...rows.map((r) => el("tr", gone.has(JSON.stringify(r)) ? {class: "gone"} : {}, ...cols.map((c) => el("td", {text: fmt(r[c])})))))));
}

export function stateTables(state) {
  return el("div", {}, ...Object.entries(state || {}).map(([table, rows]) => el("div", {}, el("h3", {text: table}), rowsTable(rows))));
}

export function replayControl(runId) {
  const out = el("div", {class: "note"});
  const button = el("button", {class: "btn primary", text: "Replay this failure on PostgreSQL"});
  button.onclick = async () => {
    button.disabled = true;
    out.textContent = "Re-executing the saved schedule…";
    try {
      const response = await fetch(`/runs/${runId}/replay`, {method: "POST"});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const r = await response.json();
      const found = r.violations.map((v) => `${v.requirement_id}: expected ${fmt(v.expected)}, actual ${fmt(v.actual)}`).join("; ");
      out.replaceChildren(
        el("p", {}, el("span", {class: `badge ${r.reproduced ? "good" : "warn"}`, text: r.reproduced ? "Reproduced" : "Did not reproduce"})),
        el("p", {text: `Re-executed ${r.attempts} times just now. Identical behaviour ${r.fingerprints_match} of ${r.attempts}. Same violation ${r.violations_match} of ${r.attempts}. ${found}`}),
        stateTables(r.final_state));
    } catch (err) { out.textContent = `Replay failed: ${err.message}`; }
    button.disabled = false;
  };
  return el("div", {}, button, out);
}

export function verdict(view) {
  if (view.error) return el("div", {class: "verdict warn"}, el("strong", {class: "big", text: "Not executed"}), el("div", {text: view.error}));
  if (!view.schedules_executed) {
    const reason = view.rejections.length ? view.rejections[0].reasons[0] : "No test could be executed.";
    return el("div", {class: "verdict warn"}, el("strong", {class: "big", text: "Nothing executed"}), el("div", {text: reason}));
  }
  if (!view.detected) {
    return el("div", {class: "verdict good"}, el("strong", {class: "big", text: "No stated requirement violated in the schedules run"}),
      el("div", {text: `${view.schedules_executed} schedules on PostgreSQL. This is not a proof that the code is safe.`}));
  }
  const v = view.violations[0];
  return el("div", {class: "verdict bad"},
    el("strong", {class: "big", text: "Verified violation"}),
    el("div", {}, el("strong", {text: `${v.requirement_id}: `}), v.requirement),
    el("div", {text: v.explanation}),
    el("div", {class: "compare"},
      el("div", {}, el("span", {text: "Expected"}), fmt(v.expected)),
      el("div", {}, el("span", {text: "Actual in PostgreSQL"}), fmt(v.actual)),
      el("div", {}, el("span", {text: "First failure"}), `schedule ${view.schedules_to_first_failure} of ${view.schedules_executed}`),
      el("div", {}, el("span", {text: "Needs concurrency?"}), view.serial_order_violation ? "No: a serial order also fails" : "Yes: no serial order failed")));
}

export function specDetails(view) {
  const d = el("details", {class: "more"}, el("summary", {text: `Test specifications from ${view.generator}: ${view.tests_valid} valid, ${view.rejections.length} rejected`}));
  for (const t of view.tests) {
    if (!t.spec) continue;
    d.append(el("h3", {text: t.name}));
    if (t.spec.rationale) d.append(el("p", {class: "note", text: t.spec.rationale}));
    d.append(el("pre", {class: "code", tabindex: "0", text: t.spec.actors.map((a) => `${a.name}  (${a.function})\n${a.steps.map((s) => "  " + s).join("\n")}`).join("\n\n")}));
    d.append(el("p", {class: "note", text: `Seed: ${JSON.stringify(t.spec.seed)}`}));
    d.append(el("p", {class: "note", text: `${t.executed} of ${t.space_size} interleavings run, ${t.violations} violating`}));
    if (Object.keys(t.renamed_actors).length) d.append(el("p", {class: "note", text: `Actor names normalized: ${JSON.stringify(t.renamed_actors)}`}));
    for (const i of t.invariants) {
      if (i.failed_without_oracle_violation > 0 && !i.screened_out) d.append(el("p", {class: "note", text: `Unconfirmed model hypothesis, not a finding: ${i.description}`}));
    }
  }
  for (const r of view.rejections) d.append(el("p", {class: "note", text: `Rejected ${r.test}: ${r.reasons.join("; ")}`}));
  return d;
}

export function resultBody(view, runId) {
  const box = el("div", {class: "stack"}, verdict(view));
  if (view.replay) {
    const r = view.replay, m = view.minimized_replay;
    box.append(el("div", {class: "stack"}, replayControl(runId),
      el("p", {class: "note", text: `When found: original ${r.fingerprints_match} of ${r.attempts} identical, minimized ${m ? m.fingerprints_match + " of " + m.attempts : "n/a"} identical. Reduced from ${view.minimized.before.steps} to ${view.minimized.after.steps} steps.`})));
  }
  if (view.timeline) {
    box.append(el("details", {class: "more", open: ""}, el("summary", {text: view.detected ? "Failing interleaving (minimized)" : "Example interleaving"}), timelineTable(view)));
    box.append(el("details", {class: "more", open: ""}, el("summary", {text: "Database state before and after"}), el("div", {class: "cols-2"},
      el("div", {}, el("h3", {text: "Before"}), stateTables(view.initial_state)),
      el("div", {}, el("h3", {text: "After (read from PostgreSQL)"}), stateTables(view.final_state)))));
  }
  box.append(specDetails(view));
  if (view.assumptions) {
    box.append(el("details", {class: "more"}, el("summary", {text: "Oracle assumptions"}),
      el("ul", {class: "tight"}, ...view.assumptions.map((a) => el("li", {text: a})))));
  }
  if (view.report_markdown) {
    box.append(el("details", {class: "more"}, el("summary", {text: "Replay command and manual steps"}),
      el("pre", {class: "code", tabindex: "0", text: view.replay_command}), el("pre", {class: "code", tabindex: "0", text: view.report_markdown})));
  }
  for (const n of view.notes) box.append(el("p", {class: "note", text: n}));
  return box;
}

export async function pollRun(runId, onDone, onFail) {
  for (;;) {
    const record = await getJson(`/runs/${runId}`);
    if (record.status === "finished") return onDone(record.result, runId);
    if (record.status === "failed") return onFail(record.error);
    await new Promise((r) => setTimeout(r, 300));
  }
}

export async function runCard(url, body, card, render) {
  const target = card.querySelector(".body");
  target.replaceChildren(el("p", {class: "status-line", role: "status"}, el("span", {class: "spinner", "aria-hidden": "true"}), "Running on PostgreSQL…"));
  const post = await fetch(url, {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify(body)});
  if (!post.ok) { target.replaceChildren(el("div", {class: "verdict warn", role: "alert"}, el("span", {class: "big", text: "The run could not start"}), el("p", {text: `The server answered with status ${post.status}.`}))); return; }
  const {run_id} = await post.json();
  const showError = (message) => target.replaceChildren(el("div", {class: "verdict warn", role: "alert"}, el("span", {class: "big", text: "The run did not finish"}), el("p", {text: message})));
  try {
    await pollRun(run_id, (view, id) => target.replaceChildren(render(view, id)), showError);
  } catch (error) { showError(error.message); }
}

export function variantCards(host) {
  const cards = [["buggy", "Buggy version"], ["corrected", "Corrected version"]].map(([variant, title]) => {
    const card = el("section", {class: "panel result-card", "data-variant": variant}, el("h2", {text: title}), el("div", {class: "body note", text: "Waiting…"}));
    host.append(card);
    return [variant, card];
  });
  return cards;
}

export function sourcePanel(src, labels) {
  const files = new Set([...Object.keys(src.variants.buggy), ...Object.keys(src.variants.corrected)]);
  return [...files].sort().map((name) => {
    const differs = src.differing_files.includes(name);
    const d = el("details", {class: "source-file"}, el("summary", {text: differs ? `${name} (differs between versions)` : name}));
    if (differs) d.setAttribute("open", "");
    d.append(differs
      ? el("div", {class: "cols-2"},
          el("div", {}, el("h3", {text: labels[0]}), el("pre", {class: "code", tabindex: "0", text: src.variants.buggy[name]})),
          el("div", {}, el("h3", {text: labels[1]}), el("pre", {class: "code", tabindex: "0", text: src.variants.corrected[name]})))
      : el("pre", {class: "code", tabindex: "0", text: src.variants.buggy[name]}));
    return d;
  });
}
