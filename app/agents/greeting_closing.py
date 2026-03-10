"""
Greeting/Closing Agent — Step 4a (runs in parallel with Draft Agent)
Generates culturally appropriate greetings and compliant closings.
"""
from app.agents.state import RenewalState
from app.core.gemini_client import call_llm
from app.api.prompts import get_active_prompt
from app.core.config import get_settings
import json
import aiosqlite

settings = get_settings()

GREETING_SYSTEM_PROMPT = """
You are the RenewAI Greeting Agent for Suraksha Life Insurance.
Generate a culturally appropriate, warm greeting in the specified language (English, Hindi, or Bengali).
Use the customer's first name and policy type. Match the tone from the plan.
Do NOT be robotic. Be warm and human.

For Hindi: Use respectful honorifics like "जी". Example: "नमस्ते [Name] जी"
For Bengali: Use respectful honorifics like "বাবু/দিদি" as appropriate. Example: "নমস্কার [Name] বাবু/দিদি"
For other regional languages: Use culturally appropriate greetings.

Output ONLY the greeting text (1-2 sentences max).
"""

CLOSING_SYSTEM_PROMPT = """
You are the RenewAI Closing Agent for Suraksha Life Insurance.
Generate a compliant closing for a renewal communication in the specified language.

MANDATORY: Always end with a translation of this exact line into the target language:
"This message is from an AI assistant of Suraksha Life Insurance. Reply HUMAN anytime to speak with a specialist."

Also include: next step, payment link placeholder [PAYMENT_LINK], and a warm sign-off.
Output ONLY the closing text in the requested language.
"""


async def greeting_closing_node(state: RenewalState) -> dict:
    """Step 4a: Generate greeting and closing."""
    
    plan = state.get("execution_plan", {})
    channel = state.get("selected_channel", "Email")
    language = plan.get("language", state.get("preferred_language", "English"))
    tone = plan.get("tone", "friendly")
    first_name = state["customer_name"].split()[0]

    greeting_prompt = f"""
Customer First Name: {first_name}
Policy Type: {state['policy_type']}
Language: {language}
Tone: {tone}
Channel: {channel}
Greeting Style: {plan.get('greeting_style', 'warm')}
"""

    closing_prompt = f"""
Customer Name: {state['customer_name']}
Channel: {channel}
Language: {language}
Tone: {tone}
Due Date: {state['premium_due_date']}
CTA Type: {plan.get('cta_type', 'payment_link')}
"""

    greeting_data = await get_active_prompt("GREETING")
    closing_data = await get_active_prompt("CLOSING")
    
    greeting_tmpl = greeting_data["prompt_text"] or GREETING_SYSTEM_PROMPT
    closing_tmpl = closing_data["prompt_text"] or CLOSING_SYSTEM_PROMPT

    greeting = await call_llm(greeting_tmpl, greeting_prompt, temperature=0.4)
    closing = await call_llm(closing_tmpl, closing_prompt, temperature=0.2)
    
    active_versions = state.get("active_versions", {})
    if greeting_data["version"]: active_versions["GREETING"] = greeting_data["version"]
    if closing_data["version"]: active_versions["CLOSING"] = closing_data["version"]

    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            "INSERT INTO audit_logs (policy_id, action_type, action_reason, triggered_by, prompt_version) VALUES (?, ?, ?, ?, ?)",
            (state["policy_id"], "GREETING_GENERATED", f"Greeting generated for {channel} in {language}", "Greeting Agent", greeting_data["version"])
        )
        await db.execute(
            "INSERT INTO audit_logs (policy_id, action_type, action_reason, triggered_by, prompt_version) VALUES (?, ?, ?, ?, ?)",
            (state["policy_id"], "CLOSING_GENERATED", f"Closing generated for {channel} in {language}", "Closing Agent", closing_data["version"])
        )
        await db.commit()

    return {
        "current_node": "DRAFT_AND_GREETING",
        "greeting": greeting.strip(),
        "closing": closing.strip(),
        "active_versions": active_versions,
        "audit_trail": [f"[GREETING_CLOSING] Greeting/Closing generated for {channel} in {language}"]
    }
