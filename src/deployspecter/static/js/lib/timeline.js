import {el, prefersReducedMotion, shown} from "./dom.js";

const LANES = [["job", "Background job"], ["user", "New application"]];

function opClass(event, marks) {
  if (event.seq === marks.overwrite_seq) return "overwrite";
  if (event.seq === marks.overwrite_commit_seq) return "overwrite-commit";
  if (event.seq === marks.user_commit_seq) return "commit-user";
  if (event.kind !== "sql") return "txn";
  if (event.rows === null && event.rowcount === 0) return "noop";
  if (event.rows === null) return "write";
  return "";
}

// A real table, so screen readers get lane and step headers. Colour is never the only signal: labels carry the meaning.
export function renderTimeline(run, {caption = "Transaction timeline"} = {}) {
  const marks = run.marks;
  const steps = run.events;
  const head = el("tr", {}, el("th", {class: "tl-lane", scope: "col", text: "Step"}),
    ...steps.map((e) => el("th", {scope: "col", text: String(e.seq + 1)})));
  const laneRow = ([role, title]) => el("tr", {}, el("th", {class: "tl-lane", scope: "row", text: title}),
    ...steps.map((e) => el("td", {"data-step": e.seq},
      e.role === role ? el("div", {class: `tl-op ${opClass(e, marks)}`, title: e.sql, text: e.label.replaceAll("_", "_\u200b")}) : null)));
  let previous = run.initial_state.profiles?.[0]?.display_name ?? null;
  const track = el("tr", {class: "tl-track"}, el("th", {class: "tl-lane", scope: "row", text: "Committed display_name"}),
    ...steps.map((e) => {
      const changed = e.committed_value !== previous;
      previous = e.committed_value;
      const lost = e.seq === marks.overwrite_commit_seq;
      const cls = lost ? "lost" : changed ? "changed" : "";
      return el("td", {"data-step": e.seq}, el("span", {class: `tl-value ${cls}`.trim(), text: shown(e.committed_value)}));
    }));
  const table = el("table", {class: "tl"}, el("caption", {class: "sr-only", text: caption}),
    el("thead", {}, head), el("tbody", {}, ...LANES.map(laneRow), track));
  return el("div", {class: "tl-scroll", tabindex: "0", role: "region", "aria-label": caption}, table);
}

export function renderCallouts(run) {
  const {marks, events} = run;
  if (marks.overwrite_seq === null) {
    const guarded = events.find((e) => e.kind === "sql" && e.rows === null && e.rowcount === 0);
    const skipped = events.find((e) => e.status === "skipped_no_rows");
    const text = guarded
      ? `Step ${guarded.seq + 1}: the job's write matched no row, because its condition found the value already set.`
      : skipped
        ? `Step ${skipped.seq + 1}: the job read no rows, so it had nothing to write.`
        : "The job's write landed before the user's update, so the user's value is the one that remains.";
    return el("div", {class: "tl-callouts"}, el("p", {class: "tl-callout good"}, el("strong", {text: `Final value: ${shown(run.final_value)}`}), text));
  }
  const committed = events[marks.user_commit_seq].committed_value;
  const final = events[marks.overwrite_commit_seq].committed_value;
  return el("div", {class: "tl-callouts"},
    el("p", {class: "tl-callout good"}, el("strong", {text: `Step ${marks.user_commit_seq + 1}: ${shown(committed)} is committed`}),
      "The user's update succeeds and is durable."),
    el("p", {class: "tl-callout bad"}, el("strong", {text: `Step ${marks.overwrite_seq + 1}: the job writes ${shown(final)}`}),
      "It uses the value it read earlier, with no check for a newer one."),
    el("p", {class: "tl-callout bad"}, el("strong", {text: `Step ${marks.overwrite_commit_seq + 1}: ${shown(committed)} is gone`}),
      `The job commits, and ${shown(final)} replaces the user's value.`));
}

// Reveals steps in order. Does nothing visual under reduced motion, where the whole timeline stays visible.
export function playTimeline(container, onDone) {
  const cells = [...container.querySelectorAll("[data-step]")];
  if (prefersReducedMotion() || !cells.length) { if (onDone) onDone(); return () => {}; }
  const last = Math.max(...cells.map((c) => Number(c.dataset.step)));
  const apply = (step) => cells.forEach((c) => c.classList.toggle("is-future", Number(c.dataset.step) > step));
  let step = -1;
  apply(step);
  const timer = setInterval(() => {
    step += 1;
    apply(step);
    if (step >= last) { clearInterval(timer); if (onDone) onDone(); }
  }, 620);
  return () => { clearInterval(timer); apply(last); };
}
