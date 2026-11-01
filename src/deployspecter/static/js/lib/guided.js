import {codeCard, dataTable, el, getJson, postJson, shown} from "./dom.js";
import {playTimeline, renderCallouts, renderTimeline} from "./timeline.js";

const STEPS = [
  ["The deployment", deploymentStep],
  ["Operations and test", proposalStep],
  ["Validation", validationStep],
  ["The interleaving", interleavingStep],
  ["Verified violation", violationStep],
  ["Replay", replayStep],
  ["Corrected version", correctedStep],
];

const key = (s, variant = s.variant, preset = s.preset) => `${s.source}/${variant}/${preset}`;

function modeBadge(s) {
  return s.mode === "live"
    ? el("span", {class: "badge live", text: "Live local PostgreSQL"})
    : el("span", {class: "badge recorded", text: `Recorded on PostgreSQL ${s.data.postgres_version}, ${s.data.generated_at}`});
}

async function runFor(s, variant = "buggy", preset = s.preset) {
  const k = key(s, variant, preset);
  if (s.mode === "recorded") return s.data.runs[k];
  if (!s.live[k]) s.live[k] = await postJson("guided/stale-backfill/execute", {source: s.source, variant, preset});
  return s.live[k];
}

function errorBox(error, s) {
  const box = el("div", {class: "verdict warn", role: "alert"}, el("span", {class: "big", text: "Live execution is unavailable"}),
    el("p", {text: error.message}), el("p", {text: "The recorded walkthrough needs no database."}));
  if (s.allowLive) box.append(el("p", {}, el("button", {class: "btn", type: "button", onclick: () => setMode(s, "recorded"), text: "Switch to recorded"})));
  return box;
}

function loading(text = "Running on PostgreSQL…") {
  return el("p", {class: "status-line", role: "status"}, el("span", {class: "spinner", "aria-hidden": "true"}), text);
}

function header(number, title, intro) {
  return el("header", {}, el("div", {class: "eyebrow", text: `Step ${number} of ${STEPS.length}`}), el("h2", {tabindex: "-1", text: title}), el("p", {text: intro}));
}

function deploymentStep(s) {
  const {files} = s.data;
  return [
    header(1, "Three things share one table during a rollout", "The migration added display_name. The old release, the new release and a background job now run at the same time against the same rows."),
    el("div", {class: "cols-3"},
      codeCard("app_v1.py: old release", files.buggy["app_v1.py"]),
      codeCard("app_v2.py: new release", files.buggy["app_v2.py"]),
      codeCard("backfill.py: background job", files.buggy["backfill.py"])),
    codeCard("migration.sql", files.buggy["migration.sql"]),
    el("div", {class: "panel"}, el("h3", {text: "Independent requirement"}), el("p", {text: s.data.scenario.requirement}),
      el("p", {class: "note", text: "Written by hand and hidden from every test generator."})),
  ];
}

function proposalStep(s) {
  const {catalog, muse} = s.data;
  const lines = muse.proposal.actors.map((a) => `${a.name}  (${a.function})\n` + a.calls.map((c) => {
    const params = c.params.map((p) => `${p.name}=${p.source === "literal" ? JSON.stringify(p.value) : `${p.bind}[${p.row}].${p.column}`}`).join(", ");
    return `  ${c.statement_id}\n    ${params}`;
  }).join("\n")).join("\n\n");
  return [
    header(2, "From source code to a concrete test", "Static extraction lists every statement in the code without running it. Muse Spark then proposes a test using only those statements."),
    el("div", {class: "panel"}, el("h3", {text: `Extracted operations: ${catalog.length} statements, no AI involved`}),
      el("div", {class: "table-scroll", tabindex: "0"}, el("table", {class: "data"},
        el("thead", {}, el("tr", {}, el("th", {scope: "col", text: "Statement"}), el("th", {scope: "col", text: "SQL"}))),
        el("tbody", {}, ...catalog.map((c) => el("tr", {}, el("td", {}, el("code", {text: c.statement_id})), el("td", {}, el("code", {text: c.sql})))))))),
    el("div", {class: "panel"},
      el("div", {class: "status-line"}, el("span", {class: "badge ai", text: "Muse Spark"}), el("span", {class: "badge recorded", text: "Recorded response, no request made"})),
      el("h3", {text: muse.proposal.name}),
      el("p", {text: muse.proposal.rationale}),
      codeCard("generated test specification", lines),
      el("details", {class: "more", style: "margin-top:14px"}, el("summary", {text: "Conflict the model identified, and usage"}),
        el("p", {text: muse.conflict.risk_explanation}),
        el("p", {class: "note", style: "margin-top:10px", text: `${muse.model}, prompt ${muse.prompt_version}. ${muse.input_tokens} input and ${muse.output_tokens} output tokens, ${muse.latency_seconds}s when recorded. One of ${muse.tests_in_response} tests in this response. Saved in ${muse.response_file}.`}))),
  ];
}

