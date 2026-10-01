from __future__ import annotations

import hashlib
import json
import os
import re
import time

import httpx

from . import config  # noqa: F401  (loads .env on import)


def _openai_model():
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def _anthropic_model():
    return os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")


def provider():
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_BASE_URL"):
        return "openai"
    return "fallback"


def status():
    p = provider()
    model = _anthropic_model() if p == "anthropic" else _openai_model()
    return {
        "provider": p,
        "model": model if p != "fallback" else None,
        "mode": "live" if p != "fallback" else "fallback",
    }


_CACHE = {}
_CACHE_TTL_SECONDS = 600


def _cache_key(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _normalize_arguments(raw):
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def chat(messages, tools=None, json_mode=False, max_tokens=700, temperature=0.2, timeout=30):
    p = provider()
    if p == "fallback":
        return {"content": None, "tool_calls": [], "provider": "fallback"}
    key = _cache_key(
        {
            "provider": p,
            "model": _anthropic_model() if p == "anthropic" else _openai_model(),
            "messages": messages,
            "tools": tools,
            "json_mode": json_mode,
        }
    )
    cached = _CACHE.get(key)
    if cached and time.time() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    try:
        if p == "anthropic":
            result = _chat_anthropic(messages, tools, max_tokens, temperature, timeout)
        else:
            result = _chat_openai(messages, tools, json_mode, max_tokens, temperature, timeout)
    except Exception as exc:  # noqa: BLE001
        result = {"content": None, "tool_calls": [], "provider": p, "error": str(exc)}
    result["provider"] = p
    if result.get("content") or result.get("tool_calls"):
        _CACHE[key] = (time.time(), result)
    return result


def _chat_openai(messages, tools, json_mode, max_tokens, temperature, timeout):
    base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    headers = {"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', '')}"}
    body = {
        "model": _openai_model(),
        "messages": _to_openai_messages(messages),
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    def call(payload):
        response = httpx.post(
            f"{base}/chat/completions", headers=headers, json=payload, timeout=timeout
        )
        response.raise_for_status()
        return response.json()

    try:
        data = call(body)
    except httpx.HTTPStatusError as exc:
        if json_mode and exc.response.status_code == 400:
            body.pop("response_format", None)
            data = call(body)
        else:
            raise
    message = data["choices"][0]["message"]
    tool_calls = []
    for call_item in message.get("tool_calls") or []:
        function = call_item.get("function", {})
        tool_calls.append(
            {
                "id": call_item.get("id"),
                "name": function.get("name"),
                "args": _normalize_arguments(function.get("arguments")),
            }
        )
    return {"content": message.get("content"), "tool_calls": tool_calls}


def _to_openai_messages(messages):
    out = []
    for message in messages:
        role = message["role"]
        if role == "assistant" and message.get("tool_calls"):
            out.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps(call.get("args") or {}),
                            },
                        }
                        for call in message["tool_calls"]
                    ],
                }
            )
        elif role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": message.get("tool_call_id"),
                    "content": str(message.get("content") or ""),
                }
            )
        else:
            out.append({"role": role, "content": message.get("content") or ""})
    return out


