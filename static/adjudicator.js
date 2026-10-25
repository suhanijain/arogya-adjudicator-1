async function viewAdjudicator() {
  const panes = { worklist: paneWorklist, bench: paneBench, queue: paneQueue, fast: paneFast, p360: pane360p, n360: pane360n, rules: paneRules, audit: paneAudit };
  await (panes[state.sub] || paneWorklist)();
}
const toBench = id => { state.claim = id; store.set("claim", id); state.sub = "bench"; store.set("sub", "bench"); render(); };
const toPatient = p => { state.adjPatient = p; state.sub = "p360"; store.set("sub", "p360"); render(); };
const toNetwork = h => { state.netHospital = h; state.sub = "n360"; store.set("sub", "n360"); render(); };
const wire = () => { $$("[data-c]").forEach(b => b.onclick = e => { e.stopPropagation(); toBench(b.dataset.c); }); $$("[data-p]").forEach(b => b.onclick = e => { e.stopPropagation(); toPatient(b.dataset.p); }); $$("[data-h]").forEach(b => b.onclick = e => { e.stopPropagation(); toNetwork(b.dataset.h); }); };
const runChip = r => r.run === "not_started" ? chip("Not run") : r.run === "waiting_for_adjudicator" ? chip("Awaiting decision", "warn") : chip(r.outcome || "finished", r.outcome === "settled" ? "ok" : "bad");

// ---------- worklist ----------
async function paneWorklist() {
  const [o, rows] = await Promise.all([adj("/api/adjudicator/overview"), adj("/api/adjudicator/worklist")]);
  const filters = { all: ["All", () => true], flagged: ["Flagged", r => r.flagged], room: ["Room-rent breach", r => r.roomBreach], rep: ["Repudiated", r => !r.admissible], senior: ["Needs senior", r => r.senior] };
  const q = (state.wq || "").toLowerCase(), f = filters[state.filter] ? state.filter : "all";
  const shown = rows.filter(filters[f][1]).filter(r => !q || `${r.claimId} ${r.patient} ${r.hospital} ${r.diagnosis} ${r.icd}`.toLowerCase().includes(q));
  const per = 10, pages = Math.max(1, Math.ceil(shown.length / per)); state.page = Math.min(state.page, pages - 1);
  const pend = o.sessionRequests.held + o.sessionRequests.awaiting + o.fastTrack.needDecision;
  $("#app").innerHTML = head("list", "Claims worklist & queue", `Every claim in the book with the engine's computed result. Open one on the adjudication bench to see the ledger, clauses and pipeline trace.`, chip("Rules engine active", "ok")) +
  `<div class="kpis">${kpi("bench", "Claims in book", o.claims, `${o.flagged} flagged · ${o.repudiated} repudiated`, bar(o.flagged / o.claims * 100, "w"))}
   ${kpi("bolt", "Fast-track settled by rules", `${o.fastTrack.autoSettled}<small style="display:inline;font-size:13px;color:var(--muted)"> / ${o.fastTrack.settled}</small>`, `net adjustment ${inr(o.fastTrack.netAdjustment)}`)}
   ${kpi("alert", "Pending your decision", pend, `${o.sessionRequests.held} held · ${o.fastTrack.needDecision} final amounts`)}
   ${kpi("rupee", "Deducted by clauses", inr(o.deducted), `of ${inr(o.claimed)} claimed (${pct(o.deducted / o.claimed)})`, bar(o.deducted / o.claimed * 100, "p"))}</div>
  <section class="panel"><div class="segs">${Object.entries(filters).map(([k, [t, fn]]) => `<button data-f="${k}" class="${k === f ? "on" : ""}">${t} (${rows.filter(fn).length})</button>`).join("")}<input id="wq" placeholder="Filter by name, hospital, ICD…" value="${esc(state.wq || "")}" style="margin-left:auto;width:240px"></div>
  <div class="tw"><table><tr><th>Claim & patient</th><th>Hospital</th><th>Diagnosis (ICD-10)</th><th class="r">Claimed → payable</th><th>Triggers</th><th>Risk</th><th>Status</th></tr>
  ${shown.slice(state.page * per, state.page * per + per).map(r => `<tr class="click" data-c="${esc(r.claimId)}"><td><span class="mono info">${esc(r.claimId)}</span><br>${esc(r.patient)} <span class="muted">${r.age}${r.gender[0]}</span></td>
   <td>${esc(r.hospital)}<br>${chip(r.tier)}</td><td>${esc(r.diagnosis)}<br>${chip(r.icd, "info")}</td>
   <td class="r"><s class="was">${inr(r.claimed)}</s>${r.admissible ? `<b>${inr(r.payable)}</b><br><span class="bad">-${inr(r.deducted)}</span>` : `<b class="bad">${inr(0)}</b>`}</td>
   <td>${!r.admissible ? chip("repudiated", "bad") + " " : ""}${r.roomBreach ? chip("room breach", "warn") + " " : ""}${r.flags.slice(0, 2).map(t => chip(label(t), r.flagged ? "bad" : "")).join(" ")}</td>
   <td style="min-width:90px"><span class="mono">${r.risk}</span>${bar(r.risk, r.risk >= 60 ? "b" : r.risk >= 30 ? "w" : "")}</td><td>${runChip(r)}</td></tr>`).join("") || `<tr><td colspan="7" class="muted">No claims match.</td></tr>`}</table></div>
  <div class="pager"><span>Showing ${shown.length ? state.page * per + 1 : 0}-${Math.min(shown.length, state.page * per + per)} of ${shown.length}</span><button class="btn sec sm" id="prev" ${state.page ? "" : "disabled"}>Previous</button><button class="btn sec sm" id="next" ${state.page < pages - 1 ? "" : "disabled"}>Next</button></div></section>`;
  $$("[data-f]").forEach(b => b.onclick = () => { state.filter = b.dataset.f; state.page = 0; render(); });
  $("#wq").onchange = e => { state.wq = e.target.value; state.page = 0; render(); };
  $("#prev").onclick = () => { state.page--; render(); }; $("#next").onclick = () => { state.page++; render(); };
  $$("tr.click").forEach(r => r.onclick = () => toBench(r.dataset.c)); wire();
}

