// Core: helpers, icons, API, shell (top bar, side nav, search), router. Views live in patient.js, hospital.js, adjudicator.js.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const inr = n => (n < 0 ? "-" : "") + "₹" + Math.abs(Number(n)).toLocaleString("en-IN", { maximumFractionDigits: 0 });
const pct = n => Math.round(n * 100) + "%";
const store = { get: (k, d) => { try { return sessionStorage.getItem(k) ?? d } catch { return d } }, set: (k, v) => { try { sessionStorage.setItem(k, v) } catch {} } };
const state = { role: store.get("role", "adjudicator"), sub: store.get("sub", "worklist"), patient: null, hospital: null, claim: store.get("claim", "CLM-001"), adjPatient: null, netHospital: null, page: 0, filter: "all" };
const PATHS = {
  shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>', search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.5-4.5"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>', bench: '<path d="M9 4h6v3H9z"/><path d="M7 5H5v16h14V5h-2"/><path d="M9 13l2 2 4-4"/>',
  rules: '<path d="M12 3v18M5 7h14"/><path d="M5 7l-3 7a3 3 0 006 0zM19 7l-3 7a3 3 0 006 0z"/>', audit: '<path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0116 0"/>', hospital: '<path d="M3 21V7l9-4 9 4v14M9 21v-6h6v6M12 8v4M10 10h4"/>', pulse: '<path d="M3 12h4l2-6 4 12 2-6h6"/>',
  check: '<path d="M5 12l5 5 9-10"/>', alert: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17h.01"/>', clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  bolt: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>', chart: '<path d="M4 20V10M10 20V4M16 20v-8M22 20H2"/>', network: '<circle cx="12" cy="5" r="2.5"/><circle cx="5" cy="19" r="2.5"/><circle cx="19" cy="19" r="2.5"/><path d="M12 7.5v4M12 11.5L6.5 17M12 11.5l5.5 5.5"/>',
  rupee: '<path d="M6 4h12M6 9h12"/><path d="M6 4c6 0 8 2 8 5s-3 5-8 5l8 6"/>', route: '<circle cx="6" cy="19" r="2.5"/><circle cx="18" cy="5" r="2.5"/><path d="M6 16.5V9a3 3 0 013-3h6.5"/>',
  doc: '<path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z"/><path d="M14 3v5h5"/>', db: '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
  home: '<path d="M3 11l9-8 9 8M5 10v10h14V10"/>', card: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18"/>', flag: '<path d="M5 21V4M5 4h12l-2 4 2 4H5"/>',
};
const ic = n => `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">${PATHS[n] || ""}</svg>`;
const chip = (t, k = "") => `<span class="chip ${k}">${esc(t)}</span>`;
const sevKind = s => ({ high: "bad", medium: "warn", low: "low" }[s] || "low");
const pubKind = s => /^(Approved|Pre-approved|Settled)/.test(s) ? "ok" : /^(Being|Standard|Final)/.test(s) ? "warn" : "bad";
const label = s => String(s || "").replaceAll("_", " ");
const rcards = fl => (fl || []).map(f => `<div class="rc ${esc(f.severity)}"><b>${esc(f.severity)} · ${esc(label(f.code))}</b>${esc(f.text)}</div>`).join("");
const panel = (icon, title, body, right = "", id = "") => `<section class="panel" ${id ? `id="${id}"` : ""}><div class="ph">${ic(icon)}<h2>${esc(title)}</h2><span class="sp">${right}</span></div>${body}</section>`;
const kpi = (icon, t, v, sub = "", bar = "") => `<div class="kpi"><span class="ki">${ic(icon)}</span><small>${esc(t)}</small><b>${v}</b><span class="sub">${sub}</span>${bar}</div>`;
const bar = (p, k = "") => `<div class="bar ${k}"><i style="width:${Math.max(2, Math.min(100, p))}%"></i></div>`;
const head = (icon, title, text, right = "") => `<div class="pagehead"><div class="ico-tile">${ic(icon)}</div><div class="grow"><h1>${esc(title)}</h1><p>${text}</p></div>${right}</div>`;
const fmtTime = s => String(s || "").replace("T", " ").slice(0, 19);

