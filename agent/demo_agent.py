from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

if "--selftest" in sys.argv:
    import tempfile

    os.environ["HANDOFF_DB"] = os.path.join(tempfile.mkdtemp(prefix="handoff-selftest-"), "selftest.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from handoff import ai  # noqa: E402

SERVER = ROOT / "mcp_server" / "server.py"


def _extract(result):
    parts = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            parts.append(text)
    raw = "\n".join(parts)
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return {"raw": raw}


def _summarize(result):
    if not isinstance(result, dict):
        return str(result)[:120]
    if result.get("error"):
        return f"error: {result['error']}"
    for key in ("count", "patient", "bottlenecks", "missed_handoffs", "answer"):
        if key in result:
            value = result[key]
            if isinstance(value, list):
                return f"{len(value)} item(s)"
            if isinstance(value, dict) and "id" in value:
                return f"{value.get('id')} ({value.get('state_label')})"
            return str(value)[:160]
    return "ok"


def _to_openai_tools(mcp_tools):
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.inputSchema or {"type": "object"},
            },
        }
        for tool in mcp_tools
    ]


async def run(question, selftest=False):
    server_env = {"HANDOFF_DB": os.environ.get("HANDOFF_DB", str(ROOT / "handoff.db"))}
    params = StdioServerParameters(
        command=sys.executable, args=[str(SERVER)], cwd=str(ROOT), env=server_env
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            tools = listed.tools
            print(f"MCP server connected — {len(tools)} tools: {', '.join(t.name for t in tools)}")
            if selftest:
                from handoff import db as db_module

                db_module.init_db()

                async def call(name, args):
                    result = _extract(await session.call_tool(name, arguments=args))
                    print(f"  {name} -> {_summarize(result)}")
                    if not result.get("ok", True):
                        raise RuntimeError(f"selftest failed at {name}: {result}")
                    return result

                registered = await call(
                    "register_patient", {"name": "Selftest Sam", "reason": "fever and cough"}
                )
                pid = registered["patient_id"]
                for event, actor in (
                    ("CALLED", "nurse"),
                    ("COMPLETED", "nurse"),
                    ("CALLED", "clinician"),
                    ("LAB_ORDERED", "clinician"),
                    ("STARTED", "lab"),
                    ("LAB_RESULT_READY", "lab"),
                ):
                    await call("advance", {"patient_id": pid, "event_type": event, "actor": actor})
                await call("get_queue", {"status": "open"})
                fetched = await call("get_patient", {"patient_id": pid})
                await call("next_for", {"role": "clinician"})
                await call("list_bottlenecks", {"window_minutes": 240})
                await call("list_missed_handoffs", {})
                await call("staff_interruptions", {"window_minutes": 1440})
                await call("explain_wait", {"patient_id": pid})
                open_handoffs = [
                    h
                    for h in fetched["patient"]["handoffs"]
                    if h["status"] in ("open", "dropped")
                ]
                await call(
                    "accept_handoff", {"handoff_id": open_handoffs[-1]["id"], "by": "clinician"}
                )
                await call("notify_patient", {"patient_id": pid, "channel": "sms"})
                print("selftest: all 11 tools OK")
                return

            trace = []
            if ai.provider() == "fallback":
                for name, args in (
                    ("list_bottlenecks", {"window_minutes": 240}),
                    ("list_missed_handoffs", {}),
                    ("staff_interruptions", {"window_minutes": 1440}),
                ):
                    result = _extract(await session.call_tool(name, arguments=args))
                    trace.append({"tool": name, "args": args, "result": result})
                    print(f"[tool] {name}({json.dumps(args)}) -> {_summarize(result)}")
                facts = {
                    "bottlenecks": trace[0]["result"].get("bottlenecks"),
                    "missed_handoffs": trace[1]["result"].get("missed_handoffs"),
                }
                from handoff import metrics

                facts["kpis"] = metrics.kpis()
                narrative = ai.narrate(question, trace, facts)
                print(f"\nANSWER ({narrative['mode']} mode)\n{narrative['answer']}")
                return

            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are the operations assistant for a neighborhood clinic called Handoff. "
                        "Use the MCP tools to ground every answer. Be concise and recommend one action."
                    ),
                },
                {"role": "user", "content": question},
            ]
            schemas = _to_openai_tools(tools)
            for _ in range(6):
                response = ai.chat(messages, tools=schemas)
                if response.get("error"):
                    print(f"LLM error: {response['error']}")
                    break
                calls = response.get("tool_calls") or []
                if not calls:
                    print(f"\nANSWER ({response.get('provider')} mode)\n{response.get('content')}")
                    return
                messages.append(
                    {
                        "role": "assistant",
                        "content": response.get("content"),
                        "tool_calls": calls,
                    }
                )
                for call in calls:
                    result = _extract(
                        await session.call_tool(call["name"], arguments=call.get("args") or {})
                    )
                    print(f"[tool] {call['name']}({json.dumps(call.get('args') or {})}) -> {_summarize(result)}")
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "name": call["name"],
                            "content": json.dumps(result, default=str)[:6000],
                        }
                    )
            print("No final answer produced.")


def main():
    args = [arg for arg in sys.argv[1:] if arg != "--selftest"]
    selftest = "--selftest" in sys.argv[1:]
    question = " ".join(args) or "Where did waiting time go today, and whose work is being interrupted?"
    asyncio.run(run(question, selftest=selftest))


if __name__ == "__main__":
    main()