def _chat_anthropic(messages, tools, max_tokens, temperature, timeout):
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    converted = []
    for message in messages:
        role = message["role"]
        if role == "system":
            continue
        if role == "assistant":
            blocks = []
            if message.get("content"):
                blocks.append({"type": "text", "text": message["content"]})
            for call in message.get("tool_calls") or []:
                blocks.append(
                    {"type": "tool_use", "id": call["id"], "name": call["name"], "input": call.get("args") or {}}
                )
            converted.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "..."}]})
        elif role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": message.get("tool_call_id"),
                "content": str(message.get("content") or ""),
            }
            if (
                converted
                and converted[-1]["role"] == "user"
                and isinstance(converted[-1]["content"], list)
                and converted[-1]["content"]
                and converted[-1]["content"][0].get("type") == "tool_result"
            ):
                converted[-1]["content"].append(block)
            else:
                converted.append({"role": "user", "content": [block]})
        else:
            converted.append({"role": "user", "content": message.get("content") or ""})

    body = {
        "model": _anthropic_model(),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": converted,
    }
    if system_parts:
        body["system"] = "\n\n".join(system_parts)
    if tools:
        body["tools"] = [
            {
                "name": tool["function"]["name"],
                "description": tool["function"].get("description", ""),
                "input_schema": tool["function"].get("parameters", {"type": "object"}),
            }
            for tool in tools
        ]
    response = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": os.getenv("ANTHROPIC_API_KEY", ""),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=body,
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    content = None
    tool_calls = []
    for block in data.get("content", []):
        if block.get("type") == "text":
            content = (content or "") + block.get("text", "")
        elif block.get("type") == "tool_use":
            tool_calls.append(
                {"id": block.get("id"), "name": block.get("name"), "args": block.get("input") or {}}
            )
    return {"content": content, "tool_calls": tool_calls}


def extract_json(text):
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except Exception:
            return None


_REPORT_WORDS = (
    "report",
    "scan",
    "x-ray",
    "xray",
    "blood",
    "result",
    "collect",
    "pick up",
    "pickup",
    "sample",
)
_FIX_WORDS = (
    "spell",
    "spelling",
    "mistake",
    "wrong",
    "correct",
    "correction",
    "error",
    "dob",
    "address",
    "print",
    "name change",
)


def classify_intake_fallback(reason):
    text = (reason or "").lower()
    if any(word in text for word in _FIX_WORDS):
        return {
            "patient_type": "return",
            "queue": "doc_fix",
            "urgency": "medium",
            "summary": "Document correction",
            "confidence": 0.45,
        }
    if any(word in text for word in _REPORT_WORDS):
        return {
            "patient_type": "report",
            "queue": "report",
            "urgency": "low",
            "summary": "Collecting a report",
            "confidence": 0.45,
        }
    return {
        "patient_type": "walkin",
        "queue": "triage",
        "urgency": "medium",
        "summary": "Walk-in consultation",
        "confidence": 0.4,
    }


def classify_intake(reason, name=""):
    fallback = classify_intake_fallback(reason)
    if provider() == "fallback":
        return fallback
    prompt = (
        "You are the intake assistant at a neighborhood clinic. A patient has arrived and the front desk "
        "typed their reason in free text. Classify it for routing.\n"
        'Return JSON only: {"patient_type": "appointment|walkin|return|report", '
        '"queue": "triage|reception|report|doc_fix|clinician", "urgency": "low|medium|high", '
        '"summary": "max 6 words", "confidence": 0.0-1.0}\n'
        "Routes: triage = new consultation or symptoms; report = collecting a test result; "
        "doc_fix = correcting a document/name/spelling; reception = general query or payment; "
        "clinician = explicitly asked to see the doctor.\n"
        f'Patient name: {name or "unknown"}\nReason: "{reason}"'
    )
    result = chat([{"role": "user", "content": prompt}], json_mode=True, max_tokens=200)
    parsed = extract_json(result.get("content")) or {}
    merged = dict(fallback)
    if parsed.get("queue") in {"triage", "reception", "report", "doc_fix", "clinician"}:
        merged["queue"] = parsed["queue"]
    if parsed.get("patient_type") in {"appointment", "walkin", "return", "report"}:
        merged["patient_type"] = parsed["patient_type"]
    if parsed.get("urgency") in {"low", "medium", "high"}:
        merged["urgency"] = parsed["urgency"]
    if parsed.get("summary"):
        merged["summary"] = str(parsed["summary"])[:80]
    try:
        merged["confidence"] = float(parsed.get("confidence", merged["confidence"]))
    except (TypeError, ValueError):
        pass
    merged["mode"] = "llm"
    return merged


