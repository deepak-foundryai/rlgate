"""The bank view as one self-contained HTML file: filters by verdict, rule and task type,
shareable without a server (no CDN, no build step; inline CSS and a little vanilla JS).

The data is the same JSON as ``--format json`` (core/report.bank_dict), embedded and
rendered client-side. Product output that the user opens in a browser.
"""

from __future__ import annotations

import datetime as _dt
import json

from rlgate.core.report import TaskView, bank_dict


def bank_html(views: list[TaskView], target: str) -> str:
    data = bank_dict(views, target)
    data["generated"] = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    blob = json.dumps(data).replace("<", "\\u003c")
    return _TEMPLATE.replace("/*DATA*/", blob)


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>rlgate report</title>
<style>
:root{--bg:#f7f7f8;--panel:#fff;--ink:#1a1a1e;--muted:#6a6a75;--line:#e3e3e8;
 --ready:#15803d;--ready-bg:#dcfce7;--fix:#a16207;--fix-bg:#fef9c3;--broken:#b91c1c;
 --broken-bg:#fee2e2;--judg:#7e22ce;--chip:#eeeef2;--accent:#4f46e5}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#111115;--panel:#1b1b21;
 --ink:#e8e8ee;--muted:#9a9aa5;--line:#2c2c34;--ready:#4ade80;--ready-bg:#12301d;--fix:#facc15;
 --fix-bg:#2f2a10;--broken:#f87171;--broken-bg:#3a1414;--judg:#c084fc;--chip:#26262e;
 --accent:#818cf8}}
:root[data-theme="dark"]{--bg:#111115;--panel:#1b1b21;--ink:#e8e8ee;--muted:#9a9aa5;
 --line:#2c2c34;--ready:#4ade80;--ready-bg:#12301d;--fix:#facc15;--fix-bg:#2f2a10;
 --broken:#f87171;--broken-bg:#3a1414;--judg:#c084fc;--chip:#26262e;--accent:#818cf8}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--muted);margin-bottom:20px;overflow-wrap:anywhere}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;
 margin-bottom:20px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px}
.card b{display:block;font-size:22px}
.card span{color:var(--muted);font-size:12px}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}
select,input{background:var(--panel);color:var(--ink);border:1px solid var(--line);
 border-radius:8px;padding:6px 8px;font:inherit;max-width:100%}
input{flex:1;min-width:160px}
section{background:var(--panel);border:1px solid var(--line);border-radius:10px;
 padding:14px;margin-bottom:16px}
h2{font-size:15px;margin:0 0 10px}
.common li{margin:4px 0}
.table{width:100%;border-collapse:collapse}
.table th,.table td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);
 vertical-align:top}
.table th{font-size:12px;color:var(--muted);font-weight:600}
.num{text-align:right!important;font-variant-numeric:tabular-nums}
.v{display:inline-block;padding:1px 8px;border-radius:999px;font-weight:600;font-size:12px;
 white-space:nowrap}
.v.ready{background:var(--ready-bg);color:var(--ready)}
.v.needs_fixes{background:var(--fix-bg);color:var(--fix)}
.v.broken{background:var(--broken-bg);color:var(--broken)}
.v.not_checked{background:var(--chip);color:inherit}
tr.task{cursor:pointer}
tr.task:hover td{background:var(--chip)}
.detail td{background:var(--bg)}
.rows{margin:6px 0 10px;padding:0;list-style:none}
.rows li{margin:2px 0}
.s-passed,.s-accepted,.s-partly-checked{color:var(--ready)}.s-failed{color:var(--broken)}
.s-to-review{color:var(--judg)}.s-warning{color:var(--fix)}
.s-skipped,.s-not-run,.s-not-checked-yet{color:var(--muted)}
.f{border-left:3px solid var(--line);padding:6px 10px;margin:8px 0}
.f.proven{border-color:var(--broken)}.f.judgment{border-color:var(--judg)}
.f.warning{border-color:var(--fix)}.f.accepted{border-color:var(--ready)}
.f dl{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;margin:6px 0 0}
.f dt{font-weight:600}.f dd{margin:0;overflow-wrap:anywhere}
code{background:var(--chip);padding:1px 5px;border-radius:5px;font-size:12px;
 overflow-wrap:anywhere}
.muted{color:var(--muted)}
.wrap{overflow-x:auto}
</style>
</head>
<body>
<main>
<h1>rlgate report</h1>
<div class="sub" id="sub"></div>
<div class="cards" id="cards"></div>
<section id="common-sec"><h2>Most common problems</h2><ul class="common" id="common"></ul></section>
<div class="filters">
 <select id="fv"><option value="">All verdicts</option><option value="ready">Ready</option>
  <option value="needs_fixes">Needs fixes</option><option value="broken">Broken</option>
  <option value="not_checked">Not checked</option></select>
 <select id="fr"><option value="">All rules</option></select>
 <select id="ft"><option value="">All task types</option></select>
 <input id="fq" type="search" placeholder="Search tasks">
