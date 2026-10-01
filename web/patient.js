const $ = (sel) => document.querySelector(sel);

const params = new URLSearchParams(location.search);
const PID = (params.get("pid") || "P12").toUpperCase();
let language = params.get("lang") || "en";
let patient = null;

const TEXT = {
  en: {
    waited: "waited",
    more: "likely more",
    journey: "Your journey",
    updates: "Recent updates",
    noAction: "No action needed from you right now.",
    interrupt: "I still don’t know why I’m waiting",
    note: "Tapping this tells the front desk to check on you. You do not need to queue again.",
    sent: "The front desk has been notified. Someone will check on you shortly.",
    done: "All done — thank you for visiting!",
  },
  hi: {
    waited: "प्रतीक्षा की",
    more: "लगभग और",
    journey: "आपकी यात्रा",
    updates: "हाल के अपडेट",
    noAction: "अभी आपको कुछ करने की ज़रूरत नहीं है।",
    interrupt: "मुझे अब भी समझ नहीं आया कि मैं क्यों प्रतीक्षा कर रहा/रही हूँ",
    note: "इससे फ्रंट डेस्क को सूचना मिलेगी। आपको दोबारा लाइन में लगने की ज़रूरत नहीं है।",
    sent: "फ्रंट डेस्क को सूचित कर दिया गया है। कोई आपसे जल्द मिलेगा।",
    done: "सब पूरा हुआ — आने के लिए धन्यवाद!",
  },
};

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

function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.className = `toast show${isError ? " error" : ""}`;
  clearTimeout(el._timer);
  el._timer = setTimeout(() => (el.className = "toast"), 3600);
}

function applyStaticText() {
  const t = TEXT[language];
  $("#waited-label").textContent = t.waited;
  $("#more-label").textContent = t.more;
  $("#journey-title").textContent = t.journey;
  $("#updates-title").textContent = t.updates;
  $("#interrupt-btn").textContent = t.interrupt;
  $("#interrupt-note").textContent = t.note;
  document.querySelectorAll(".lang-toggle button").forEach((button) => {
    button.classList.toggle("active", button.dataset.lang === language);
  });
}

function render(data) {
  patient = data.patient;
  const explanation = data.explanation;
  const card = $("#status-card");
  card.className = "status-card";
  if (patient.blocked) card.classList.add("blocked");
  if (["READY_FOR_PICKUP", "NOTIFIED", "DONE"].includes(patient.state)) card.classList.add("ready");

  $("#pid").textContent = patient.id;
  $("#patient-name").textContent = patient.name;
  $("#state-label").textContent = patient.state_label;
  $("#wait-time").textContent = fmtWait(patient.wait_minutes);
  $("#eta").textContent = patient.state === "DONE" || patient.state === "NOTIFIED"
    ? "—"
    : `~${Math.round(explanation.eta_minutes ?? data.facts.eta_minutes ?? 0)}m`;
  $("#explain").textContent = explanation.message;
  $("#action").textContent = explanation.action_needed || TEXT[language].noAction;

  $("#steps").innerHTML = data.steps.map((s) => `
    <span class="step ${s.status}">${esc(s.label)}</span>`).join("");

  $("#timeline").innerHTML = data.patient.timeline.slice(-6).reverse().map((event) => `
    <li>
      <span class="when">${new Date(event.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
      <span>${esc(event.text)}</span>
    </li>`).join("");

  const button = $("#interrupt-btn");
  button.disabled = patient.state === "DONE";
  button.onclick = async () => {
    button.disabled = true;
    try {
      await fetch(`/api/patients/${PID}/interruption`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          at_role: patient.blocked ? "reception" : (patient.owner || "reception"),
          note: "Patient pressed “why am I waiting”",
        }),
      });
      toast(TEXT[language].sent);
    } catch (error) {
      toast("Could not notify the desk.", true);
      button.disabled = false;
    }
  };
}

async function load() {
  try {
    const response = await fetch(`/api/patients/${PID}/explain?language=${language}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Patient not found");
    render(data);
  } catch (error) {
    $(".patient-main").innerHTML = `
      <div class="panel error-card">
        <h2>We couldn’t find token ${esc(PID)}</h2>
        <p class="privacy">${esc(error.message)}</p>
        <a href="/patient?pid=P12">Try the demo patient (P12)</a>
      </div>`;
  }
}

document.querySelectorAll(".lang-toggle button").forEach((button) => {
  button.onclick = () => {
    language = button.dataset.lang;
    applyStaticText();
    load();
  };
});

applyStaticText();
load();
setInterval(load, 5000);