_EN_TEMPLATES = {
    "WAITING_RECEPTION": "You are waiting for the front desk to check your documents or report.",
    "WAITING_TRIAGE": "You are waiting for the nurse to call you for vitals.",
    "IN_TRIAGE": "The nurse is checking your vitals now.",
    "WAITING_CONSULT": "You are waiting for the doctor to call you in.",
    "IN_CONSULT": "You are with the doctor now.",
    "WAITING_LAB_COLLECTION": "You are waiting for the lab to collect your sample.",
    "LAB_PROCESSING": "Your sample is being processed by the lab.",
    "WAITING_REPORT_REVIEW": "Your report is ready and waiting for the doctor to review it.",
    "WAITING_DOC_FIX": "The front desk is correcting your document.",
    "READY_FOR_PICKUP": "Your report is ready. Please collect it at the front desk.",
    "NOTIFIED": "You have been notified. Please collect your report at the front desk.",
    "BLOCKED_NO_OWNER": "Your wait lost its owner — staff have been alerted automatically.",
    "DONE": "You are all done. Thank you for visiting.",
    "ARRIVED": "You have just arrived. The front desk will guide you next.",
}

_HI_TEMPLATES = {
    "WAITING_RECEPTION": "आप फ्रंट डेस्क पर अपने दस्तावेज़ या रिपोर्ट की जाँच के लिए प्रतीक्षा कर रहे हैं।",
    "WAITING_TRIAGE": "नर्स आपको वाइटल्स के लिए बुलाने वाली है।",
    "IN_TRIAGE": "नर्स अभी आपके वाइटल्स देख रही है।",
    "WAITING_CONSULT": "आप डॉक्टर के बुलाने की प्रतीक्षा कर रहे हैं।",
    "IN_CONSULT": "आप अभी डॉक्टर के पास हैं।",
    "WAITING_LAB_COLLECTION": "लैब आपका सैंपल लेने वाली है।",
    "LAB_PROCESSING": "आपका सैंपल लैब में जाँच के लिए भेजा गया है।",
    "WAITING_REPORT_REVIEW": "आपकी रिपोर्ट तैयार है और डॉक्टर के रिव्यू की प्रतीक्षा में है।",
    "WAITING_DOC_FIX": "फ्रंट डेस्क आपका दस्तावेज़ ठीक कर रहा है।",
    "READY_FOR_PICKUP": "आपकी रिपोर्ट तैयार है। कृपया फ्रंट डेस्क से ले जाएँ।",
    "NOTIFIED": "आपको सूचना भेज दी गई है। कृपया अपनी रिपोर्ट फ्रंट डेस्क से लें।",
    "BLOCKED_NO_OWNER": "आपकी प्रतीक्षा का ज़िम्मा किसी के पास नहीं था — स्टाफ को तुरंत सूचित कर दिया गया है।",
    "DONE": "सब पूरा हो गया। धन्यवाद।",
    "ARRIVED": "आप अभी पहुँचे हैं। फ्रंट डेस्क आपको आगे बढ़ाएगा।",
}


def explain_wait_fallback(facts, language="en"):
    state = facts.get("state", "ARRIVED")
    templates = _HI_TEMPLATES if language == "hi" else _EN_TEMPLATES
    message = templates.get(state, _EN_TEMPLATES["WAITING_RECEPTION"])
    eta = facts.get("eta_minutes")
    if facts.get("blocked"):
        message = templates["BLOCKED_NO_OWNER"]
        eta = max(2, int(eta or 5))
    if state == "DONE" or state == "NOTIFIED":
        eta_text = ""
    elif eta is not None:
        eta_text = f" About {int(eta)} more minutes." if language != "hi" else f" लगभग {int(eta)} मिनट और।"
    else:
        eta_text = ""
    action = None
    if state == "WAITING_DOC_FIX":
        action = "Please keep your ID ready." if language != "hi" else "कृपया अपना आईडी तैयार रखें।"
    elif state == "READY_FOR_PICKUP" or state == "NOTIFIED":
        action = "Bring your token or ID." if language != "hi" else "कृपया टोकन या आईडी साथ लाएँ।"
    return {"message": f"{message}{eta_text}", "action_needed": action, "mode": "fallback"}


