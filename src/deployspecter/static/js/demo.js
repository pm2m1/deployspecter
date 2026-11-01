import {mountGuided} from "./lib/guided.js";

const stepFromHash = () => Number(location.hash.slice(1)) - 1;
const start = stepFromHash();
mountGuided(document.getElementById("walkthrough"), {
  onStep: (step) => history.replaceState(null, "", `#${step + 1}`),
}).then((s) => {
  if (!s) return;
  if (start > 0) s.go(start, false);
  window.addEventListener("hashchange", () => {
    if (stepFromHash() >= 0) s.go(stepFromHash(), false);
  });
});