async function api(url, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.adj ? { "X-Adjudicator-Key": store.get("key", "demo-key") } : {}) };
  const r = await fetch(url, { method: opts.body ? "POST" : "GET", headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : r.statusText);
  return j;
}
const adj = (url, body) => api(url, { adj: true, body });
const me = () => ({ approver: store.get("name", ""), level: store.get("level", "junior") });
const select = (id, opts, cur, extra = "") => `<select id="${id}" ${extra}>${opts.map(([v, t]) => `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>`;

// ---------- shell ----------
const NAV = {
  patient: [["overview", "home", "Overview"], ["treat", "pulse", "My treatment"], ["pre", "shield", "Pre-approvals"], ["hist", "card", "History & payments"]],
  hospital: [["req", "bolt", "Session approval"], ["fast", "shield", "Pre-approve & bill"], ["list", "list", "Requests"], ["score", "chart", "Scorecard"]],
  adjudicator: [["worklist", "list", "Claims worklist"], ["bench", "bench", "Adjudication bench"], ["queue", "alert", "Session approvals"], ["fast", "bolt", "Fast-track claims"], ["p360", "user", "Patient 360"], ["n360", "network", "Network 360"], ["rules", "rules", "Rule matrix & search"], ["audit", "audit", "Audit & compliance"]],
};
async function drawShell() {
  $("#logo").innerHTML = ic("shield"); $("#searchic").innerHTML = ic("search");
  $$("#roles button").forEach(b => b.classList.toggle("on", b.dataset.role === state.role));
  const who = $("#who");
  if (state.role === "adjudicator") {
    who.innerHTML = `<input id="name" placeholder="Your name" size="13" value="${esc(store.get("name", ""))}" aria-label="Your name"><select id="level" aria-label="Level"><option value="junior">L1 junior</option><option value="senior">L3 senior</option></select><input id="key" type="password" placeholder="API key" size="9" value="${esc(store.get("key", "demo-key"))}" aria-label="Adjudicator key">`;
    $("#level").value = store.get("level", "junior");
    $("#name").oninput = e => store.set("name", e.target.value); $("#level").onchange = e => store.set("level", e.target.value); $("#key").oninput = e => store.set("key", e.target.value);
  } else who.innerHTML = "";
  let counts = {}, engine = { embedder: "…" };
  try { engine = await api("/api/policy/vector-db"); } catch {}
  if (state.role === "adjudicator") { try { const o = await adj("/api/adjudicator/overview"); counts = { queue: o.sessionRequests.held + o.sessionRequests.awaiting, fast: o.fastTrack.needDecision + o.fastTrack.standardReview, backlog: o.sessionRequests.held + o.sessionRequests.awaiting + o.fastTrack.needDecision }; } catch {} }
  $("#enginechip").textContent = "IRDAI-compliant engine";
  const items = NAV[state.role].map(([k, i, t]) => state.role === "adjudicator"
    ? `<button data-k="${k}" class="${state.sub === k ? "on" : ""}">${ic(i)}${t}${counts[k] ? `<span class="count">${counts[k]}</span>` : ""}</button>`
    : `<a href="#${state.role[0]}-${k}" data-k="${k}">${ic(i)}${t}</a>`).join("");
  $("#side").innerHTML = `<h4>${state.role === "adjudicator" ? "Adjudication console" : state.role === "hospital" ? "Network hospital" : "My cover"}</h4>${items}
    <div class="engine"><div>ENGINE STATE<b class="st">STABLE</b></div>${state.role === "adjudicator" ? `<div>Queue backlog<b>${counts.backlog ?? 0} pending</b></div>` : ""}<div>Retrieval<b>${esc(String(engine.embedder).split(" ")[0])}</b></div><div>Vector DB<b>${engine.clauses ?? "–"} clauses</b></div></div>`;
  if (state.role === "adjudicator") $$("#side button").forEach(b => b.onclick = () => { state.sub = b.dataset.k; store.set("sub", state.sub); render(); });
}
$$("#roles button").forEach(b => b.onclick = () => { state.role = b.dataset.role; store.set("role", state.role); render(); });
$("#gsearch").onsubmit = async e => {
  e.preventDefault(); const q = $("#q").value.trim(); if (!q) return;
  if (/^CLM-?\d+/i.test(q)) { state.role = "adjudicator"; state.sub = "bench"; state.claim = q.toUpperCase().replace(/^CLM(\d)/, "CLM-$1"); store.set("claim", state.claim); }
  else { const ps = await api("/api/patient"); const m = ps.find(p => p.policy.toLowerCase() === q.toLowerCase() || p.name.toLowerCase().includes(q.toLowerCase()));
    if (m) { state.role = "adjudicator"; state.sub = "p360"; state.adjPatient = m.policy; } else { state.role = "adjudicator"; state.sub = "rules"; state.rq = q; } }
  store.set("role", state.role); store.set("sub", state.sub); render();
};
let busy = false, again = false;
function render() {            // one render at a time; a click during a render queues exactly one more, so views never overwrite each other
  if (busy) { again = true; return Promise.resolve(); }
  busy = true;
  const views = { patient: viewPatient, hospital: viewHospital, adjudicator: viewAdjudicator };
  const v = views[state.role]().catch(e => { $("#app").innerHTML = `<div class="panel"><div class="err">${esc(e.message)}</div></div>`; });
  return Promise.all([drawShell(), v]).finally(() => { busy = false; if (again) { again = false; render(); } });
}