</div>
<section class="wrap"><table class="table"><thead><tr><th>Task</th><th>Verdict</th>
 <th class="num">Proven</th><th class="num">To review</th><th class="num">Quality score</th>
 </tr></thead><tbody id="rows"></tbody></table>
 <p class="muted" id="empty" hidden>No tasks match these filters.</p></section>
</main>
<script>
const D = /*DATA*/;
const LABEL = {ready:"Ready", needs_fixes:"Needs fixes", broken:"Broken",
               not_checked:"Not checked"};
const SYM = {"passed":"✓", "failed":"✗", "to review":"?", "accepted":"✓",
             "partly checked":"✓", "not run":"-", "skipped":"-", "not checked yet":"-"};
const KIND = {proven:"Proven", judgment:"Needs your judgment", warning:"Warning",
              accepted:"Accepted, with reason"};
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",
  '"':"&quot;"}[c]));
const $ = id => document.getElementById(id);
$("sub").textContent = `${D.target} · ${D.tasks} tasks · generated ${D.generated} · rlgate ${D.rlgate}`;
$("cards").innerHTML = [["Tasks", D.tasks], ["Ready", D.verdicts.ready],
  ["Needs fixes", D.verdicts.needs_fixes], ["Broken", D.verdicts.broken]]
  .map(([k, v]) => `<div class="card"><b>${v}</b><span>${k}</span></div>`).join("");
if (!D.common_problems.length) $("common-sec").hidden = true;
$("common").innerHTML = D.common_problems.map(p =>
  `<li><code>${esc(p.rule)}</code> ${esc(p.title)} <span class="muted">· ${p.tasks.length}
   task(s): ${esc(p.tasks.slice(0, 5).join(", "))}${p.tasks.length > 5 ? " …" : ""}</span></li>`).join("");
const rulesSeen = [...new Set(D.results.flatMap(r => (r.findings || []).map(f => f.rule)))].sort();
$("fr").innerHTML += rulesSeen.map(r => `<option>${esc(r)}</option>`).join("");
const types = [...new Set(D.results.map(r => r.type || "unknown"))].sort();
$("ft").innerHTML += types.map(t => `<option>${esc(t)}</option>`).join("");
const open = new Set();
function detail(r) {
  if (r.error) return `<p>Could not run: ${esc(r.error)}</p>`;
  const rows = r.steps.map(g => `<li class="s-${g.status.replace(/ /g, "-")}">${SYM[g.status] || "-"}
    Step ${g.step} ${esc(g.name)} · ${esc(g.status)}
    <span class="muted">${esc(g.summary)}</span></li>`).join("");
  const fs = r.findings.map(f => `<div class="f ${f.kind}"><b>${esc(f.rule)}</b> ${esc(f.title)}
    <span class="muted">· ${KIND[f.kind] || f.kind} · ${esc(f.severity)}</span><dl>
    <dt>What</dt><dd>${esc(f.what)}</dd><dt>Why</dt><dd>${esc(f.why)}</dd>
    <dt>Proof</dt><dd><code>${esc(f.proof || "-")}</code></dd><dt>Fix</dt><dd>${esc(f.fix)}</dd>
    ${f.waiver ? `<dt>Accepted</dt><dd>${esc(f.waiver.reason)} (${esc(f.waiver.author)},
      ${esc(f.waiver.date)})</dd>` : ""}</dl></div>`).join("");
  return `<p class="muted">${esc(r.mode)} run · ${esc(r.headline || "")} · ${esc(r.target)}</p><ul class="rows">${rows}</ul>${fs}`;
}
function render() {
  const v = $("fv").value, ru = $("fr").value, t = $("ft").value, q = $("fq").value.toLowerCase();
  const order = {broken:0, needs_fixes:1, ready:2};
  const list = D.results.filter(r => (!v || r.verdict === v)
      && (!ru || (r.findings || []).some(f => f.rule === ru))
      && (!t || (r.type || "unknown") === t)
      && (!q || r.task.toLowerCase().includes(q)))
    .sort((a, b) => order[a.verdict] - order[b.verdict] || a.task.localeCompare(b.task));
  $("empty").hidden = list.length > 0;
  $("rows").innerHTML = list.map(r => {
    const c = r.counts || {};
    const row = `<tr class="task" data-id="${esc(r.task)}"><td>${esc(r.task)}</td>
      <td><span class="v ${r.verdict}">${LABEL[r.verdict]}</span></td>
      <td class="num">${c.proven ?? "-"}</td><td class="num">${c.to_review ?? "-"}</td>
      <td class="num">${r.quality_score != null ? r.quality_score.toFixed(2) : "-"}</td></tr>`;
    return row + (open.has(r.task) ? `<tr class="detail"><td colspan="5">${detail(r)}</td></tr>` : "");
  }).join("");
}
$("rows").addEventListener("click", e => {
  const tr = e.target.closest("tr.task"); if (!tr) return;
  const id = tr.dataset.id; open.has(id) ? open.delete(id) : open.add(id); render();
});
["fv", "fr", "ft"].forEach(id => $(id).addEventListener("change", render));
$("fq").addEventListener("input", render);
render();
</script>
</body>
</html>
"""