// ---------- adjudication bench ----------
async function paneBench() {
  const rows = await adj("/api/adjudicator/worklist");
  const cid = rows.some(r => r.claimId === state.claim) ? state.claim : rows[0].claimId;
  const b = await adj(`/api/adjudicator/claims/${cid}`), c = b.claim, w = b.worksheet, fr = b.fraud, tr = b.trace;
  const [similar, hits] = await Promise.all([adj(`/api/adjudicator/claims/${cid}/similar`), api(`/api/policy/search?mode=hybrid&k=4&q=${encodeURIComponent(c.diagnosis + " " + c.procedureName)}`)]);
  const n = x => Number(x), pay = n(w.finalPayableAmount), claimed = n(w.claimedTotal), ded = n(w.totalDeductions);
  const idx = rows.findIndex(r => r.claimId === cid), reasonsNZ = Object.entries(w.deductionsByReason).filter(([, v]) => n(v) > 0);
  const senior = w.requiresSeniorApproval || fr.hasFraudFlag;
  const stepKind = s => /MISMATCH|cannot|refused|DENIED|INVALID|Four-eyes/.test(s) ? "b" : /Rejected/.test(s) ? "b" : /flag|Complex|breach/i.test(s) ? "w" : "";
  const decision = (() => {
    if (tr.status === "not_started") return `<p class="muted">Not run yet. Running executes the nine-step workflow: supervisor, extraction check, fraud screen, coverage, model note, critic, human approval, settlement, memory.</p><button class="btn" id="run">${ic("bolt")}Run adjudication</button>`;
    if (tr.status === "finished") return `<div class="note ${tr.outcome === "settled" ? "" : "bad"}"><b>${esc(tr.outcome)}</b>${tr.settlement?.settlementId ? ` · <span class="mono">${esc(tr.settlement.settlementId)}</span>` : ""}</div>`;
    return `${senior ? `<div class="note warn"><b>Senior approval required.</b> ${esc([...w.seniorApprovalReasons].join(" "))}</div>` : ""}<label>Decision note / rejection reason</label><textarea id="dnote" rows="2"></textarea>
      <div class="row" style="margin-top:10px"><button class="btn good" id="approve">${ic("check")}Approve ${inr(pay)}</button><button class="btn danger" id="reject">Reject</button></div>`;
  })();
  $("#app").innerHTML = head("bench", `${cid} · ${c.insuredName}`, `${esc(c.diagnosis)} · ${esc(c.hospitalName)}, ${esc(c.hospitalCity)} <span class="chip">${esc(c.cityTier)}</span> ${chip("IRDAI engine", "ok")}`,
    `<div class="row">${select("cpick", rows.map(r => [r.claimId, `${r.claimId} · ${r.patient}`]), cid, 'aria-label="Choose claim"')}<button class="btn sec sm" id="cprev" ${idx ? "" : "disabled"}>Prev</button><button class="btn sec sm" id="cnext" ${idx < rows.length - 1 ? "" : "disabled"}>Next</button></div>`) +
  `<div class="bench"><div>
    ${panel("user", "Patient profile", `<dl class="kv"><dt>Name</dt><dd>${esc(c.insuredName)} · ${c.patientAge}${esc(c.patientGender[0])}</dd><dt>Policy</dt><dd>${esc(c.policyNumber)}</dd><dt>Sum insured</dt><dd>${inr(n(c.sumInsured))}</dd><dt>Bonus</dt><dd>${inr(n(c.cumulativeBonus))}</dd><dt>Cover ceiling</dt><dd>${inr(n(c.totalCoverageLimit))}</dd><dt>Tenure</dt><dd>${n(c.continuousMonthsActive)} months</dd></dl>
      ${bar(Math.min(100, claimed / n(c.totalCoverageLimit) * 100), claimed > n(c.totalCoverageLimit) ? "b" : "p")}<span class="small muted">Claim is ${pct(claimed / n(c.totalCoverageLimit))} of the ceiling</span><div class="row" style="margin-top:10px"><button class="btn sec sm" data-h="${esc(c.hospitalName)}">Network 360</button></div>`)}
    ${panel("pulse", "Clinical admission context", `<dl class="kv"><dt>Diagnosis</dt><dd>${esc(c.diagnosis)} ${chip(c.icd10Code, "info")}</dd><dt>Procedure</dt><dd>${esc(c.procedureName)}</dd><dt>Admitted</dt><dd>${esc(c.admissionDate)} → ${esc(c.dischargeDate)}</dd><dt>Stay</dt><dd>${c.hospitalizationDays} d${c.icuDays ? " + ICU " + c.icuDays + " d" : ""}</dd>
      <dt>Room</dt><dd>${esc(c.roomCategoryAdmitted)} @ ${inr(n(c.actualRoomRentPerDay))}/d <span class="muted">cap ${inr(n(w.roomRentCapPerDay))}</span></dd></dl>
      <div class="row" style="margin-top:8px">${c.isAccident ? chip("accident", "warn") : ""}${c.isDayCare ? chip("day care") : ""}${c.isPreExisting ? chip("pre-existing: " + (c.preExistingConditionName || ""), "warn") : ""}${!c.isAccident && !c.isDayCare && !c.isPreExisting ? chip("planned admission") : ""}</div>`)}
    ${panel("doc", "Submitted dossier", b.documents.map(d => `<div class="doc"><span class="ok">${ic("check")}</span><div><b>${esc(d.name)}</b><br><span class="small muted mono">${d.chars} chars · text extracted</span></div></div>`).join(""))}
  </div><div>
    <section class="panel"><div class="ph">${ic("shield")}<h2>Rules-engine result</h2><span class="sp">${chip(w.isAdmissible ? "admissible" : "repudiated", w.isAdmissible ? "ok" : "bad")} ${chip("hash " + String(w.worksheetHash).slice(0, 10))}</span></div>
      <div class="verdict"><div><div class="muted small">NET PAYABLE</div><div class="big ${w.isAdmissible ? "ok" : "bad"}">${inr(pay)}</div></div><div class="r" style="text-align:right"><div class="muted small">TOTAL INVOICED</div><div class="mono"><s class="was" style="display:inline;font-size:14px">${inr(claimed)}</s></div><div class="bad mono">Disallowed ${inr(ded)}</div></div></div>
      ${w.isAdmissible ? `<div class="strip">${reasonsNZ.map(([k, v]) => `<div><small>${esc(label(k))}</small><b>-${inr(n(v))}</b></div>`).join("") || "<div><small>deductions</small><b>none</b></div>"}</div>` : `<div class="note bad" style="margin-top:12px"><b>${esc(w.inadmissibilityClause)}</b><br>${esc(w.inadmissibilityReason)}</div>`}
      ${w.lineSumMatchesBill ? "" : `<div class="note warn"><b>Bill total does not equal the line items.</b> Line sum ${inr(n(w.lineSumTotal))} against ${inr(claimed)}.</div>`}</section>
    ${panel("list", "Adjudication ledger", `<div class="tw"><table><tr><th>Bill line</th><th>Clause</th><th class="r">Claimed</th><th class="r">Admissible</th><th class="r">Deduction</th></tr>${w.lines.map(l => `<tr ${n(l.deductionAmount) > 0 ? `style="box-shadow:inset 3px 0 0 var(--bad)"` : ""}><td>${esc(l.rawDescription)}<br>${chip(label(l.category))}${l.proportionalCutApplied ? " " + chip("pro-rata " + Number(l.proportionalRatio).toFixed(3), "warn") : ""}</td><td>${l.clauseCited ? chip(l.clauseCited, "info") : ""}</td><td class="r">${inr(n(l.claimedAmount))}</td><td class="r">${inr(n(l.admissibleAmount))}</td><td class="r ${n(l.deductionAmount) ? "bad" : ""}">${n(l.deductionAmount) ? "-" + inr(n(l.deductionAmount)) : "–"}</td></tr>`).join("")}</table></div>
      ${w.lines.filter(l => n(l.deductionAmount) > 0).map(l => `<div class="rc bad"><b>${esc(l.clauseCited)} · -${inr(n(l.deductionAmount))}</b>${esc(l.rawDescription)}: ${esc(l.deductionReason)}${l.auditExplanation ? `<div class="code">${esc(l.auditExplanation)}</div>` : ""}</div>`).join("")}
      ${n(w.copayAmount) ? `<div class="rc warn"><b>Clause 9.5 · co-payment -${inr(n(w.copayAmount))}</b>Mandatory ${w.copayPercent}% of the admissible ${inr(n(w.admissibleBeforeCopay))}.</div>` : ""}`)}
    ${panel("flag", "Fraud & prompt-attack screen", `<div class="row"><span class="mono">risk ${fr.riskScore}/100</span><div style="flex:1">${bar(fr.riskScore, fr.riskScore >= 60 ? "b" : fr.riskScore >= 30 ? "w" : "")}</div></div>${fr.flags.map(f => `<div class="rc ${esc(f.severity)}"><b>${esc(f.severity)} · ${esc(label(f.type))}${f.clauseCited ? " · " + esc(f.clauseCited) : ""}</b>${esc(f.title)}. ${esc(f.description)}${f.evidence ? `<div class="code">${esc(f.evidence)}</div>` : ""}</div>`).join("") || `<p class="muted">No flags.</p>`}`)}
  </div><div>
    ${panel("route", "Decision", `<div class="row" style="margin-bottom:8px">${runChip({ run: tr.status, outcome: tr.outcome })}${senior ? chip("senior approval", "warn") : ""}</div>${decision}<div class="err" id="derr"></div>`)}
    ${panel("route", "Pipeline trace", tr.steps?.length ? `<div class="tl">${tr.steps.map(s => `<div class="n ${stepKind(s.summary)}"><span class="t">${esc(fmtTime(s.at).slice(11))}</span><b>${esc(s.node)}${s.llm === false ? "" : ""}</b><p>${esc(s.summary)}</p></div>`).join("")}</div>` : `<p class="muted">No trace yet. Run the claim to record the pipeline.</p>`)}
    ${panel("db", "Retrieved policy clauses", hits.map(h => `<div class="rc"><b>${esc(h.clause)} · ${esc(h.title)}</b><span class="chip">${esc(h.mode)}</span> <span class="chip">${h.score}</span></div>`).join("") || `<p class="muted">None.</p>`, `<span class="small muted">BM25 + vector DB</span>`)}
    ${panel("db", "Similar past claims", similar.map(s => `<div class="rc ${s.admissible ? "ok" : "bad"}" style="cursor:pointer" data-c="${esc(s.claim)}"><b>${esc(s.claim)} · similarity ${s.score}</b>${esc(s.diagnosis)} · ${esc(s.procedure)}<br><span class="mono small">${inr(s.claimed)} → ${s.admissible ? inr(s.payable) : "repudiated"}</span></div>`).join(""), `<span class="small muted">vector DB</span>`)}
  </div></div>`;
  $("#cpick").onchange = e => toBench(e.target.value); $("#cprev").onclick = () => toBench(rows[idx - 1].claimId); $("#cnext").onclick = () => toBench(rows[idx + 1].claimId);
  const act = async (fn) => { try { await fn(); await render(); } catch (x) { $("#derr").textContent = x.message; } };
  if ($("#run")) $("#run").onclick = () => act(() => api(`/api/claims/${cid}/run`, { body: {} }).catch(() => fetch(`/api/claims/${cid}/run`, { method: "POST" })));
  const dec = action => act(() => adj(`/api/claims/${cid}/decision`, { action, ...me(), reason: $("#dnote").value }));
  if ($("#approve")) { $("#approve").onclick = () => dec("approve"); $("#reject").onclick = () => dec("reject"); }
  wire();
}

