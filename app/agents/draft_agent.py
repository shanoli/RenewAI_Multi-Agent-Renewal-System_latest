"""
Draft Agent — Step 4b
Generates channel-specific message body.
Three channel-specific system prompts, one agent.
Runs in parallel with Greeting/Closing agent.
"""
from app.agents.state import RenewalState
from app.core.gemini_client import call_llm
from app.api.prompts import get_active_prompt
from app.core.config import get_settings
import json
import aiosqlite

settings = get_settings()

# ── Email System Prompt ──────────────────────────────────────────────────────
EMAIL_SYSTEM_PROMPT = """
You are the RenewAI Email Draft Agent for Suraksha Life Insurance.
Generate a professional renewal email BODY in the specified language (English, Hindi, or Bengali).
Use ONLY the policy facts provided — never invent figures.

Requirements:
- Languages Supported: English, Hindi, Bengali.
- For Hindi/Bengali: Use formal and respectful vocabulary suitable for financial/insurance contexts.
- Include: due date, premium amount, top 3 benefits, CTA button placeholder [CTA_BUTTON]
- Include payment link placeholder [PAYMENT_LINK]
- Leave [GREETING] at top and [CLOSING] at bottom
- Use the specified language and tone
- For ULIP: include fund value and current NAV if available
- Keep under 250 words

Output ONLY the email body text in the requested language.
"""

# ── WhatsApp System Prompt ───────────────────────────────────────────────────
WHATSAPP_SYSTEM_PROMPT = """
You are the RenewAI WhatsApp Draft Agent for Suraksha Life Insurance.
Generate a WhatsApp renewal message BODY in the specified language (English, Hindi, or Bengali).

Requirements:
- Languages Supported: English, Hindi, Bengali.
- For Hindi/Bengali: Use natural, conversational phrasing with appropriate honorifics.
- MAXIMUM 200 characters for main message
- Use appropriate emojis (not excessive)
- Apply objection playbook if customer has prior objections
- If distress keywords detected in history, set distress_flag and be empathetic
- Leave [GREETING] at top and [CLOSING] at bottom
- CTA: simple reply instruction or payment link placeholder [PAYMENT_LINK]

Output ONLY the WhatsApp body text in the requested language.
"""

# ── Voice System Prompt ──────────────────────────────────────────────────────
VOICE_SYSTEM_PROMPT = """
You are the RenewAI Voice Script Draft Agent for Suraksha Life Insurance.
Generate a voice call script BODY in the specified language (English, Hindi, or Bengali).

Requirements:
- Languages Supported: English, Hindi, Bengali.
- Include [PAUSE] markers for natural speech breaks
- Structure: premium reminder → benefit highlight → objection handle → payment CTA
- Mark [ESCALATE] clearly if distress detected
- Leave [GREETING] at top and [CLOSING] at bottom
- Keep under 150 words (about 60 seconds)
- Use natural spoken language (e.g., spoken Hindi/Bengali, not overly formal literary script)

Output ONLY the voice script body in the requested language.
"""

DISTRESS_KEYWORDS = [
    "lost job", "husband passed", "wife passed", "death", "funeral",
    "can't pay", "cannot pay", "no money", "bankrupt", "hospital",
    "accident", "hardship", "financial crisis", "naukri gayi",
    "पैसे नहीं", "नौकरी गई", "मृत्यु", "बीमार",
    "টাকা নেই", "চাকরি চলে গেছে", "অসুস্থ", "মৃত্যু"
]


def detect_distress(history: list) -> bool:
    for interaction in history:
        content = interaction.get("content", "").lower()
        if any(kw.lower() in content for kw in DISTRESS_KEYWORDS):
            return True
    return False


async def draft_agent_node(state: RenewalState) -> dict:
    """Step 4b: Generate channel-specific message body."""
    
    channel = state.get("selected_channel", "Email")
    plan = state.get("execution_plan", {})
    language = plan.get("language", state.get("preferred_language", "English"))
    tone = plan.get("tone", "friendly")

    # Check distress in history
    distress_detected = detect_distress(state.get("interaction_history", []))
    if distress_detected and not state.get("distress_flag"):
        distress_flag = True
    else:
        distress_flag = state.get("distress_flag", False)

    base_context = f"""
Language: {language}
Tone: {tone}
Customer Name: {state['customer_name']}
Policy Type: {state['policy_type']}
Premium Due: ₹{state['annual_premium']}
Due Date: {state['premium_due_date']}
Fund Value: {state.get('fund_value', 'N/A')}
Key Facts: {', '.join(plan.get('key_facts', []))}
CTA Type: {plan.get('cta_type', 'payment_link')}
Objection Responses: {json.dumps(plan.get('objection_responses', []))}

Recent Interaction History:
{json.dumps(state.get('interaction_history', [])[-3:], indent=2)}

Retrieved Policy Docs:
{state.get('rag_policy_docs', '')[:500]}

Objection Playbook:
{state.get('rag_objections', '')[:300]}
"""

    active_versions = state.get("active_versions", {})
    
    if channel == "Email":
        prompt_data = await get_active_prompt("EMAIL_DRAFT")
        system_prompt = prompt_data["prompt_text"] or EMAIL_SYSTEM_PROMPT
        if prompt_data["version"]: active_versions["EMAIL_DRAFT"] = prompt_data["version"]
    elif channel == "WhatsApp":
        prompt_data = await get_active_prompt("WHATSAPP_DRAFT")
        system_prompt = prompt_data["prompt_text"] or WHATSAPP_SYSTEM_PROMPT
        if prompt_data["version"]: active_versions["WHATSAPP_DRAFT"] = prompt_data["version"]
    elif channel == "Voice":
        prompt_data = await get_active_prompt("VOICE_DRAFT")
        system_prompt = prompt_data["prompt_text"] or VOICE_SYSTEM_PROMPT
        if prompt_data["version"]: active_versions["VOICE_DRAFT"] = prompt_data["version"]
    else:
        system_prompt = EMAIL_SYSTEM_PROMPT

    draft = await call_llm(system_prompt, base_context, temperature=0.4)

    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            "INSERT INTO audit_logs (policy_id, action_type, action_reason, triggered_by, prompt_version) VALUES (?, ?, ?, ?, ?)",
            (state["policy_id"], "DRAFT_GENERATED", f"Draft generated for {channel} | Language: {language} | Tone: {tone}", "Draft Agent", active_versions.get(f"{channel.upper()}_DRAFT"))
        )
        await db.commit()

    updates = {
        "current_node": "CRITIQUE_B",
        "draft_message": draft.strip(),
        "active_versions": active_versions,
        "audit_trail": [f"[DRAFT_AGENT] Draft generated for {channel} | Distress: {distress_flag}"]
    }
    if distress_detected and not state.get("distress_flag"):
        updates["distress_flag"] = True
        updates["audit_trail"].append("[DRAFT_AGENT] ⚠️ DISTRESS DETECTED in message history")

    return updates
