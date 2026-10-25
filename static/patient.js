async function viewPatient() {
  const list = await api("/api/patient");
  state.patient ||= list[0].policy;
  const h = await api(`/api/patient/${state.patient}`);
  const used = h.coverTotal - h.remainingCover, held = h.requests.filter(r => r.status.includes("we will call"));
  const slots = h.standingApprovals.reduce((n, a) => n + a.sessionsLeft, 0);
  $("#app").innerHTML = head("user", `Hello, ${h.name.split(" ")[0]}`, `Policy <span class="mono">${esc(h.policy)}</span> · valid to ${esc(h.validTo)} · registered phone <span class="mono">${esc(h.registeredContact)}</span>`,
    `<div>${select("pick", list.map(p => [p.policy, p.name]), state.patient, 'aria-label="Choose patient (demo)"')}</div>`) +
  `<div id="p-overview" class="kpis">${kpi("rupee", "Cover left this year", inr(h.remainingCover), `of ${inr(h.coverTotal)} · ${pct(used / h.coverTotal)} used`, bar(used / h.coverTotal * 100, used / h.coverTotal > 0.8 ? "w" : "p"))}
    ${kpi("pulse", "Ongoing treatments", h.ongoingTreatments.length, h.ongoingTreatments.map(t => esc(t.procedure.split(" ")[0])).join(", ") || "none found")}
    ${kpi("shield", "Pre-approved sessions left", slots, slots ? "no forms needed" : "ask your hospital to request one")}
    ${kpi("clock", "Requests in progress", h.requests.filter(r => !r.status.startsWith("Approved")).length + h.preapprovals.filter(x => !["settled", "preapproved"].includes(x.state)).length, "usually answered in seconds")}</div>
  ${held.length ? `<div class="note warn"><b>We need a quick check.</b> For your safety we will call your registered number ${esc(h.registeredContact)} before approving ${held.length > 1 ? "these sessions" : "this session"}. In an emergency the hospital can contact us directly and treatment should not wait.</div>` : ""}
  ${panel("pulse", "My treatment", h.ongoingTreatments.length ? h.ongoingTreatments.map(t => `<div class="rc ok"><b>${esc(t.procedure)}</b>About every ${Math.round(t.everyDays)} days at ${esc(t.usualHospital)} · ${t.sessions} sessions · last on ${esc(t.lastSession)}
      <div class="cad" title="Last ${t.recentDates.length} sessions">${t.recentDates.map(d => `<i title="${esc(d)}"></i>`).join("")}</div></div>`).join("") +
      (h.standingApprovals.map(a => `<div class="note"><b>Pre-approved:</b> ${esc(a.procedure)} at ${esc(a.hospitals.join(", "))} · <span class="mono">${a.sessionsLeft}</span> sessions left until ${esc(a.validTo)}. Each session is approved in seconds when it matches your usual pattern.</div>`).join("") || `<p class="muted">Not pre-approved yet. Your hospital can ask us once and we approve the coming sessions in one step.</p>`)
      : `<p class="muted">No regular treatment found yet.</p>`, "", "p-treat")}
  <div id="p-pre">${h.preapprovals.length ? panel("shield", "Pre-approved treatment", h.preapprovals.map(x => `<div class="note ${["settled", "preapproved"].includes(x.state) ? "" : "warn"}"><b>${esc(x.procedure)}</b> at ${esc(x.hospital)} on ${esc(x.serviceDate)} ${chip(x.status, pubKind(x.status))}
      ${x.guaranteed ? `<br>Guaranteed <span class="mono">${inr(x.guaranteed)}</span> after your 5% co-payment: the hospital will not ask you to pay this upfront.` : ""}
      ${x.state === "settled" ? `<br><b>Final amount <span class="mono">${inr(x.settled)}</span>.</b>${x.change.length ? "<ul>" + x.change.map(c => `<li>${c.direction === "up" ? "Higher" : "Lower"} by <span class="mono">${inr(c.amount)}</span>: ${esc(c.text)}</li>`).join("") + "</ul>" : " Same as the pre-approval."}` : ""}</div>`).join("")) : ""}
  ${panel("clock", "Session requests", h.requests.length ? `<div class="tw"><table><tr><th>Date</th><th>Hospital</th><th>Treatment</th><th class="r">Amount</th><th>Status</th></tr>${h.requests.map(r => `<tr><td class="mono">${esc(r.serviceDate)}</td><td>${esc(r.hospital)}</td><td>${esc(r.procedure)}</td><td class="r">${inr(r.amount)}</td><td>${chip(r.status, pubKind(r.status))}</td></tr>`).join("")}</table></div>` : `<p class="muted">Nothing pending.</p>`)}</div>
  ${panel("card", "Recent treatments and payments", `<div class="tw"><table><tr><th>Date</th><th>Hospital</th><th>Treatment</th><th class="r">Billed</th><th class="r">Paid</th></tr>${h.recent.map(e => `<tr><td class="mono">${esc(e.date)}</td><td>${esc(e.hospital)}</td><td>${esc(e.procedure)}</td><td class="r">${inr(e.billed)}</td><td class="r">${inr(e.paid)}</td></tr>`).join("")}</table></div>`, "", "p-hist")}`;
  $("#pick").onchange = e => { state.patient = e.target.value; render(); };
}