// ---------- session approval queue ----------
async function paneQueue() {
  const q = await adj("/api/adjudicator/queue"), held = q.items.filter(r => r.status === "held").length;
  $("#app").innerHTML = head("alert", "Session approvals", `Sessions that differ from the patient's own pattern are held for a person. Regular treatment can be approved once for the coming sessions. Demo date <span class="mono">${esc(q.asOf)}</span>.`, `${chip(held + " held", held ? "bad" : "")} ${chip(q.items.length - held + " to review", "warn")}`) +
  (q.items.map(r => `<section class="panel" data-id="${esc(r.id)}"><div class="ph"><h2>${esc(r.patient)} · ${esc(r.procedure)}</h2>${chip(r.status === "held" ? "Held" : "Needs review", r.status === "held" ? "bad" : "warn")}<span class="sp"><button class="btn sec sm" data-p="${esc(r.policy)}">Patient 360</button> <button class="btn sec sm" data-h="${esc(r.hospital)}">Network 360</button></span></div>
    <p class="muted"><span class="mono">${esc(r.id)}</span> · ${esc(r.hospital)} (${esc(r.hospitalCity)}) · ${esc(r.serviceDate)} · <span class="mono">${inr(r.amount)}</span> · ${esc(r.why)}</p>${rcards(r.flags)}
    ${r.recommendation ? `<div class="note"><b>Suggested standing approval:</b> up to ${r.recommendation.sessions} sessions at ${esc(r.recommendation.hospitals.join(", "))}, max <span class="mono">${inr(r.recommendation.perSessionCap)}</span> each, until ${esc(r.recommendation.validTo)}. Each later session is still checked against the pattern.</div>` : ""}
    <label>${r.status === "held" ? "How was the patient verified? (senior, required to approve)" : "Note (required to reject)"}</label><textarea rows="2" class="vnote" placeholder="${r.status === "held" ? "e.g. Called registered number XXXXXX1234; patient confirmed" : ""}"></textarea>
    <div class="row" style="margin-top:10px">${r.recommendation ? `<button class="btn good" data-a="standing">${ic("check")}Approve standing authorisation</button>` : ""}<button class="btn ${r.recommendation ? "sec" : "good"}" data-a="approve">Approve this session only</button><button class="btn danger" data-a="reject">Reject</button></div><div class="err"></div></section>`).join("") || panel("check", "Queue is empty", `<p class="muted">Nothing waiting.</p>`));
  $$("[data-a]").forEach(b => b.onclick = async () => { const card = b.closest("section"); try { await adj(`/api/adjudicator/preauth/${card.dataset.id}/${b.dataset.a}`, { ...me(), note: $(".vnote", card).value }); render(); } catch (x) { $(".err", card).textContent = x.message; } });
  wire();
}

