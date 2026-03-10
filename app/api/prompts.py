"""
Prompt Lab API
Stores, versions, and serves system prompts for each agent.
Allows editing and activating prompts from the UI without code changes.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.core.security import get_current_user
from app.core.config import get_settings
import aiosqlite

settings = get_settings()
router = APIRouter(prefix="/prompts", tags=["Prompt Lab"])

# ── Default prompts seed (pulled from agent files) ──────────────────────────
SEED_PROMPTS = {
    "ORCHESTRATOR": """You are the RenewAI Renewal Orchestrator for Suraksha Life Insurance.
Your job: Given a customer profile, policy data, and full interaction history, 
decide the NEXT BEST communication channel (Email / WhatsApp / Voice) for renewal outreach.

Rules:
1. Check if payment_done = True → set payment_done flag, no more outreach needed
2. If distress_flag is True OR objection_count >= 3 → set escalate: true
3. Consider preferred_channel first, then interaction history to see what worked
4. If a channel has been tried 3+ times with no result, switch to fallback

Respond ONLY with valid JSON:
{
  "channel": "WhatsApp|Email|Voice",
  "justification": "specific evidence from history",
  "priority": "high|medium|low",
  "fallback_channel": "Email|WhatsApp|Voice",
  "escalate": false,
  "payment_done": false
}""",

    "EMAIL_DRAFT": """You are the RenewAI Email Draft Agent for Suraksha Life Insurance.
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

Output ONLY the email body text in the requested language.""",

    "WHATSAPP_DRAFT": """You are the RenewAI WhatsApp Draft Agent for Suraksha Life Insurance.
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

Output ONLY the WhatsApp body text in the requested language.""",

    "VOICE_DRAFT": """You are the RenewAI Voice Script Draft Agent for Suraksha Life Insurance.
Generate a voice call script BODY in the specified language (English, Hindi, or Bengali).

Requirements:
- Languages Supported: English, Hindi, Bengali.
- Include [PAUSE] markers for natural speech breaks
- Structure: premium reminder → benefit highlight → objection handle → payment CTA
- Mark [ESCALATE] clearly if distress detected
- Leave [GREETING] at top and [CLOSING] at bottom
- Keep under 150 words (about 60 seconds)
- Use natural spoken language (e.g., spoken Hindi/Bengali, not overly formal literary script)

Output ONLY the voice script body in the requested language.""",

    "GREETING": """You are the RenewAI Greeting Agent for Suraksha Life Insurance.
Generate a culturally appropriate, warm greeting in the specified language (English, Hindi, or Bengali).
Use the customer's first name and policy type. Match the tone from the plan.
Do NOT be robotic. Be warm and human.

For Hindi: Use respectful honorifics like "जी". Example: "नमस्ते [Name] जी"
For Bengali: Use respectful honorifics like "বাবু/দিদি" as appropriate. Example: "নমস্কার [Name] বাবু/দিদি"
For other regional languages: Use culturally appropriate greetings.

Output ONLY the greeting text (1-2 sentences max).""",

    "CLOSING": """You are the RenewAI Closing Agent for Suraksha Life Insurance.
Generate a compliant closing for a renewal communication in the specified language.

MANDATORY: Always end with a translation of this exact line into the target language:
"This message is from an AI assistant of Suraksha Life Insurance. Reply HUMAN anytime to speak with a specialist."

Also include: next step, payment link placeholder [PAYMENT_LINK], and a warm sign-off.
Output ONLY the closing text in the requested language.""",

    "PLANNER": """You are the RenewAI Communication Planner for Suraksha Life Insurance.
Given customer data and channel decision, create a precise communication plan.

Output a JSON plan with:
{
  "tone": "empathetic|urgent|friendly|formal",
  "language": "English|Hindi|Bengali",
  "key_message": "core renewal CTA",
  "objection_handle": "specific rebuttal if objections exist",
  "cta_type": "payment_link|call_back|reply",
  "greeting_style": "warm|formal|regional",
  "distress_flag": false
}""",

    "CRITIQUE_A": """You are the RenewAI Compliance Critic A for Suraksha Life Insurance.
Review the orchestrator's channel selection decision.

Check:
1. Is the channel appropriate for this customer (age, segment, history)?
2. Are there any compliance red flags (distress, recent death, job loss)?
3. Is timing appropriate (e.g., not too many recent attempts)?

Output a JSON with:
{
  "approved": true|false,
  "issues": ["list of issues if any"],
  "recommendation": "proceed|escalate|change_channel"
}""",

    "CRITIQUE_B": """You are the RenewAI Content Critic B for Suraksha Life Insurance.
Review the drafted renewal message for quality and compliance.

Check:
1. Does the message match the approved tone and language?
2. Are all facts accurate (no hallucinated amounts or policy details)?
3. Does it include required disclaimers and CTA?
4. Is it culturally appropriate for the language?
5. Is the length appropriate for the channel?

