"""
Critique Agent Phase A — Step 2
Verifies Orchestrator's channel selection with evidence-based reasoning.
"""
from app.agents.state import RenewalState
from app.core.gemini_client import call_llm_json
from app.rag.chroma_store import hybrid_search_and_rerank
from app.api.prompts import get_active_prompt
from app.core.config import get_settings
import json
import aiosqlite

settings = get_settings()

CRITIQUE_A_SYSTEM_PROMPT = """
You are the RenewAI Critique Agent, Phase A — the evidence verifier.
Your job: Verify the Orchestrator's channel selection against hard evidence.

Check ALL of:
1. Is there interaction data supporting this channel choice?
2. Has this channel been EXHAUSTED (3+ attempts, no result)?
3. Does the customer segment/preference align with this channel?
4. CHANNEL FEASIBILITY: If WhatsApp or Voice is selected but Phone Availability is "No", you MUST OVERRIDE to Email and set the reason to "Missing Phone - Use Other Channels".
5. Is escalation threshold already met (distress or objection_count >= 3)?

Respond ONLY with valid JSON:
{
  "verdict": "APPROVED|OVERRIDE",
  "confidence": 0.0-1.0,
  "evidence": "specific evidence from data",
  "alternative_channel": "Email|WhatsApp|Voice|null",
  "override_reason": "reason if OVERRIDE else null"
}
"""


async def critique_a_node(state: RenewalState) -> dict:
    """Step 2: Verify channel selection with evidence."""
    
    # Fetch prompt from DB
    prompt_data = await get_active_prompt("CRITIQUE_A")
    system_prompt = prompt_data["prompt_text"] or CRITIQUE_A_SYSTEM_PROMPT
    version = prompt_data["version"]
    
    # Retrieve regulatory guidelines for reference
    reg_results = hybrid_search_and_rerank(
        "regulatory_guidelines",
        query=f"channel communication policy IRDAI {state.get('selected_channel', '')}",
        n_results=3,
        rerank_top_k=2
    )
    reg_context = "\n".join([r["document"] for r in reg_results]) if reg_results else ""

    # Count channel attempts
    history = state.get("interaction_history", [])
    channel = state.get("selected_channel", "")
    channel_attempts = sum(1 for h in history if h.get("channel") == channel and h.get("direction") == "OUTBOUND")

    user_prompt = f"""
Orchestrator Decision:
- Selected Channel: {channel}
- Justification: {state.get('channel_justification', '')}

Customer:
- Preferred Channel: {state['preferred_channel']}
- Phone Availability: {"Yes" if state.get("customer_phone") else "No"}
- Segment: {state['segment']}
- Distress Flag: {state.get('distress_flag', False)}
- Objection Count: {state.get('objection_count', 0)}

Channel Attempts for "{channel}": {channel_attempts}
Total Interaction History Count: {len(history)}

Recent History:
{json.dumps(history[-5:], indent=2)}

Regulatory Context:
{reg_context}
"""

    result = await call_llm_json(system_prompt, user_prompt)
    verdict = result.get("verdict", "APPROVED")

    active_versions = state.get("active_versions", {})
    if version:
        active_versions["CRITIQUE_A"] = version

    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            "INSERT INTO audit_logs (policy_id, action_type, action_reason, triggered_by, prompt_version) VALUES (?, ?, ?, ?, ?)",
            (state["policy_id"], f"CRITIQUE_A_{verdict}", f"Verdict: {verdict} | Confidence: {result.get('confidence')} | Evidence: {result.get('evidence')}", "Critique A Agent", version)
        )
        await db.commit()

    updates = {
        "critique_a_result": verdict,
        "rag_regulations": reg_context,
        "active_versions": active_versions,
        "audit_trail": [f"[CRITIQUE_A] Verdict: {verdict} | Confidence: {result.get('confidence')} | Evidence: {result.get('evidence')}"]
    }

    if verdict == "OVERRIDE":
        # Provide alternative channel back to orchestrator
        alt_channel = result.get("alternative_channel")
        if alt_channel:
            updates["selected_channel"] = alt_channel
            updates["channel_justification"] = result.get("override_reason", "Critique A override")
        updates["current_node"] = "PLANNER"  # Proceed with override channel
    else:
        updates["current_node"] = "PLANNER"

    return updates