function validationStep(s) {
  const {validator} = s.data.muse;
  return [
    header(3, "The validator decides what may run", "A generated test is data. It can't carry SQL, and it can only refer to statements that exist in the submitted code."),
    el("div", {class: `verdict ${validator.accepted ? "good" : "bad"}`}, el("span", {class: "big", text: validator.accepted ? "Accepted by the validator" : "Rejected by the validator"}),
      validator.reasons.length ? el("p", {text: validator.reasons.join("; ")}) : null),
    el("div", {class: "panel"}, el("h3", {text: "What it checked"}), el("ul", {class: "steps-check"},
      el("li", {text: "Every statement belongs to the function it names, in an order the code can take."}),
      el("li", {text: "Every statement the code always runs is present."}),
      el("li", {text: "Every parameter is supplied, and matches the code's data flow where that is statically known."}),
      el("li", {text: "Seed rows name real tables and columns, and PostgreSQL accepts them."}))),
    el("p", {class: "note", text: "Validation says a test is allowed to run. It says nothing about whether the code is correct. Only execution and the oracle decide that."}),
  ];
}

function sourceControls(s, rerender) {
  return el("div", {class: "field"}, "Test source", el("div", {class: "segmented", role: "group", "aria-label": "Test source"},
    ...Object.entries(s.data.sources).map(([id, label]) => el("button", {type: "button", "aria-pressed": String(s.source === id), onclick: () => { s.source = id; rerender(); }, text: label}))));
}

function presetControls(s, rerender) {
  return el("div", {class: "field"}, "Schedule", el("div", {class: "segmented", role: "group", "aria-label": "Schedule"},
    ...Object.entries(s.data.presets).map(([id, p]) => el("button", {type: "button", "aria-pressed": String(s.preset === id), onclick: () => { s.preset = id; rerender(); }, text: p.label}))));
}

function interleavingStep(s, rerender) {
  const body = el("div", {class: "stack", style: "display:grid;gap:16px"});
  const draw = async () => {
    if (s.mode === "live" && !s.live[key(s, "buggy")]) {
      const go = el("button", {class: "btn primary", type: "button", text: "Run this schedule on PostgreSQL"});
      go.onclick = async () => {
        body.replaceChildren(loading());
        try { await runFor(s); draw(); } catch (error) { body.replaceChildren(errorBox(error, s)); }
      };
      body.replaceChildren(el("p", {text: s.data.presets[s.preset].summary}), el("div", {}, go));
      return;
    }
    const run = await runFor(s);
    const timeline = renderTimeline(run, {caption: `${s.data.presets[s.preset].label}: ${s.data.sources[s.source]}`});
    const callouts = renderCallouts(run);
    let stop = () => {};
    const play = el("button", {class: "btn", type: "button", text: "Play step by step"});
    play.onclick = () => {
      stop();
      callouts.style.visibility = "hidden";
      stop = playTimeline(timeline, () => { callouts.style.visibility = "visible"; });
    };
    s.cancel = () => stop();
    body.replaceChildren(
      el("div", {class: "status-line"}, modeBadge(s), el("span", {text: s.data.presets[s.preset].summary})),
      timeline, callouts,
      el("div", {class: "status-line"}, play, s.mode === "live" ? el("span", {text: "This run just executed on your local PostgreSQL."}) : null),
      el("details", {class: "more"}, el("summary", {text: "Exact SQL, in order"}),
        el("div", {class: "table-scroll", tabindex: "0"}, el("table", {class: "data"},
          el("thead", {}, el("tr", {}, ...["Step", "Session", "SQL", "Parameters", "Rows"].map((h) => el("th", {scope: "col", text: h})))),
          el("tbody", {}, ...run.events.map((e) => el("tr", {}, el("td", {text: String(e.seq + 1)}), el("td", {text: e.actor}), el("td", {}, el("code", {text: e.sql})), el("td", {text: Object.keys(e.params).length ? JSON.stringify(e.params) : ""}), el("td", {text: e.rowcount === null || e.rowcount < 0 ? "" : String(e.rowcount)}))))))));
  };
  draw().catch((error) => body.replaceChildren(errorBox(error, s)));
  const note = s.source === "muse"
    ? "Muse Spark chose its own values for this test, so the names on screen differ from the reference test."
    : "The reference test uses readable names. Switch the test source to run Muse Spark's recorded test instead.";
  return [
    header(4, "The same code, three schedules", "Choose an order of operations. Only one of these orders loses data, and it isn't one a serial test would ever try."),
    el("div", {class: "panel controls"}, sourceControls(s, rerender), presetControls(s, rerender)),
    el("p", {class: "note", text: note}),
    body,
  ];
}

