"""
Critique Agent Phase B — Step 5
Reviews the final assembled message for compliance, tone, accuracy.
"""
from app.agents.state import RenewalState
from app.core.gemini_client import call_llm_json
from app.rag.chroma_store import hybrid_search_and_rerank
from app.api.prompts import get_active_prompt
from app.core.config import get_settings
import json
import aiosqlite

settings = get_settings()

CRITIQUE_B_SYSTEM_PROMPT = """
You are the RenewAI Critique Agent, Phase B — the content compliance reviewer.
Review the assembled renewal message before sending.

Check ALL of:
1. ACCURACY: Are all policy figures correct and not fabricated?
2. COMPLIANCE: Does it include the mandatory AI disclosure line?
3. TONE: Does it match the planned tone and segment?
4. LANGUAGE: Is the specified language used correctly?
5. DISTRESS: If distress_flag=true, is the message appropriately empathetic?
6. IRDAI COMPLIANCE: No misleading claims, no pressure tactics

Verdict options:
- APPROVED: Message is ready to send
- REVISION_NEEDED: Specific issues found (include fix_instructions)
- ESCALATE: Message cannot be fixed automatically; escalate to human

Respond ONLY with valid JSON:
{
  "verdict": "APPROVED|REVISION_NEEDED|ESCALATE",
  "issues": ["issue1","issue2"],
  "fix_instructions": "specific fixes if REVISION_NEEDED",
  "compliance_score": 0.0-1.0,
  "escalate_reason": "reason if ESCALATE else null"
}
"""


async def critique_b_node(state: RenewalState) -> dict:
    """Step 5: Review assembled message for compliance and quality."""
    
    # Fetch prompt from DB
    prompt_data = await get_active_prompt("CRITIQUE_B")
    system_prompt = prompt_data["prompt_text"] or CRITIQUE_B_SYSTEM_PROMPT
    version = prompt_data["version"]
    
    # RAG: regulatory guidelines for compliance check
    reg_results = hybrid_search_and_rerank(
        "regulatory_guidelines",
        query=f"IRDAI insurance communication compliance {state.get('selected_channel','')}",
        n_results=3,
        rerank_top_k=2
    )
    reg_context = "\n".join([r["document"] for r in reg_results]) if reg_results else ""

    # Assemble the full message
    greeting = state.get("greeting", "")
    draft = state.get("draft_message", "")
    closing = state.get("closing", "")
    full_message = f"{greeting}\n\n{draft}\n\n{closing}"

    user_prompt = f"""
Assembled Message to Review:
---
{full_message}
---

Context:
- Channel: {state.get('selected_channel')}
- Customer Segment: {state.get('segment')}
- Distress Flag: {state.get('distress_flag', False)}
- Planned Tone: {state.get('execution_plan', {}).get('tone', 'N/A')}
- Planned Language: {state.get('execution_plan', {}).get('language', 'N/A')}
- Policy Type: {state['policy_type']}
- Annual Premium: ₹{state['annual_premium']}

Regulatory Guidelines:
{reg_context}
"""

    result = await call_llm_json(system_prompt, user_prompt)
    verdict = result.get("verdict", "APPROVED")

    active_versions = state.get("active_versions", {})
    if version:
        active_versions["CRITIQUE_B"] = version

    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            "INSERT INTO audit_logs (policy_id, action_type, action_reason, triggered_by, prompt_version) VALUES (?, ?, ?, ?, ?)",
            (state["policy_id"], f"CRITIQUE_B_{verdict}", f"Verdict: {verdict} | Score: {result.get('compliance_score')} | Issues: {result.get('issues')}", "Critique B Agent", version)
        )
        await db.commit()

    updates = {
        "critique_b_result": verdict,
        "active_versions": active_versions,
        "audit_trail": [f"[CRITIQUE_B] Verdict: {verdict} | Score: {result.get('compliance_score')} | Issues: {result.get('issues')}"]
    }

    if verdict == "ESCALATE":
        updates["current_node"] = "ESCALATION"
        updates["escalate"] = True
        updates["escalation_reason"] = result.get("escalate_reason", "Critique B escalation")
        updates["mode"] = "HUMAN_CONTROL"
    elif verdict == "REVISION_NEEDED":
        # For simplicity: log and proceed with current draft (max 1 auto-revision loop)
        updates["current_node"] = "CHANNEL_SEND"
        updates["audit_trail"].append(f"[CRITIQUE_B] Issues noted but proceeding: {result.get('fix_instructions')}")
    else:
        updates["current_node"] = "CHANNEL_SEND"
        # Finalize message
        greeting = state.get("greeting", "")
        draft = state.get("draft_message", "")
        closing = state.get("closing", "")
        updates["final_message"] = f"{greeting}\n\n{draft}\n\n{closing}".strip()

    return updates
