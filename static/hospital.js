async function viewHospital() {
  const names = await api("/api/hospital"), pats = await api("/api/patient");
  state.hospital ||= names[0];
  const h = await api(`/api/hospital/${encodeURIComponent(state.hospital)}`);
  const sc = h.scorecard, ratio = sc.billingVsBenchmark, rooms = Object.keys(h.rates.rooms), pkgs = Object.keys(h.rates.packages);
  const waiting = h.claimPreapprovals.filter(x => x.state === "preapproved");
  const outcome = x => x.state === "preapproved" || x.state === "cleared" || x.status === "Approved" ? `<div class="note"><b>${esc(x.status)}.</b> ${x.guaranteed ? `Guaranteed <span class="mono">${inr(x.guaranteed)}</span> (after 5% co-payment). ID <span class="mono">${esc(x.id)}</span>. Send the bill after treatment.` : "You can proceed with the session."}</div>`
    : x.state === "settled" ? `<div class="note"><b>Settled <span class="mono">${inr(x.settled)}</span>.</b>${x.change.length ? "<ul>" + x.change.map(c => `<li>${c.direction === "up" ? "Up" : "Down"} <span class="mono">${inr(c.amount)}</span>: ${esc(c.text)}</li>`).join("") + "</ul>" : " Matches the pre-approval."}</div>`
    : `<div class="note warn"><b>${esc(x.status)}.</b> ${x.state === "adjudication" ? "The final amount needs a short check by an adjudicator." : "An adjudicator will look at this shortly. We may call the patient's registered number."}</div>`;
  $("#app").innerHTML = head("hospital", h.hospital, `${esc(h.city)} · request approval for a session or a whole treatment, usually answered in seconds`,
    `<div>${select("pick", names.map(n => [n, n]), state.hospital, 'aria-label="Choose hospital (demo)"')}</div>`) +
  `<div id="h-score" class="kpis">${kpi("pulse", "Treatments on record", sc.treatments, `${sc.patients} patients`)}
    ${kpi("chart", "Billing vs benchmark", ratio ? ratio + "x" : "–", ratio > 1.3 ? "<span class='bad'>above peers: sessions are reviewed</span>" : "in line with your city tier", bar(Math.min(100, (ratio || 0) / 2 * 100), ratio > 1.3 ? "b" : "p"))}
    ${kpi("shield", "Pre-approvals waiting for a bill", waiting.length, "send the bill after treatment")}
    ${kpi("doc", "Claims on file", sc.claims, "")}</div>
  <div class="grid">
   <form id="f" class="panel"><div class="ph">${ic("bolt")}<h2>New session request</h2></div><p class="muted small">For one dialysis, chemotherapy or physiotherapy session.</p>
    <label>Patient</label><select name="policy">${pats.map(p => `<option value="${esc(p.policy)}">${esc(p.name)}</option>`).join("")}</select>
    <label>Treatment</label><select name="procedure">${pkgs.map(k => `<option>${esc(k)}</option>`).join("")}</select>
    <div class="fields"><div><label>Session date</label><input type="date" name="serviceDate" value="2026-10-28"></div><div><label>Amount (₹)</label><input type="number" name="amount" min="1" value="2800"></div><div><label>Patient phone, last 4</label><input name="contactLast4" maxlength="4" placeholder="1234"></div></div>
    <p><button class="btn" type="submit">Request approval</button></p><div id="res"></div></form>
   <form class="panel" id="cp" ><div class="ph">${ic("shield")}<h2>Pre-approve a regular treatment</h2></div><p class="muted small">Eligible patients get a guaranteed amount from your contracted rates (synced <span class="mono">${esc(h.ratesSyncedAt)}</span>).</p>
    <label>Patient</label><select name="policy">${pats.map(p => `<option value="${esc(p.policy)}">${esc(p.name)}</option>`).join("")}</select>
    <label>Treatment</label><select name="procedure">${pkgs.map(k => `<option value="${esc(k)}">${esc(k)} · ${inr(h.rates.packages[k])}</option>`).join("")}</select>
    <div class="fields"><div><label>Planned date</label><input type="date" name="serviceDate" value="2026-10-28"></div><div><label>Room</label><select name="roomCategory">${rooms.map(k => `<option value="${esc(k)}">${esc(label(k))} · ${inr(h.rates.rooms[k])}/day</option>`).join("")}</select></div>
     <div><label>Room days</label><input type="number" name="roomDays" min="0" max="7" value="0"></div><div><label>Patient phone, last 4</label><input name="contactLast4" maxlength="4"></div></div>
    <p><button class="btn" type="submit">Get pre-approval</button></p><div id="cpres"></div></form>
  </div>
  <form class="panel" id="bill"><div class="ph">${ic("doc")}<h2>Send bill after treatment</h2></div><p class="muted small">Only the amount is adjusted, up or down, from this bill and the discharge summary.</p>
    <div class="fields"><div style="flex:2"><label>Pre-approval</label><select name="rid">${waiting.map(x => `<option value="${esc(x.id)}">${esc(x.id)} · ${esc(x.patient)} · ${esc(x.procedure)}</option>`).join("") || "<option value=''>none waiting for a bill</option>"}</select></div>
     <div><label>Bill number</label><input name="billNumber"></div><div><label>Treatment charge (₹)</label><input type="number" name="package" min="0"></div><div><label>Other items, e.g. kit (₹)</label><input type="number" name="other" min="0" value="0"></div>
     <div><label>Room days billed</label><input type="number" name="billedDays" min="0" value="0"></div><div><label>Room rate billed (₹/day)</label><input type="number" name="billedRate" min="0" value="0"></div></div>
    <h3>Discharge summary</h3>
    <div class="fields"><div><label>Admitted</label><input type="date" name="adm"></div><div><label>Discharged</label><input type="date" name="dis"></div><div><label>Room</label><select name="dsRoom">${rooms.map(k => `<option value="${esc(k)}">${esc(label(k))}</option>`).join("")}</select></div><div><label>Room days</label><input type="number" name="dsDays" min="0" value="0"></div></div>
    <p><button class="btn" type="submit">Send bill</button></p><div id="billres"></div></form>
  <div id="h-list">${panel("list", "Claim pre-approvals", `<div class="tw"><table><tr><th>ID</th><th>Patient</th><th>Treatment</th><th>Date</th><th>Status</th><th class="r">Guaranteed</th><th class="r">Final</th></tr>${h.claimPreapprovals.map(x => `<tr><td class="mono">${esc(x.id)}</td><td>${esc(x.patient)}</td><td>${esc(x.procedure)}</td><td class="mono">${esc(x.serviceDate)}</td><td>${chip(x.status, pubKind(x.status))}</td><td class="r">${x.guaranteed ? inr(x.guaranteed) : "–"}</td><td class="r">${x.settled != null ? inr(x.settled) : "–"}</td></tr>`).join("") || `<tr><td colspan="7" class="muted">None yet.</td></tr>`}</table></div>`)}
  ${panel("clock", "Session requests", `<div class="tw"><table><tr><th>ID</th><th>Patient policy</th><th>Date</th><th>Treatment</th><th class="r">Amount</th><th>Status</th></tr>${h.requests.map(r => `<tr><td class="mono">${esc(r.id)}</td><td class="mono">${esc(r.policy)}</td><td class="mono">${esc(r.serviceDate)}</td><td>${esc(r.procedure)}</td><td class="r">${inr(r.amount)}</td><td>${chip(r.status, pubKind(r.status))}</td></tr>`).join("") || `<tr><td colspan="6" class="muted">No requests yet.</td></tr>`}</table></div>`)}</div>`;
  $("#pick").onchange = e => { state.hospital = e.target.value; render(); };
  const show = async (sel, x) => { await render(); $(sel).innerHTML = outcome(x); };
  const fail = (sel, z) => { $(sel).innerHTML = `<div class="err">${esc(z.message)}</div>`; };
  $("#f").onsubmit = async e => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)); f.amount = +f.amount;
    try { await show("#res", await api(`/api/hospital/${encodeURIComponent(state.hospital)}/preauth`, { body: f })); } catch (z) { fail("#res", z); } };
  $("#cp").onsubmit = async e => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)); f.roomDays = +f.roomDays;
    try { await show("#cpres", await api(`/api/hospital/${encodeURIComponent(state.hospital)}/claim-preapproval`, { body: f })); } catch (z) { fail("#cpres", z); } };
  $("#bill").onsubmit = async e => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)); if (!f.rid) return;
    const L = []; if (+f.billedDays) L.push({ description: "Room", category: "room", qty: +f.billedDays, rate: +f.billedRate, amount: +f.billedDays * +f.billedRate });
    L.push({ description: "Treatment package", category: "procedure", qty: 1, rate: +f.package, amount: +f.package }); if (+f.other) L.push({ description: "Admission kit and other items", category: "consumables", qty: 1, rate: +f.other, amount: +f.other });
    const body = { bill: { billNumber: f.billNumber, totalStated: L.reduce((n, l) => n + l.amount, 0), lines: L }, discharge: { admissionDate: f.adm, dischargeDate: f.dis, roomCategory: f.dsRoom, roomDays: +f.dsDays } };
    try { await show("#billres", await api(`/api/hospital/${encodeURIComponent(state.hospital)}/claim-preapproval/${f.rid}/bill`, { body })); } catch (z) { fail("#billres", z); } };
}