function violationStep(s, rerender) {
  const body = el("div", {style: "display:grid;gap:18px"});
  const draw = async () => {
    if (s.mode === "live" && !s.live[key(s, "buggy", "stale_read")]) {
      const go = el("button", {class: "btn primary", type: "button", text: "Run the failing schedule on PostgreSQL"});
      go.onclick = async () => {
        body.replaceChildren(loading());
        try { await runFor(s, "buggy", "stale_read"); draw(); } catch (error) { body.replaceChildren(errorBox(error, s)); }
      };
      body.replaceChildren(el("div", {}, go));
      return;
    }
    const run = await runFor(s, "buggy", "stale_read");
    const v = run.violations[0];
    const exploredKey = `${s.source}/buggy`;
    const explored = s.mode === "live" ? s.liveExplore[exploredKey] : s.data.exploration[exploredKey];
    const exploreBox = el("div", {class: "panel"});
    const drawExplore = (e) => exploreBox.replaceChildren(el("h3", {text: "Is this a one-off? Every interleaving of this test"}),
      el("div", {class: "compare"},
        el("div", {}, el("span", {text: "Schedules executed"}), `${e.executed} of ${e.space_size}`),
        el("div", {}, el("span", {text: "Violating schedules"}), String(e.violating)),
        el("div", {}, el("span", {text: "Serial orders that fail"}), e.violates_in_serial_order ? "At least one" : "None")));
    if (explored) drawExplore(explored);
    else {
      const go = el("button", {class: "btn", type: "button", text: "Explore every interleaving on PostgreSQL"});
      go.onclick = async () => {
        exploreBox.replaceChildren(loading("Exploring…"));
        try {
          s.liveExplore[exploredKey] = await postJson("guided/stale-backfill/explore", {source: s.source, variant: "buggy"});
          drawExplore(s.liveExplore[exploredKey]);
        } catch (error) { exploreBox.replaceChildren(errorBox(error, s)); }
      };
      exploreBox.append(el("h3", {text: "Is this a one-off?"}), go);
    }
    body.replaceChildren(
      el("div", {class: "verdict bad"}, el("span", {class: "big", text: "Verified violation"}),
        el("p", {}, el("strong", {text: `${v.requirement_id}: `}), v.requirement), el("p", {text: `The user committed ${shown(v.expected)} at step ${run.marks.user_commit_seq + 1}. The job's write at step ${run.marks.overwrite_seq + 1} replaced it, and the final value is ${shown(v.actual)}.`}),
        el("div", {class: "compare"},
          el("div", {}, el("span", {text: "Expected display_name"}), shown(v.expected)),
          el("div", {}, el("span", {text: "Actual, read from PostgreSQL"}), shown(v.actual)),
          el("div", {}, el("span", {text: "Needs concurrency?"}), "Yes: no serial order failed"))),
      el("div", {class: "panel"}, el("div", {class: "status-line"}, modeBadge(s)), el("h3", {text: "Final database state"}), dataTable(run.final_state.profiles)),
      exploreBox);
  };
  draw().catch((error) => body.replaceChildren(errorBox(error, s)));
  return [
    header(5, "An independent oracle confirms the loss", "The oracle replays what committed and compares it with the final row. It was written by hand, and the model never sees it."),
    body,
  ];
}