def explain_wait(facts, language="en"):
    fallback = explain_wait_fallback(facts, language)
    if provider() == "fallback":
        return fallback
    prompt = (
        "You explain clinic waits to patients in plain, warm language. Use ONLY the facts provided. "
        "Never invent times or reasons.\n"
        'Return JSON only: {"message": "1-2 short sentences", "action_needed": "one short instruction or null"}\n'
        f"Language: {'Hindi (Devanagari)' if language == 'hi' else 'English'}\n"
        f"Facts: {json.dumps(facts, default=str)}"
    )
    result = chat([{"role": "user", "content": prompt}], json_mode=True, max_tokens=250)
    parsed = extract_json(result.get("content")) or {}
    if not parsed.get("message"):
        return fallback
    return {
        "message": str(parsed["message"]),
        "action_needed": parsed.get("action_needed"),
        "mode": "llm",
    }


def draft_notification(patient, channel="sms"):
    fallback = (
        f"Hello {patient.get('name', '')}, your report is ready at the clinic front desk. "
        f"Please collect it at your convenience. — Neighborhood Clinic"
    )
    if provider() == "fallback":
        return {"message": fallback, "mode": "fallback"}
    prompt = (
        "Write a short, friendly clinic SMS telling a patient their report is ready for pickup. "
        "Use only the facts provided. Return JSON only: {\"message\": \"...\"}.\n"
        f"Facts: {json.dumps(patient, default=str)}"
    )
    result = chat([{"role": "user", "content": prompt}], json_mode=True, max_tokens=150)
    parsed = extract_json(result.get("content")) or {}
    if not parsed.get("message"):
        return {"message": fallback, "mode": "fallback"}
    return {"message": str(parsed["message"]), "mode": "llm"}


def narrate(question, trace, facts):
    if provider() == "fallback":
        return {"answer": _fallback_narrative(question, trace, facts), "mode": "fallback"}
    prompt = (
        "You are the operations assistant for a neighborhood clinic called Handoff. Answer the manager's "
        "question using ONLY the tool results and facts provided. Be concise: 3-6 sentences. Name the "
        "biggest bottleneck, whose work is affected, and one recommended action.\n"
        f"Question: {question}\n"
        f"Tool trace: {json.dumps(trace, default=str)[:6000]}\n"
        f"Computed facts: {json.dumps(facts, default=str)[:3000]}"
    )
    result = chat([{"role": "user", "content": prompt}], max_tokens=400)
    if not result.get("content"):
        return {"answer": _fallback_narrative(question, trace, facts), "mode": "fallback"}
    return {"answer": result["content"].strip(), "mode": "llm"}


def _fallback_narrative(question, trace, facts):
    bottlenecks = facts.get("bottlenecks") or []
    kpis = facts.get("kpis") or {}
    lines = []
    if bottlenecks:
        top = bottlenecks[0]
        lines.append(
            f"The biggest time sink is {top['state_label'].lower()}: {top['total_minutes']:.0f} min in"
            f" the window, p90 {top['p90_minutes']} min across {top['samples']} samples."
        )
    if kpis:
        lines.append(
            f"Right now {kpis.get('active_patients', 0)} patients are active, median wait is"
            f" {kpis.get('median_wait_minutes')} min, and {kpis.get('waits_with_owner_pct')}% of waits"
            f" have a clear owner."
        )
        lines.append(
            f"Dropped handoffs: {kpis.get('dropped_handoffs', 0)}; orphan minutes:"
            f" {kpis.get('orphan_minutes', 0)}; interruptions today: {kpis.get('interruptions_today', 0)}."
        )
    missed = facts.get("missed_handoffs") or []
    if missed:
        first = missed[0]
        detail = (
            f"{first.get('item')} handoff to {first.get('to_role')} never accepted"
            if first.get("item")
            else first.get("message") or "handoff overdue"
        )
        lines.append(
            f"Priority: {first.get('patient_name')} ({first.get('patient_id')}) — {detail}."
            " Review that handoff first."
        )
    if not lines:
        lines.append("No bottlenecks detected in the selected window.")
    return " ".join(lines)
