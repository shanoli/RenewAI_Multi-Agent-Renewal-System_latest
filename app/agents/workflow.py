"""
LangGraph Workflow Definition
Connects all agents in the correct sequence.
Includes timing + token telemetry for each node.
"""
import time
import asyncio
import aiosqlite
from langgraph.graph import StateGraph, END
from app.agents.state import RenewalState
from app.agents.orchestrator import orchestrator_node
from app.agents.critique_a import critique_a_node
from app.agents.planner import planner_node
from app.agents.greeting_closing import greeting_closing_node
from app.agents.draft_agent import draft_agent_node
from app.agents.critique_b import critique_b_node
from app.agents.escalation import escalation_node
from app.agents.channels.email_agent import email_send_node
from app.agents.channels.whatsapp_agent import whatsapp_send_node
from app.agents.channels.voice_agent import voice_send_node
from app.core.config import get_settings

settings = get_settings()


def timed_node(agent_name: str, fn):
    """Wraps an agent node to record execution time and token usage."""
    async def wrapper(state: RenewalState) -> dict:
        t_start = time.monotonic()
        status = "SUCCESS"
        tokens_in = 0
        tokens_out = 0
        try:
            result = await fn(state)
            # Extract token usage if agents returned it
            tokens_in = result.pop("_tokens_in", 0) or 0
            tokens_out = result.pop("_tokens_out", 0) or 0
            return result
        except Exception as e:
            status = f"ERROR: {str(e)[:100]}"
            raise
        finally:
            duration_ms = (time.monotonic() - t_start) * 1000
            policy_id = state.get("policy_id", "UNKNOWN")
            try:
                async with aiosqlite.connect(settings.sqlite_db_path) as db:
                    await db.execute(
                        """INSERT INTO agent_telemetry 
                           (policy_id, agent_name, execution_time_ms, tokens_input, tokens_output, status) 
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (policy_id, agent_name, round(duration_ms, 2), tokens_in, tokens_out, status)
                    )
                    await db.commit()
            except Exception:
                pass  # Never let telemetry crash the workflow

    return wrapper


def route_after_orchestrator(state: RenewalState) -> str:
    node = state.get("current_node", "")
    if node == "ESCALATION":
        return "escalation"
    if node == "COMPLETED":
        return END
    return "critique_a"


def route_after_critique_b(state: RenewalState) -> str:
    node = state.get("current_node", "")
    if node == "ESCALATION":
        return "escalation"
    return "channel_router"


def route_channel(state: RenewalState) -> str:
    channel = state.get("selected_channel", "Email")
    mapping = {
        "Email": "email_send",
        "WhatsApp": "whatsapp_send",
        "Voice": "voice_send"
    }
    return mapping.get(channel, "email_send")


async def parallel_draft_and_greeting(state: RenewalState) -> dict:
    """Run Greeting/Closing and Draft Agent in parallel."""
    greeting_task = asyncio.create_task(greeting_closing_node(state))
    draft_task = asyncio.create_task(draft_agent_node(state))

    greeting_result, draft_result = await asyncio.gather(greeting_task, draft_task)

    # Merge results
    merged = {}
    merged.update(greeting_result)
    merged.update(draft_result)

    # Merge audit trails
    merged["audit_trail"] = (
        greeting_result.get("audit_trail", []) +
        draft_result.get("audit_trail", [])
    )
    merged["current_node"] = "CRITIQUE_B"
    return merged


def build_workflow() -> StateGraph:
    graph = StateGraph(RenewalState)

    # Add nodes — all wrapped with timing telemetry
    graph.add_node("orchestrator",      timed_node("ORCHESTRATOR",      orchestrator_node))
    graph.add_node("critique_a",        timed_node("CRITIQUE_A",        critique_a_node))
    graph.add_node("planner",           timed_node("PLANNER",           planner_node))
    graph.add_node("draft_and_greeting",timed_node("DRAFT_AND_GREETING",parallel_draft_and_greeting))
    graph.add_node("critique_b",        timed_node("CRITIQUE_B",        critique_b_node))
    graph.add_node("escalation",        timed_node("ESCALATION",        escalation_node))
    graph.add_node("email_send",        timed_node("EMAIL_AGENT",       email_send_node))
    graph.add_node("whatsapp_send",     timed_node("WHATSAPP_AGENT",    whatsapp_send_node))
    graph.add_node("voice_send",        timed_node("VOICE_AGENT",       voice_send_node))
    graph.add_node("channel_router", lambda s: s)  # pass-through router

    # Entry point
    graph.set_entry_point("orchestrator")

    # Edges
    graph.add_conditional_edges("orchestrator", route_after_orchestrator, {
        "critique_a": "critique_a",
        "escalation": "escalation",
        END: END
    })
    graph.add_edge("critique_a", "planner")
    graph.add_edge("planner", "draft_and_greeting")
    graph.add_edge("draft_and_greeting", "critique_b")
    graph.add_conditional_edges("critique_b", route_after_critique_b, {
        "escalation": "escalation",
        "channel_router": "channel_router"
    })
    graph.add_conditional_edges("channel_router", route_channel, {
        "email_send": "email_send",
        "whatsapp_send": "whatsapp_send",
        "voice_send": "voice_send"
    })
    graph.add_edge("email_send", END)
    graph.add_edge("whatsapp_send", END)
    graph.add_edge("voice_send", END)
    graph.add_edge("escalation", END)

    return graph.compile()


# Singleton workflow instance
_workflow = None


def get_workflow():
    global _workflow
    if _workflow is None:
        _workflow = build_workflow()
    return _workflow