function replayStep(s) {
  const body = el("div", {style: "display:grid;gap:16px"});
  const k = key(s, "buggy", "stale_read");
  const recorded = s.data.runs[k];
  const describe = (r, when) => el("div", {class: "verdict good"}, el("span", {class: "big", text: r.reproduced ? "Reproduced" : "Did not reproduce"}),
    el("p", {text: `${when}: identical behaviour ${r.fingerprints_match} of ${r.attempts} replays, same violation ${r.violations_match} of ${r.attempts}.`}));
  const minimized = (run) => (run && run.minimized ? el("div", {class: "compare"},
    el("div", {}, el("span", {text: "Actors"}), String(run.minimized.after.actors)),
    el("div", {}, el("span", {text: "Steps"}), `${run.minimized.before.steps} before, ${run.minimized.after.steps} after`),
    el("div", {}, el("span", {text: "Context switches"}), String(run.minimized.after.preemptions))) : null);
  if (s.mode === "recorded") {
    body.append(el("div", {class: "status-line"}, modeBadge(s)), describe(recorded.replay, "Recorded when this walkthrough was built"), minimized(recorded));
    body.append(el("p", {class: "note", text: "To replay it yourself, run the project locally and use the live mode, or the replay command in the README."}));
  } else {
    const go = el("button", {class: "btn primary", type: "button", text: "Replay the minimized failure on PostgreSQL"});
    const out = el("div", {style: "display:grid;gap:14px"});
    go.onclick = async () => {
      go.disabled = true;
      out.replaceChildren(loading("Replaying…"));
      try {
        const run = await runFor(s, "buggy", "stale_read");
        const r = await postJson(`runs/${run.run_id}/replay`, {});
        out.replaceChildren(el("div", {class: "status-line"}, modeBadge(s)), describe(r, "Just now"), minimized(run), dataTable(r.final_state.profiles));
      } catch (error) { out.replaceChildren(errorBox(error, s)); }
      go.disabled = false;
    };
    body.append(el("div", {}, go), out);
  }
  return [
    header(6, "The failure is reproducible on demand", "The schedule, test and setup are saved as an artifact. Replay executes them again on real PostgreSQL and compares observed behaviour, not just the verdict."),
    body,
  ];
}

function lineDiff(a, b, tag) {
  const other = new Set(b.split("\n"));
  return Object.fromEntries(a.split("\n").filter((l) => !other.has(l)).map((l) => [l, tag]));
}

function correctedStep(s) {
  const body = el("div", {style: "display:grid;gap:18px"});
  const before = s.data.files.buggy["backfill.py"], after = s.data.files.corrected_backfill;
  const column = (title, run) => el("div", {class: "panel", style: "display:grid;gap:14px;min-width:0"}, el("h3", {style: "margin:0", text: title}),
    renderTimeline(run, {caption: `${title}: timeline`}), renderCallouts(run),
    el("div", {class: "compare"}, el("div", {}, el("span", {text: "Final display_name"}), shown(run.final_value)),
      el("div", {}, el("span", {text: "Oracle"}), run.violations.length ? "Requirement violated" : "No violation in this schedule")));
  const draw = async () => {
    const both = async () => [await runFor(s, "buggy", "stale_read"), await runFor(s, "corrected", "stale_read")];
    if (s.mode === "live" && !(s.live[key(s, "buggy", "stale_read")] && s.live[key(s, "corrected", "stale_read")])) {
      const go = el("button", {class: "btn primary", type: "button", text: "Run the same schedule on both versions"});
      go.onclick = async () => {
        body.replaceChildren(loading());
        try { await both(); draw(); } catch (error) { body.replaceChildren(errorBox(error, s)); }
      };
      body.replaceChildren(el("div", {}, go));
      return;
    }
    const [buggy, corrected] = await both();
    const explored = s.mode === "recorded" ? s.data.exploration[`${s.source}/corrected`] : null;
    body.replaceChildren(el("div", {class: "status-line"}, modeBadge(s)),
      column("Original job", buggy), column("Corrected job", corrected),
      explored ? el("p", {class: "note", text: `Every interleaving of this test on the corrected job: ${explored.violating} of ${explored.executed} violate. That covers this test's inputs only, and isn't a proof that the code is safe.`})
        : el("p", {class: "note", text: "This covers one schedule of this test. It isn't a proof that the code is safe."}));
  };
  draw().catch((error) => body.replaceChildren(errorBox(error, s)));
  return [
    header(7, "One condition closes this failure", "The corrected job writes only if display_name is still NULL. The row lock makes PostgreSQL re-check that condition after the user's commit."),
    el("div", {class: "cols-2"}, codeCard("backfill.py: original", before, lineDiff(before, after, "removed")), codeCard("backfill.py: corrected", after, lineDiff(after, before, "added"))),
    body,
  ];
}

