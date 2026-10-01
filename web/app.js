const $ = (sel) => document.querySelector(sel);

const COLUMNS = [
  { id: "reception", title: "Front desk", states: ["WAITING_RECEPTION", "WAITING_DOC_FIX", "READY_FOR_PICKUP"] },
  { id: "flagged", title: "Flagged — no owner", states: [] },
  { id: "nurse", title: "Nurse", states: ["WAITING_TRIAGE", "IN_TRIAGE"] },
  { id: "clinician", title: "Doctor", states: ["WAITING_CONSULT", "IN_CONSULT", "WAITING_REPORT_REVIEW"] },
  { id: "lab", title: "Lab", states: ["WAITING_LAB_COLLECTION", "LAB_PROCESSING"] },
  { id: "patient", title: "Patient", states: ["NOTIFIED"] },
  { id: "done", title: "Done", states: ["DONE"] },
];

const ACTIONS = {
  ARRIVED: [{ label: "Route to triage", event: "ROUTED", actor: "reception", payload: { queue: "triage" } }],
  WAITING_RECEPTION: [
    { label: "Report ready", event: "MARK_READY", actor: "reception" },
    { label: "Complete", event: "COMPLETED", actor: "reception" },
  ],
  WAITING_DOC_FIX: [{ label: "Mark fixed", event: "REPORT_FIXED", actor: "reception" }],
  WAITING_TRIAGE: [{ label: "Call in (nurse)", event: "CALLED", actor: "nurse" }],
  IN_TRIAGE: [{ label: "Triage done", event: "COMPLETED", actor: "nurse" }],
  WAITING_CONSULT: [{ label: "Call in (doctor)", event: "CALLED", actor: "clinician" }],
  IN_CONSULT: [
    { label: "Order lab", event: "LAB_ORDERED", actor: "clinician", payload: { test: "blood panel" } },
    { label: "Doc fix needed", event: "REPORT_FIX_REQUESTED", actor: "clinician" },
    { label: "Consult done", event: "COMPLETED", actor: "clinician" },
  ],
  WAITING_LAB_COLLECTION: [{ label: "Sample collected", event: "STARTED", actor: "lab" }],
  LAB_PROCESSING: [{ label: "Result ready", event: "LAB_RESULT_READY", actor: "lab" }],
  WAITING_REPORT_REVIEW: [{ label: "Accept review", event: "HANDOFF_ACCEPTED", actor: "clinician" }],
  READY_FOR_PICKUP: [{ label: "Notify patient", action: "notify", payload: { channel: "sms" } }],
  NOTIFIED: [{ label: "Picked up", event: "PICKED_UP", actor: "reception" }],
  DONE: [],
};