// ---------- fast-track claims ----------
async function paneFast() {
  const items = await adj("/api/adjudicator/fast-track");
  const lab = { adjudication: ["Needs decision", "bad"], standard_review: ["Not eligible: standard review", "warn"], preapproved: ["Pre-approved, waiting for bill", "ok"], settled: ["Settled", "ok"], rejected: ["Rejected", "bad"] };
  $("#app").innerHTML = head("bolt", "Fast-track claims", "Regular-treatment claims pre-approved from the hospital's contracted rates. After treatment only the amount moves, up or down, from the itemised bill and the discharge summary. Anything that check cannot settle comes here with a proposed amount.") +
  (items.map(x => { const rc = x.reconciliation, a = x.assessed, [lt, lk] = lab[x.status];
    return `<section class="panel" data-id="${esc(x.id)}"><div class="ph"><h2>${esc(x.patient)} · ${esc(x.procedure)}</h2>${chip(lt, lk)}<span class="sp"><button class="btn sec sm" data-p="${esc(x.policy)}">Patient 360</button> <button class="btn sec sm" data-h="${esc(x.hospital)}">Network 360</button></span></div>
     <p class="muted"><span class="mono">${esc(x.id)}</span> · ${esc(x.hospital)} · ${esc(x.serviceDate)}${a ? ` · assessed <span class="mono">${inr(a.total)}</span>, payable <span class="mono">${inr(a.payable)}</span> (package ${inr(a.package)} + ${a.roomDays} day(s) × ${inr(a.roomRateAssessed)})` : ""}</p>
     ${x.eligibility.filter(c => !c.ok).map(c => `<div class="rc high"><b>${esc(label(c.code))}</b>${esc(c.text)}</div>`).join("")}
     ${x.status === "preapproved" ? `<details><summary>Eligibility checks (all passed)</summary>${x.eligibility.map(c => `<div class="small ok">✓ ${esc(c.text)}</div>`).join("")}</details>` : ""}
     ${rc ? `<h3>Bill ${esc(rc.billNumber)}: billed ${inr(rc.billedTotal)} → allowed ${inr(rc.total)}, payable ${inr(rc.payable)} (${rc.direction === "none" ? "no change" : (rc.delta > 0 ? "+" : "") + inr(rc.delta)} vs pre-approved ${inr(rc.preapprovedPayable)})</h3>
       ${rc.adjustments.map(c => `<div class="rc ${c.kind === "up" ? "pass" : "warn"}"><b>${c.kind === "up" ? "Up" : "Down"} ${inr(c.amount)}${c.clause ? " · " + esc(c.clause) : ""}</b>${esc(c.text)}</div>`).join("")}
       ${rc.exits.map(t => `<div class="rc high"><b>Needs a person</b>${esc(t)}</div>`).join("")}${rc.settlementId ? `<p class="small muted mono">Settled ${esc(rc.settlementId)} by ${esc(rc.settledBy)} (${esc(rc.level)})</p>` : ""}` : ""}
     ${x.status === "adjudication" ? `<label>Decision note (senior, at least 10 characters, to approve; any reason to reject)</label><input class="vnote" style="width:100%"><div class="row" style="margin-top:10px"><button class="btn good" data-a="approve">${ic("check")}Approve ${inr(rc.payable)}</button><button class="btn danger" data-a="reject">Reject</button></div><div class="err"></div>` : ""}</section>`; }).join("") || panel("bolt", "No fast-track claims yet", `<p class="muted">Ask a hospital to pre-approve a regular treatment.</p>`));
  $$("[data-a]").forEach(b => b.onclick = async () => { const card = b.closest("section"); try { await adj(`/api/adjudicator/fast-track/${card.dataset.id}/${b.dataset.a}`, { ...me(), note: $(".vnote", card).value }); render(); } catch (x) { $(".err", card).textContent = x.message; } });
  wire();
}