function setMode(s, mode) {
  s.mode = mode;
  s.render();
}

export async function mountGuided(host, {allowLive = false, dataUrl = "static/data/walkthrough.json", onStep} = {}) {
  host.replaceChildren(el("p", {class: "load-state", role: "status", text: "Loading the walkthrough…"}));
  let data;
  try { data = await getJson(dataUrl); } catch (error) {
    host.replaceChildren(el("div", {class: "verdict warn", role: "alert"}, el("span", {class: "big", text: "The walkthrough could not load"}), el("p", {text: error.message})));
    return null;
  }
  const s = {data, mode: "recorded", source: "reference", preset: "stale_read", step: 0, live: {}, liveExplore: {}, allowLive, cancel: () => {}};
  const rail = el("nav", {class: "guided-rail", "aria-label": "Walkthrough steps"});
  const content = el("div", {style: "min-width:0"});
  host.replaceChildren(el("div", {class: "guided"}, rail, content));
  s.render = (focus = false) => {
    s.cancel();
    s.cancel = () => {};
    rail.replaceChildren(el("ol", {}, ...STEPS.map(([title], i) => el("li", {}, el("button", {
      type: "button", "aria-current": i === s.step ? "step" : null, text: title, onclick: () => go(i, true),
    })))));
    const bar = el("div", {class: "guided-bar"}, el("div", {class: "status-line"}, modeBadge(s)));
    if (allowLive) {
      bar.append(el("div", {class: "segmented", role: "group", "aria-label": "Data source"},
        el("button", {type: "button", "aria-pressed": String(s.mode === "recorded"), onclick: () => setMode(s, "recorded"), text: "Recorded"}),
        el("button", {type: "button", id: "guided-live", "aria-pressed": String(s.mode === "live"), onclick: () => setMode(s, "live"), text: "Live local PostgreSQL"})));
    }
    const [, builder] = STEPS[s.step];
    const nav = el("div", {class: "guided-nav"},
      el("button", {class: "btn", type: "button", disabled: s.step === 0 ? "" : null, onclick: () => go(s.step - 1, true), text: "Previous"}),
      s.step < STEPS.length - 1 ? el("button", {class: "btn primary", id: "guided-next", type: "button", onclick: () => go(s.step + 1, true), text: `Next: ${STEPS[s.step + 1][0]}`}) : el("span"));
    const section = el("div", {class: "guided-step"}, ...builder(s, () => s.render()), nav);
    content.replaceChildren(bar, section);
    if (focus) {
      section.querySelector("h2").focus({preventScroll: true});
      content.scrollIntoView({block: "start", behavior: "auto"});
    }
    if (onStep) onStep(s.step);
  };
  const go = (index, focus) => { s.step = Math.max(0, Math.min(STEPS.length - 1, index)); s.render(focus); };
  rail.onkeydown = (event) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    go(s.step + (event.key === "ArrowDown" ? 1 : -1), false);
    rail.querySelector('[aria-current="step"]').focus();
  };
  s.go = go;
  s.render();
  return s;
}