let currentState = null;
let pollTimer = null;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtWait(minutes) {
  const m = Math.max(0, Math.round(minutes || 0));
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

function waitClass(minutes, blocked) {
  if (blocked) return "danger";
  if (minutes > 20) return "danger";
  if (minutes > 10) return "warn";
  return "";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.className = `toast show${isError ? " error" : ""}`;
  clearTimeout(el._timer);
  el._timer = setTimeout(() => (el.className = "toast"), 3200);
}

function clock() {
  const now = new Date();
  $("#clock").textContent = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function renderKpis(kpis) {
  const cards = [
    { label: "Active patients", value: kpis.active_patients },
    { label: "Median wait", value: `${Math.round(kpis.median_wait_minutes ?? 0)}m`, cls: kpis.median_wait_minutes > 15 ? "warn" : "" },
    { label: "Waits with owner", value: `${Math.round(kpis.waits_with_owner_pct)}%`, cls: kpis.waits_with_owner_pct < 100 ? "danger" : "ok" },
    { label: "Dropped handoffs", value: kpis.dropped_handoffs, cls: kpis.dropped_handoffs > 0 ? "danger" : "ok" },
    { label: "Orphan minutes", value: Math.round(kpis.orphan_minutes) , cls: kpis.orphan_minutes > 0 ? "danger" : "" },
    { label: "Interruptions today", value: kpis.interruptions_today, cls: kpis.interruptions_today > 8 ? "warn" : "" },
  ];
  $("#kpis").innerHTML = cards.map((c) => `
    <div class="kpi">
      <div class="label">${c.label}</div>
      <div class="value ${c.cls || ""}">${c.value}</div>
    </div>`).join("");
}

function cardHtml(patient) {
  const wc = waitClass(patient.wait_minutes, patient.blocked);
  const classes = ["card"];
  if (patient.blocked) classes.push("blocked");
  else if (["IN_TRIAGE", "IN_CONSULT"].includes(patient.state)) classes.push("active");
  else if (patient.state === "DONE") classes.push("done");
  return `
    <article class="${classes.join(" ")}" data-pid="${esc(patient.id)}">
      <div class="card-top">
        <span class="pid">${esc(patient.id)}</span>
        <span class="type ${esc(patient.type)}">${esc(patient.type)}</span>
        <span class="wait ${wc}">${fmtWait(patient.wait_minutes)}</span>
      </div>
      <h3>${esc(patient.name)}</h3>
      <p class="state">${esc(patient.state_label)}${patient.owner_label ? ` · ${esc(patient.owner_label)}` : ""}</p>
      ${patient.reason ? `<p class="reason">${esc(patient.reason)}</p>` : ""}
      ${patient.blocked ? `<p class="flag">⚠ ${esc(patient.block_reason || "no owner on this wait")}</p>` : ""}
    </article>`;
}

function renderBoard(patients) {
  const groups = {};
  for (const column of COLUMNS) groups[column.id] = [];
  for (const patient of patients) {
    if (patient.blocked) { groups.flagged.push(patient); continue; }
    if (patient.state === "DONE") { groups.done.push(patient); continue; }
    const column = COLUMNS.find((c) => c.states.includes(patient.state));
    if (column) groups[column.id].push(patient);
  }
  groups.done.sort((a, b) => a.wait_minutes - b.wait_minutes);
  groups.done = groups.done.slice(0, 6);

  $("#board").innerHTML = COLUMNS.map((column) => {
    const items = groups[column.id];
    const body = items.length
      ? items.map(cardHtml).join("")
      : `<div class="empty">—</div>`;
    return `
      <div class="column ${column.id}">
        <h2>${column.title} <span>${items.length}</span></h2>
        <div class="cards">${body}</div>
      </div>`;
  }).join("");
}

function renderAlerts(alerts) {
  $("#alert-count").textContent = alerts.length;
  $("#alerts").innerHTML = alerts.length ? alerts.map((alert) => `
    <div class="alert">
      <p class="msg">${esc(alert.message)}</p>
      <div class="row">
        ${alert.handoff_id ? `<button class="btn small" data-resolve="${alert.handoff_id}">Resolve handoff</button>` : ""}
        <button class="btn small ghost" data-open-patient="${esc(alert.patient_id)}">Open patient</button>
      </div>
    </div>`).join("") : `<div class="empty">No alerts. Every wait has an owner.</div>`;
}

function renderLlm(llm) {
  const pill = $("#llm-pill");
  if (llm.mode === "live") {
    pill.textContent = `AI live · ${llm.model || llm.provider}`;
    pill.className = "pill live";
  } else {
    pill.textContent = "AI fallback · deterministic";
    pill.className = "pill fallback";
  }
}

function renderBottlenecks(items) {
  const top = (items || []).slice(0, 5);
  if (!top.length) {
    $("#bottlenecks").innerHTML = `<div class="empty">No waiting time recorded yet.</div>`;
    return;
  }
  const max = Math.max(...top.map((b) => b.total_minutes), 1);
  $("#bottlenecks").innerHTML = top.map((b) => `
    <div class="bar-row">
      <div class="bar-head">
        <span>${esc(b.state_label)}</span>
        <span>${Math.round(b.total_minutes)}m · p90 ${b.p90_minutes ?? "—"}m</span>
      </div>
      <div class="bar"><div class="fill" style="width:${Math.max(4, Math.round((100 * b.total_minutes) / max))}%"></div></div>
    </div>`).join("");
}

function renderNextUp(nextUp) {
  const roles = ["reception", "nurse", "clinician", "lab"];
  const labels = { reception: "Front desk", nurse: "Nurse", clinician: "Doctor", lab: "Lab" };
  $("#nextup").innerHTML = roles.map((role) => {
    const entry = (nextUp || {})[role];
    if (!entry || !entry.patient) {
      return `<div class="next-row"><span class="next-role">${labels[role]}</span><span class="empty">—</span></div>`;
    }
    return `
      <div class="next-row" data-open-patient="${esc(entry.patient.id)}">
        <span class="next-role">${labels[role]}</span>
        <span class="next-who"><strong>${esc(entry.patient.id)}</strong> ${esc(entry.patient.name)} · ${fmtWait(entry.waited_minutes)}</span>
        <span class="next-why">${esc(entry.reason)}</span>
      </div>`;
  }).join("");
}

async function refresh() {
  try {
    currentState = await api("/api/state");
    renderKpis(currentState.kpis);
    renderBoard(currentState.patients);
    renderAlerts(currentState.alerts);
    renderBottlenecks(currentState.bottlenecks);
    renderNextUp(currentState.next_up);
    renderLlm(currentState.llm);
  } catch (error) {
    console.error(error);
  }
}

async function openPatient(pid) {
  const modal = $("#patient-modal");
  $("#modal-content").innerHTML = `<div class="empty">Loading…</div>`;
  modal.showModal();
  try {
    const patient = await api(`/api/patients/${pid}`);
    const actions = ACTIONS[patient.state] || [];
    const resolveButton = patient.blocked && patient.open_handoff
      ? `<button class="btn danger" data-resolve="${patient.open_handoff.id}">Resolve handoff #${patient.open_handoff.id}</button>`
      : "";
    $("#modal-content").innerHTML = `
      <div class="modal-head">
        <div>
          <h2>${esc(patient.name)} <span class="pid">${esc(patient.id)}</span></h2>
          <p class="sub">${esc(patient.state_label)} · owner: ${esc(patient.owner_label || "none")} · waiting ${fmtWait(patient.wait_minutes)}</p>
        </div>
        <button class="btn ghost small" id="modal-close">Close</button>
      </div>
      ${patient.reason ? `<p class="sub">“${esc(patient.reason)}”</p>` : ""}
      ${patient.blocked ? `<div class="alert" style="margin-top:12px"><p class="msg">⚠ ${esc(patient.block_reason || "No owner on this wait")}</p><div class="row">${resolveButton}</div></div>` : ""}
      <div class="section-title">Journey</div>
      <div class="steps">${patient.steps.map((s) => `<span class="step ${s.status}">${esc(s.label)}</span>`).join("")}</div>
      <div class="section-title">Explain to patient</div>
      <div class="explain-box" id="explain-box"><span class="empty">Loading…</span></div>
      <div class="section-title">Actions</div>
      <div class="actions">
        ${actions.map((a, i) => `<button class="btn" data-action="${i}">${esc(a.label)}</button>`).join("") || `<span class="empty">No actions available.</span>`}
        ${patient.state === "DONE" ? "" : `<button class="btn ghost" data-log-interrupt>Log interruption</button>`}
      </div>
      <div class="section-title">Timeline</div>
      <ul class="timeline">
        ${patient.timeline.slice().reverse().map((e) => `
          <li><span class="when">${new Date(e.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span><span>${esc(e.text)}</span></li>
        `).join("")}
      </ul>`;

    $("#modal-close").onclick = () => modal.close();
    $("#modal-content").querySelectorAll("[data-resolve]").forEach((button) => {
      button.onclick = async () => {
        try {
          await api(`/api/handoffs/${button.dataset.resolve}/accept`, { method: "POST", body: { by: "clinician" } });
          toast("Handoff resolved — patient is moving again.");
          modal.close();
          refresh();
        } catch (error) { toast(error.message, true); }
      };
    });
    $("#modal-content").querySelectorAll("[data-action]").forEach((button) => {
      button.onclick = async () => {
        const action = actions[Number(button.dataset.action)];
        try {
          if (action.action === "notify") {
            const result = await api(`/api/patients/${pid}/notify`, { method: "POST", body: action.payload || {} });
            toast(`SMS sent: ${result.message}`);
          } else {
            await api(`/api/patients/${pid}/event`, {
              method: "POST",
              body: { event_type: action.event, actor: action.actor, payload: action.payload || {} },
            });
            toast(`${action.label} recorded.`);
          }
          modal.close();
          refresh();
        } catch (error) { toast(error.message, true); }
      };
    });
    const interruptButton = $("#modal-content").querySelector("[data-log-interrupt]");
    if (interruptButton) {
      interruptButton.onclick = async () => {
        const staffRoles = ["reception", "nurse", "clinician", "lab"];
        const atRole = staffRoles.includes(patient.owner) ? patient.owner : "reception";
        try {
          await api(`/api/patients/${pid}/interruption`, {
            method: "POST",
            body: { at_role: atRole, note: "Staff noted an interruption" },
          });
          toast("Interruption logged.");
          refresh();
        } catch (error) { toast(error.message, true); }
      };
    }

    const explain = await api(`/api/patients/${pid}/explain?language=en`);
    const box = $("#explain-box");
    if (box) {
      box.innerHTML = `
        ${esc(explain.explanation.message)}
        ${explain.explanation.action_needed ? `<div class="action">→ ${esc(explain.explanation.action_needed)}</div>` : ""}
        <span class="mode" style="color:var(--muted);font-size:10.5px">${esc(explain.explanation.mode)}</span>`;
    }
  } catch (error) {
    $("#modal-content").innerHTML = `<div class="empty">Could not load patient: ${esc(error.message)}</div>`;
  }
}

function appendChat(role, html) {
  const log = $("#ask-log");
  const bubble = document.createElement("div");
  bubble.className = `bubble ${role}`;
  bubble.innerHTML = html;
  log.appendChild(bubble);
  log.scrollTop = log.scrollHeight;
  return bubble;
}

async function ask(question) {
  appendChat("user", esc(question));
  const thinking = appendChat("bot", `<span class="empty">Working…</span>`);
  try {
    const result = await api("/api/ask", { method: "POST", body: { question } });
    const tools = (result.trace || []).map((t) => `<span class="tool-chip">${esc(t.tool)}</span>`).join("");
    thinking.innerHTML = `
      ${esc(result.answer).replace(/\n/g, "<br/>")}
      <div class="tool-trace">${tools}</div>
      <span class="mode">${esc(result.mode)} mode</span>`;
  } catch (error) {
    thinking.innerHTML = `<span style="color:var(--danger)">${esc(error.message)}</span>`;
  }
}

function bind() {
  $("#ask-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("#ask-input");
    const question = input.value.trim();
    if (!question) return;
    input.value = "";
    ask(question);
  });

  $("#ask-chips").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (chip) ask(chip.textContent);
  });

  $("#board").addEventListener("click", (event) => {
    const card = event.target.closest("[data-pid]");
    if (card) openPatient(card.dataset.pid);
  });

  $("#alerts").addEventListener("click", async (event) => {
    const resolve = event.target.closest("[data-resolve]");
    const open = event.target.closest("[data-open-patient]");
    if (resolve) {
      try {
        await api(`/api/handoffs/${resolve.dataset.resolve}/accept`, { method: "POST", body: { by: "clinician" } });
        toast("Handoff resolved.");
        refresh();
      } catch (error) { toast(error.message, true); }
    }
    if (open) openPatient(open.dataset.openPatient);
  });

  $("#nextup").addEventListener("click", (event) => {
    const open = event.target.closest("[data-open-patient]");
    if (open) openPatient(open.dataset.openPatient);
  });

  $("#reset-btn").onclick = async () => {
    await api("/api/sim/reset", { method: "POST" });
    toast("Demo day reset.");
    refresh();
  };

  const walkinModal = $("#walkin-modal");
  $("#walkin-btn").onclick = () => {
    $("#walkin-form").reset();
    walkinModal.showModal();
  };
  $("#walkin-cancel").onclick = () => walkinModal.close();
  $("#walkin-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = $("#walkin-name").value.trim();
    const reason = $("#walkin-reason").value.trim();
    if (!name || !reason) return;
    try {
      const result = await api("/api/intake", { method: "POST", body: { name, reason } });
      walkinModal.close();
      const c = result.classification;
      toast(`${result.patient_id} ${name}: routed to ${c.queue} — ${c.summary} (${Math.round(c.confidence * 100)}% confident)`);
      refresh();
    } catch (error) { toast(error.message, true); }
  });

  $("#ask-chips").innerHTML = [
    "Where did waiting time go today?",
    "Whose work is being interrupted?",
    "What should reception do next?",
    "Any dropped handoffs?",
  ].map((q) => `<button class="chip" type="button">${q}</button>`).join("");
}

bind();
clock();
setInterval(clock, 1000);
refresh();
pollTimer = setInterval(refresh, 2500);
