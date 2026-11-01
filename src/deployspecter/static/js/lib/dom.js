// Data is written with textContent only. Model output and SQL are never parsed as HTML.
export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node[key] = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) if (child !== null && child !== undefined) node.append(child);
  return node;
}

export const $ = (id) => document.getElementById(id);
export const fmt = (v) => (v === null || v === undefined ? "n/a" : typeof v === "object" ? JSON.stringify(v) : String(v));
export const shown = (v) => (v === null || v === undefined ? "NULL" : String(v));
export const fmtRows = (rows) => (rows.length
  ? rows.map((r) => Object.entries(r).map(([k, v]) => `${k} = ${fmt(v)}`).join(", ")).join("; ")
  : "none");

export async function getJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    const error = new Error(detail.detail || `The server answered with status ${response.status}.`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

export const postJson = (url, body) => getJson(url, {
  method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify(body),
});

export function codeCard(name, text, highlight = {}) {
  const pre = el("pre", {class: "code", tabindex: "0"});
  for (const line of text.replace(/\n$/, "").split("\n")) {
    pre.append(el("span", {class: `code-line ${highlight[line] || ""}`.trim(), text: line || " "}));
  }
  return el("div", {class: "code-card"}, el("header", {}, el("span", {text: name})), pre);
}

export function dataTable(rows) {
  if (!rows.length) return el("p", {class: "note", text: "No rows."});
  const columns = Object.keys(rows[0]);
  return el("div", {class: "table-scroll", tabindex: "0"}, el("table", {class: "data"},
    el("thead", {}, el("tr", {}, ...columns.map((c) => el("th", {scope: "col", text: c})))),
    el("tbody", {}, ...rows.map((r) => el("tr", {}, ...columns.map((c) => el("td", {text: fmt(r[c])})))))));
}

export const prefersReducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