// ---------- Patient 360 ----------
function timelineSvg(tl) {
  if (!tl.length) return "";
  const hosp = [...new Set(tl.map(t => t.hospital))], colors = ["#38bdf8", "#f59e0b", "#a78bfa", "#34d399", "#f472b6", "#fb923c"];
  const day = s => Date.parse(s) / 864e5, lo = day(tl[0].date), hi = day(tl.at(-1).date), W = 900, rowH = 28, H = hosp.length * rowH + 30, x = d => 120 + (W - 140) * (hi === lo ? .5 : (day(d) - lo) / (hi - lo));
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Treatment timeline by hospital">${hosp.map((h, i) => `<text x="0" y="${22 + i * rowH}">${esc(h.split(" ").slice(0, 2).join(" "))}</text><line x1="120" x2="${W - 20}" y1="${18 + i * rowH}" y2="${18 + i * rowH}" stroke="#334155" stroke-dasharray="2 4"/>`).join("")}
   ${tl.map(t => { const i = hosp.indexOf(t.hospital); return `<circle cx="${x(t.date)}" cy="${18 + i * rowH}" r="${t.flags.length ? 6 : 3.5}" fill="${colors[i % 6]}" ${t.flags.length ? 'stroke="#ef4444" stroke-width="3"' : ""}><title>${esc(t.date)} ${esc(t.hospital)} ${inr(t.billed)}${t.flags.length ? " · " + esc(t.flags.map(f => f.code).join(", ")) : ""}</title></circle>`; }).join("")}
   <text x="120" y="${H - 4}">${esc(tl[0].date)}</text><text x="${W - 20}" y="${H - 4}" text-anchor="end">${esc(tl.at(-1).date)}</text></svg>
   <div class="legend">${hosp.map((h, i) => `<span><i style="background:${colors[i % 6]}"></i>${esc(h)}</span>`).join("")}<span><i style="border:3px solid #ef4444;width:9px;height:9px"></i>out of pattern</span></div>`;
}
async function pane360p() {
  const pats = await api("/api/patient"); state.adjPatient ||= pats[0].policy;
  const p = await adj(`/api/adjudicator/patients/${state.adjPatient}`), flagged = p.timeline.filter(t => t.flags.length), open = p.requests.filter(r => ["held", "awaiting"].includes(r.status));
  const used = p.sumInsured - p.remainingCover;
  $("#app").innerHTML = head("user", `Patient 360: ${p.name}`, `${p.age} · ${esc(p.gender)} · home ${esc(p.homeCity)} · registered phone <span class="mono">${esc(p.registeredContact)}</span> · policy valid to ${esc(p.policyValidTo)}`, `<div>${select("pp", pats.map(x => [x.policy, x.name]), state.adjPatient, 'aria-label="Patient"')}</div>`) +
  `<div class="kpis">${kpi("rupee", "Paid to date", inr(p.totalPaid), `of ${inr(p.totalBilled)} billed`)}${kpi("shield", "Cover left", inr(p.remainingCover), `${pct(used / p.sumInsured)} of ${inr(p.sumInsured)} used`, bar(used / p.sumInsured * 100, "p"))}${kpi("pulse", "Treatments", p.timeline.length, `${p.hospitals.length} hospital(s)`)}${kpi("alert", "Out-of-pattern (past)", p.pastFlags, p.pastFlags ? "<span class='warn'>ask the patient</span>" : "<span class='ok'>clean history</span>", bar(Math.min(100, p.pastFlags * 25), p.pastFlags ? "b" : ""))}</div>
  ${panel("pulse", "Regular treatment", (p.regularTreatments.map(r => `<div class="rc ok"><b>${esc(r.procedure)}</b>${r.episodes} sessions since ${esc(r.firstDate)} · every ~${Math.round(r.typicalGapDays)} days · typically <span class="mono">${inr(r.typicalAmount)}</span><br><span class="muted">Usual hospital: ${esc(r.usualHospitals.join(", "))} (${esc(r.usualCities.join(", "))}). Last session ${esc(r.lastDate)}.</span></div>`).join("") || `<p class="muted">No regular pattern. New requests get a standard review.</p>`) +
    p.standing.map(a => `<div class="note ${a.revoked ? "bad" : ""}"><b>Standing authorisation <span class="mono">${esc(a.id)}</span></b> ${chip(a.revoked ? "Revoked" : "Active", a.revoked ? "bad" : "ok")}: ${esc(a.procedure)} · ${esc(a.hospitals.join(", "))} · ${a.sessionsLeft}/${a.sessionsGranted} sessions left · max ${inr(a.perSessionCap)} · to ${esc(a.validTo)} · by ${esc(a.approver)} ${a.revoked ? "" : `<button class="btn danger sm" data-rev="${esc(a.id)}" style="margin-left:8px">Revoke</button>`}</div>`).join("") + `<div class="err" id="rerr"></div>`)}
  ${panel("chart", "Timeline by hospital", timelineSvg(p.timeline))}
  ${open.map(r => panel("alert", `Open request ${r.id}`, `<p class="muted">${esc(r.hospital)} · ${esc(r.serviceDate)} · <span class="mono">${inr(r.amount)}</span></p>${rcards(r.flags) || `<p class="muted">Matches the patient's pattern.</p>`}<button class="btn sec sm" id="gq">Open in session approvals</button>`)).join("")}
  ${panel("flag", "Out-of-pattern sessions in the past", flagged.length ? `<div class="tw"><table><tr><th>Date</th><th>Hospital</th><th class="r">Amount</th><th>Why</th></tr>${flagged.map(t => `<tr><td class="mono">${esc(t.date)}</td><td>${esc(t.hospital)}, ${esc(t.city)}</td><td class="r">${inr(t.billed)}</td><td>${rcards(t.flags)}</td></tr>`).join("")}</table></div><p class="muted small">A change of hospital or phone can be innocent. These are prompts to ask the patient, not findings.</p>` : `<p class="muted">None. Every past session matched the pattern.</p>`)}
  ${panel("hospital", "Hospitals used", `<div class="tw"><table><tr><th>Hospital</th><th>City</th><th class="r">Sessions</th><th class="r">Billed</th><th class="r">Paid</th></tr>${p.hospitals.map(h => `<tr class="click" data-h="${esc(h.hospital)}"><td>${esc(h.hospital)}</td><td>${esc(h.city)}</td><td class="r">${h.episodes}</td><td class="r">${inr(h.billed)}</td><td class="r">${inr(h.paid)}</td></tr>`).join("")}</table></div>`)}`;
  $("#pp").onchange = e => { state.adjPatient = e.target.value; render(); };
  $$("[data-rev]").forEach(b => b.onclick = async () => { const reason = prompt("Reason for revoking?"); if (!reason) return; try { await adj(`/api/adjudicator/standing/${b.dataset.rev}/revoke`, { ...me(), note: reason }); render(); } catch (x) { $("#rerr").textContent = x.message; } });
  const gq = $("#gq"); if (gq) gq.onclick = () => { state.sub = "queue"; render(); }; wire();
}

// ---------- Network 360 ----------
async function pane360n() {
  const rows = await adj("/api/adjudicator/network"); state.netHospital ||= rows[0].hospital;
  const n = await adj(`/api/adjudicator/network/${encodeURIComponent(state.netHospital)}`), me_ = rows.find(r => r.hospital === n.hospital), max = Math.max(1, ...n.monthly.map(m => m[1]));
  $("#app").innerHTML = head("network", "Network 360", "How each hospital bills and how its patients' sessions compare with their own pattern. Each hospital has only about 8 claims, so claim flag rates are a prompt, not a finding.", chip(rows.filter(r => r.status === "review").length + " under review", "warn")) +
  panel("network", "Network hospitals", `<div class="tw"><table><tr><th>Hospital</th><th>City</th><th class="r">Billing vs benchmark</th><th class="r">Claim flag rate</th><th class="r">Repudiated</th><th class="r">Out of pattern</th><th>Status</th></tr>${rows.map(r => `<tr class="click ${r.hospital === n.hospital ? "sel" : ""}" data-n="${esc(r.hospital)}"><td><b>${esc(r.hospital)}</b></td><td>${esc(r.city)}</td><td class="r ${r.billingVsBenchmark > 1.3 ? "bad" : ""}">${r.billingVsBenchmark ? r.billingVsBenchmark + "x" : "–"}</td><td class="r">${pct(r.flagRate)}</td><td class="r">${pct(r.repudiationRate)}</td><td class="r">${pct(r.outOfPatternShare)}</td><td>${chip(r.status === "review" ? "Review" : "Normal", r.status === "review" ? "warn" : "ok")}</td></tr>`).join("")}</table></div>`) +
  panel("hospital", `${n.hospital}, ${n.city} (${n.tier})`, `<div class="kpis" style="margin-bottom:6px">${kpi("pulse", "Treatments on record", n.treatments, `${n.patients} patients`)}${kpi("bench", "Claims", n.claims, inr(Number(n.claimedTotal)) + " claimed")}${kpi("chart", "Billing vs benchmark", n.billingVsBenchmark ? n.billingVsBenchmark + "x" : "–", "same treatments, same city tier", bar(Math.min(100, (n.billingVsBenchmark || 0) / 2 * 100), n.billingVsBenchmark > 1.3 ? "b" : "p"))}${kpi("flag", "Out of pattern", pct(n.outOfPatternShare), "patients' own pattern")}</div>
    ${me_.reasons.map(t => `<div class="rc medium"><b>Review reason</b>${esc(t)}</div>`).join("")}
    <div class="grid" style="margin-top:10px"><div><h3>Treatments per month</h3><svg viewBox="0 0 600 100" width="100%" role="img" aria-label="Treatments per month">${n.monthly.map((m, i) => { const w = 560 / n.monthly.length, h = 64 * m[1] / max; return `<rect x="${20 + i * w + 4}" y="${74 - h}" width="${w - 8}" height="${h}" fill="#0ea5e9"><title>${esc(m[0])}: ${m[1]}</title></rect><text x="${20 + i * w + w / 2}" y="92" text-anchor="middle">${esc(m[0].slice(5))}</text>`; }).join("") || `<text x="20" y="40">No treatment history</text>`}</svg></div>
    <div><h3>Top treatments</h3>${n.topProcedures.map(t => `<div class="row"><span style="flex:1">${esc(t[0])}</span><span class="mono">${t[1]}</span></div>`).join("") || `<span class="muted">none</span>`}<h3>Claim flags raised</h3>${Object.entries(n.flagTypes).map(([k, v]) => `<div class="row"><span style="flex:1">${esc(label(k))}</span><span class="mono">${v}</span></div>`).join("") || `<span class="muted">none</span>`}</div></div>`);
  $$("tr[data-n]").forEach(r => r.onclick = () => { state.netHospital = r.dataset.n; render(); });
}

// ---------- rule matrix & semantic search ----------
async function paneRules() {
  const [cl, info, ev] = await Promise.all([api("/api/policy/clauses"), api("/api/policy/vector-db"), api("/api/policy/eval")]);
  const q = state.rq ?? "charges for the bed and nurse per night";
  const modes = ["bm25", "vector", "hybrid"], res = await Promise.all(modes.map(m => api(`/api/policy/search?mode=${m}&k=4&q=${encodeURIComponent(q)}`)));
  const col = (m, r) => `<div><h3>${{ bm25: "BM25 · keywords", vector: "Vector DB · meaning", hybrid: "Hybrid · both fused" }[m]}</h3>${r.map(d => `<div class="rc"><b>${esc(d.clause)} · ${esc(d.title)}</b><span class="chip">${d.score}</span> ${d.mode && d.mode !== m ? `<span class="chip info">${esc(d.mode)}</span>` : ""}<div class="small muted" style="margin-top:3px">${esc(d.text.slice(0, 130))}…</div></div>`).join("") || `<p class="muted small">No match.</p>`}</div>`;
  const cf = (state.cf || "").toLowerCase(), shown = cl.filter(c => !cf || `${c.number} ${c.title} ${c.section} ${c.tags.join(" ")}`.toLowerCase().includes(cf));
  $("#app").innerHTML = head("rules", "Rule matrix & policy search", "The numbered Arogya Sanjeevani clauses the engine cites, with keyword (BM25), semantic (vector database) and hybrid retrieval side by side.", chip(info.store, "info")) +
  `<div class="kpis">${kpi("rules", "Clauses indexed", info.clauses, "numbered policy clauses")}${kpi("db", "Bill terms indexed", info.terms, "hospital wording → standard head")}${kpi("bolt", "Embedder", `<span style="font-size:15px;line-height:22px">${esc(info.embedder.split(" ")[0])}</span>`, info.embedder.includes("ollama") ? "neural, open source" : "offline LSA: no neural model available")}
   ${kpi("chart", "Vector recall@3 (held-out)", ev.retrieval_heldout.modes.vector["recall@3"].toFixed(2), `BM25 ${ev.retrieval_heldout.modes.bm25["recall@3"].toFixed(2)} · ${ev.retrieval_heldout.queries} unseen questions`)}</div>
  ${panel("search", "Semantic search sandbox", `<form class="row" id="rf"><input id="rq" value="${esc(q)}" style="flex:1;min-width:240px" aria-label="Ask a policy question"><button class="btn" type="submit">${ic("search")}Search</button></form>
    <div class="grid" style="margin-top:10px">${modes.map((m, i) => col(m, res[i])).join("")}</div>
    <h3>Hospital wording → standard head</h3><form class="row" id="nf"><input id="nt" placeholder="e.g. bed charges, theatre consumables" style="flex:1;min-width:240px"><button class="btn sec" type="submit">Normalise</button></form><div id="nres"></div>`)}
  ${panel("chart", "Retrieval quality, measured", `<div class="tw"><table><tr><th>Question set</th><th>Mode</th><th class="r">recall@3</th><th class="r">MRR</th></tr>${Object.entries(ev).filter(([k]) => k !== "embedder").flatMap(([k, s]) => Object.entries(s.modes).map(([m, v], i) => `<tr><td>${i ? "" : `${esc(k === "retrieval_eval" ? "Development" : "Held-out")} (${s.queries} questions)`}</td><td>${esc(m)}</td><td class="r">${v["recall@3"].toFixed(2)}</td><td class="r">${v.mrr.toFixed(2)}</td></tr>`)).join("")}</table></div>
    <p class="muted small">Small question sets, written by the same team that wrote the glossary the offline embedder learns from. Treat the gap between modes as indicative. A neural embedder (Ollama) should be re-measured with <span class="mono">python eval_retrieval.py</span>.</p>`)}
  ${panel("list", "Clause configuration matrix", `<input id="cf" placeholder="Filter clauses…" value="${esc(state.cf || "")}" style="margin-bottom:8px;width:260px"><div class="tw"><table><tr><th>Clause</th><th>Benefit / rule</th><th>Type</th><th>Section</th></tr>${shown.map(c => `<tr><td>${chip(c.number, "info")}</td><td><b>${esc(c.title)}</b><details><summary>text</summary><span class="muted">${esc(c.text)}</span></details></td><td>${chip(c.ruleType)}</td><td class="muted">${esc(c.section)}</td></tr>`).join("")}</table></div>`)}`;
  $("#rf").onsubmit = e => { e.preventDefault(); state.rq = $("#rq").value; render(); };
  $("#cf").onchange = e => { state.cf = e.target.value; render(); };
  $("#nf").onsubmit = async e => { e.preventDefault(); const r = await api(`/api/policy/normalize?term=${encodeURIComponent($("#nt").value)}`); $("#nres").innerHTML = `<div class="rc ${r.via === "none" ? "warn" : "ok"}"><b>${esc(r.normalized)} · matched by ${esc(r.via)}${r.similarity ? " (" + r.similarity + ")" : ""}</b>${esc(r.category || "")} · ${esc(r.primaryClause || "")}</div>`; };
}

// ---------- audit ----------
async function paneAudit() {
  const rows = await adj("/api/adjudicator/audit");
  const kind = a => /reject|revoke/.test(a) ? "b" : /settled|approve|granted/.test(a) ? "" : "i";
  $("#app").innerHTML = head("audit", "Audit & compliance", "Every adjudicator action and settlement, newest first, with an integrity hash of its fields. Approvals are bound to the exact worksheet they were given for.", chip(rows.length + " events", "info")) +
  panel("audit", "Audit trail", rows.length ? `<div class="tl">${rows.map(r => `<div class="n ${kind(r.action)}"><span class="t">${esc(fmtTime(r.at))}</span><b>${esc(r.action)} · ${esc(r.ref)}</b><p>${esc(r.approver || "system")}${r.level ? " · " + esc(r.level) : ""}${r.settlementId ? ` · <span class="mono">${esc(r.settlementId)}</span>` : ""} <span class="mono muted">sha256:${esc(r.hash)}</span></p></div>`).join("")}</div>` : `<p class="muted">No actions yet. Approve, edit or reject something and it appears here.</p>`);
}