Output a JSON with:
{
  "approved": true|false,
  "quality_score": 0.0-1.0,
  "issues": ["list of specific issues"],
  "recommendation": "send|revise|escalate"
}"""
}


async def ensure_seeded(db):
    """Seed default prompts if table is empty."""
    cursor = await db.execute("SELECT COUNT(*) FROM prompt_versions")
    row = await cursor.fetchone()
    if row[0] == 0:
        for agent_name, prompt_text in SEED_PROMPTS.items():
            await db.execute(
                "INSERT INTO prompt_versions (agent_name, version, prompt_text, is_active, notes, created_by) VALUES (?,?,?,?,?,?)",
                (agent_name, 1, prompt_text.strip(), 1, "Initial version (seeded from code)", "system")
            )
        await db.commit()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/", summary="List all active prompts per agent")
async def list_prompts(current_user: str = Depends(get_current_user)):
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        await ensure_seeded(db)
        cursor = await db.execute("""
            SELECT agent_name,
                   MAX(version) as latest_version,
                   SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END) as has_active
            FROM prompt_versions GROUP BY agent_name ORDER BY agent_name
        """)
        agents = await cursor.fetchall()
    return {"agents": [dict(a) for a in agents]}


class PromptTestRequest(BaseModel):
    prompt_text: str
    sample_input: Optional[str] = None


@router.post("/{agent_name}/test", summary="Test a prompt with sample data")
async def test_prompt(agent_name: str, req: PromptTestRequest, current_user: str = Depends(get_current_user)):
    """Run a prompt test with sample input and return the LLM response."""
    from app.core.gemini_client import call_llm_raw
    sample_input = req.sample_input or "Customer: Priya Sharma | Policy: Term Gold | Premium: \u20b912,500 | Language: English | Channel: WhatsApp"
    try:
        result = await call_llm_raw(req.prompt_text.strip(), sample_input, temperature=0.3)
        return {
            "response": result.text,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "duration_ms": round(result.duration_ms, 2)
        }
    except Exception as e:
        return {
            "error": str(e),
            "response": None,
            "tokens_in": 0,
            "tokens_out": 0,
            "duration_ms": 0
        }


@router.get("/{agent_name}/compare", summary="Compare two versions of a prompt for an agent")
async def compare_prompts(agent_name: str, v1: int, v2: int, current_user: str = Depends(get_current_user)):
    """Return two prompt versions side-by-side for comparison."""
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, version, prompt_text, is_active, notes, created_by FROM prompt_versions WHERE agent_name=? AND version IN (?,?) ORDER BY version DESC",
            (agent_name.upper(), v1, v2)
        )
        versions = await cursor.fetchall()
    if len(versions) < 2:
        raise HTTPException(404, f"Could not find both versions {v1} and {v2} for {agent_name}")
    return {"agent_name": agent_name.upper(), "versions": [dict(v) for v in versions]}


@router.get("/{agent_name}", summary="Get all versions for an agent")
async def get_agent_prompts(agent_name: str, current_user: str = Depends(get_current_user)):
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        await ensure_seeded(db)
        cursor = await db.execute(
            "SELECT * FROM prompt_versions WHERE agent_name=? ORDER BY version DESC",
            (agent_name.upper(),)
        )
        versions = await cursor.fetchall()
    if not versions:
        raise HTTPException(404, f"Agent '{agent_name}' not found")
    return {"agent_name": agent_name.upper(), "versions": [dict(v) for v in versions]}


class PromptCreate(BaseModel):
    agent_name: str
    prompt_text: str
    notes: Optional[str] = None
    activate: bool = False


@router.post("/", summary="Save a new prompt version")
async def create_prompt_version(req: PromptCreate, current_user: str = Depends(get_current_user)):
    agent = req.agent_name.upper()
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        await ensure_seeded(db)
        cursor = await db.execute(
            "SELECT MAX(version) as max_v FROM prompt_versions WHERE agent_name=?", (agent,)
        )
        row = await cursor.fetchone()
        next_version = (row["max_v"] or 0) + 1

        if req.activate:
            await db.execute(
                "UPDATE prompt_versions SET is_active=0 WHERE agent_name=?", (agent,)
            )

        await db.execute(
            "INSERT INTO prompt_versions (agent_name, version, prompt_text, is_active, notes, created_by) VALUES (?,?,?,?,?,?)",
            (agent, next_version, req.prompt_text.strip(), 1 if req.activate else 0, req.notes, current_user)
        )
        await db.commit()
    return {"success": True, "agent_name": agent, "version": next_version, "is_active": req.activate}


class ActivateRequest(BaseModel):
    version_id: int


@router.patch("/{agent_name}/activate", summary="Set the active prompt version")
async def activate_prompt(agent_name: str, req: ActivateRequest, current_user: str = Depends(get_current_user)):
    agent = agent_name.upper()
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute("UPDATE prompt_versions SET is_active=0 WHERE agent_name=?", (agent,))
        result = await db.execute(
            "UPDATE prompt_versions SET is_active=1 WHERE id=? AND agent_name=?",
            (req.version_id, agent)
        )
        if result.rowcount == 0:
            raise HTTPException(404, "Version not found for this agent")
        await db.commit()
    return {"success": True, "activated_version_id": req.version_id}


# (PromptTestRequest and test_prompt moved above GET /{agent_name} to fix routing)


async def get_active_prompt(agent_name: str) -> dict:
    """Used by agent nodes to fetch the active prompt and version from DB."""
    try:
        async with aiosqlite.connect(settings.sqlite_db_path) as db:
            cursor = await db.execute(
                "SELECT prompt_text, version FROM prompt_versions WHERE agent_name=? AND is_active=1 ORDER BY version DESC LIMIT 1",
                (agent_name.upper(),)
            )
            row = await cursor.fetchone()
            if row:
                return {"prompt_text": row[0], "version": row[1]}
            return {"prompt_text": None, "version": None}
    except Exception:
        return {"prompt_text": None, "version": None}
