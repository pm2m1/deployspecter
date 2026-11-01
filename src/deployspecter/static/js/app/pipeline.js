import {$, el, fmt, getJson} from "../lib/dom.js";
import {resultBody, runCard, verdict} from "./components.js";

function proposalCard(t) {
  const p = t.proposal;
  const calls = p.actors.map((a) => `${a.name}  (${a.function})\n` + a.calls.map((c) => {
    const params = c.params.map((x) => `${x.name}=${x.source === "literal" ? JSON.stringify(x.value) : `${x.bind}[${x.row}].${x.column}${x.transform !== "none" ? ` ${x.transform}` : ""}`}`).join(", ");
    return `  ${c.statement_id}${params ? `  ${params}` : ""}${c.bind_as ? `  → ${c.bind_as}` : ""}`;
  }).join("\n")).join("\n\n");
  return el("div", {},
    el("h3", {}, `${p.name} `, el("span", {class: `badge ${t.accepted ? "good" : "bad"}`, text: t.accepted ? "Accepted by validator" : "Rejected by validator"})),
    p.rationale ? el("p", {class: "note", text: p.rationale}) : null,
    el("pre", {class: "code", tabindex: "0", text: calls}),
    el("p", {class: "note", text: `Seed rows: ${JSON.stringify(p.seed.map((s) => ({[s.table]: Object.fromEntries(s.values.map((v) => [v.column, v.value]))})))}`}),
    p.invariants.length ? el("details", {class: "more"}, el("summary", {text: `${p.invariants.length} candidate invariants (hypotheses, never findings)`}),
      el("ul", {class: "tight"}, ...p.invariants.map((i) => el("li", {text: `${i.kind}: ${i.description}`})))) : null,
    t.rejections.length ? el("ul", {class: "tight"}, ...t.rejections.map((r) => el("li", {text: r}))) : null,
    Object.keys(t.renamed_actors).length ? el("p", {class: "note", text: `Actor names normalized: ${JSON.stringify(t.renamed_actors)}`}) : null,
    el("details", {class: "more"}, el("summary", {text: "Raw recorded proposal"}), el("pre", {class: "code", tabindex: "0", text: JSON.stringify(p, null, 2)})));
}

export function renderPipeline(list) {
  const tab = $("tab-pipeline");
  const scenario = el("select", {id: "ai-scenario"}, ...list.map((s) => el("option", {value: s.id, text: `${s.title} (${s.split})`})));
  const variant = el("select", {id: "ai-variant"}, el("option", {value: "buggy", text: "Buggy version"}), el("option", {value: "corrected", text: "Corrected version"}));
  const repeat = el("select", {id: "ai-repeat"});
  // Offers only recordings that exist. The API still answers a clear 404 for any other request.
  const fillRecordings = async () => {
    const available = await getJson(`/scenarios/${scenario.value}/recordings`);
    const runs = available[variant.value];
    const current = repeat.value === "" ? null : Number(repeat.value);
    repeat.replaceChildren(...runs.map((r) => el("option", {value: r, text: `Recorded run ${r}`})));
    if (runs.length) repeat.value = String(runs.includes(current) ? current : runs[0]);
    return runs.length > 0;
  };
  const body = el("div", {});
  const load = async () => {
    body.textContent = "Loading the recorded response…";
    if (!(await fillRecordings())) {
      body.replaceChildren(el("div", {class: "verdict warn"}, el("strong", {class: "big", text: "No recording"}), el("div", {text: "No recorded model response exists for this case."})));
      return;
    }
    let d;
    try { d = await getJson(`/scenarios/${scenario.value}/ai-pipeline?variant=${variant.value}&repeat=${repeat.value}`); }
    catch (err) { body.replaceChildren(el("div", {class: "verdict warn", role: "alert"}, el("strong", {class: "big", text: err.status === 404 ? "No recording" : "This walkthrough needs PostgreSQL"}), el("div", {text: err.message}))); return; }
    const scored = d.as_scored_in_saved_benchmark;
    const usage = d.attempts.map((a) => `${a.kind}: ${a.status}, ${a.input_tokens} input and ${a.output_tokens} output tokens, ${a.latency_seconds}s${a.parse_error ? ", failed parsing" : ""}`).join("; ");
    const result = el("div", {class: "panel", "data-result": "ai"}, el("div", {class: "body note", text: "Not run yet. Execute the recorded tests above to see the oracle's result."}));
    const execute = el("button", {class: "btn primary", id: "ai-execute", text: "Execute these recorded tests on PostgreSQL"});
    execute.onclick = async () => {
      execute.disabled = true;
      await runCard(`/scenarios/${scenario.value}/run`, {variant: variant.value, generator: "ai", repeat: Number(repeat.value)}, result, resultBody);
      execute.disabled = false;
    };
    body.replaceChildren(el("ol", {class: "flow-steps"},
      el("li", {}, el("div", {}, el("div", {class: "who", text: "Submitted code"}), el("h3", {text: "Source code and migration"}),
        ...Object.entries(d.files).map(([n, text]) => el("details", {class: "source-file"}, el("summary", {text: n}), el("pre", {class: "code", tabindex: "0", text}))))),
      el("li", {}, el("div", {}, el("div", {class: "who", text: "Static extraction, no AI"}), el("h3", {text: `Operation catalog: ${d.catalog.length} statements`}),
        el("div", {class: "table-scroll", tabindex: "0"}, el("table", {class: "data"}, el("thead", {}, el("tr", {}, ...["statement_id", "Path", "SQL"].map((h) => el("th", {text: h})))),
          el("tbody", {}, ...d.catalog.map((c) => el("tr", {}, el("td", {}, el("code", {text: c.statement_id})), el("td", {text: c.required ? "required" : "optional"}), el("td", {}, el("code", {text: c.sql}))))))))),
      el("li", {}, el("div", {}, el("div", {class: "who", text: "Muse Spark"}),
        el("h3", {}, "Recorded test proposal ", el("span", {class: "badge recorded", text: "Recorded response, no request made"})),
        el("p", {class: "note", text: `${d.model}, prompt ${d.prompt_version}. ${usage}.`}),
        d.conflicts.length ? el("details", {class: "more", open: ""}, el("summary", {text: `Application-aware conflicts it identified: ${d.conflicts.length}`}),
          el("ul", {class: "tight"}, ...d.conflicts.map((c) => el("li", {text: `${c.table}.${c.column}: ${c.risk_explanation}`})))) : null,
        ...d.tests.map(proposalCard),
        d.tests.length ? null : el("p", {class: "note", text: "This recording contains no usable tests."}))),
      el("li", {}, el("div", {}, el("div", {class: "who", text: "Validator, no AI"}), el("h3", {text: "What the validator allowed"}),
        el("p", {text: `Current validator: ${d.tests.filter((t) => t.accepted).length} of ${d.tests.length} tests accepted. A proposal cannot contain SQL, and every statement must exist in the submitted source.`}),
        scored ? el("p", {class: "note", text: `As scored in the saved benchmark (${scored.generated_at}): first response ${fmt(scored.first_attempt_valid)} of ${fmt(scored.first_attempt_proposed)} valid, final ${scored.tests_valid} of ${scored.tests_proposed}.${scored.rejections.length ? ` Rejected then: ${scored.rejections.map((r) => r.slice(0, 160)).join(" | ")}` : ""}`}) : null)),
      el("li", {}, el("div", {}, el("div", {class: "who", text: "PostgreSQL, no AI"}), el("h3", {text: "Execution over real interleavings"}), execute)),
      el("li", {}, el("div", {}, el("div", {class: "who", text: "Independent oracle, no AI"}), el("h3", {text: "Verified result and replay"}),
        el("p", {class: "note", text: "Only the oracle can produce a finding. It was written by hand and is hidden from every generator."}), result))));
  };
  scenario.onchange = variant.onchange = repeat.onchange = load;
  tab.replaceChildren(
    el("div", {class: "page-head"}, el("h1", {text: "Muse Spark pipeline"}), el("p", {text: "Follow one recorded model response from source code to a verified result. No model request is made here."})),
    el("div", {class: "panel"},
      el("div", {class: "chain"}, ...["Source code", "Operation catalog", "Muse Spark proposal", "Validator", "PostgreSQL", "Oracle", "Reproducible failure"].flatMap((s, i) => [i ? "→" : "", el("span", {text: s})])),
      el("ul", {},
        el("li", {text: "Muse Spark proposes application-aware tests and candidate invariants from the code."}),
        el("li", {text: "The validator rejects anything unsupported or unsafe. Generated output cannot carry SQL."}),
        el("li", {text: "PostgreSQL executes the accepted tests over bounded interleavings."}),
        el("li", {text: "The independent oracle alone decides whether a verified violation exists."})),
      el("p", {class: "note", text: "Muse Spark did not find more defects than the non-LLM baselines. See the comparison tab."})),
    el("div", {class: "panel"}, el("div", {class: "controls"},
      el("label", {class: "field"}, "Scenario", scenario), el("label", {class: "field"}, "Version", variant), el("label", {class: "field"}, "Recording", repeat))),
    el("div", {class: "panel"}, el("h2", {text: "One recorded response, stage by stage"}), body));
  return load();
}
