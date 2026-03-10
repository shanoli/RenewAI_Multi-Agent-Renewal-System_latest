"""
WhatsApp Channel Agent — Step 6 (WhatsApp)
Modular: developers can extend this independently.

Error 63016 Fix Guide:
  WhatsApp requires pre-approved message templates for business-initiated outreach.
  - Set TWILIO_CONTENT_SID in .env with a Twilio-approved Content Template SID.
  - Without it, raw messages only work if the customer messaged you first (24h window).
  - Get a Content SID from: https://console.twilio.com/us1/develop/sms/content
"""
from app.agents.state import RenewalState
import aiosqlite
import json
import os
from app.core.config import get_settings

settings = get_settings()

from twilio.rest import Client
from app.utils.logger import logger


async def whatsapp_send_node(state: RenewalState) -> dict:
    # Build message components
    greeting = state.get("greeting", f"Hi {state.get('customer_name', 'Customer')},")
    draft = state.get("draft_message",
        f"Your {state.get('policy_type', 'policy')} {state.get('policy_id', 'N/A')}\n"
        f"is due for renewal on {state.get('premium_due_date', 'N/A')}.\n\n"
        f"Annual Premium: ₹{state.get('annual_premium', state.get('premium_amount', 'N/A'))}"
    )
    closing = state.get("closing", "Reply 1 to renew\nReply HUMAN to speak to advisor")
    final_message = state.get("final_message") or f"{greeting}\n\n{draft}\n\n{closing}".strip()

    phone = state.get("customer_phone")
    logical_phone = phone or "+91-XXXXX-XXXXX"

    if not phone:
        logger.warning(f"[WHATSAPP_AGENT] Missing phone number for {state.get('customer_name')} - using dummy for logs")

    # Per user request: use logical phone for logs, actual send to test number
    # Set TWILIO_TEST_NUMBER in .env for your own test number
    to_phone = os.getenv("TWILIO_TEST_NUMBER", "whatsapp:+14155238886")

    # Twilio Integration
    success = False
    api_error = None
    is_simulated = False
    message_ids = []
    messages_to_send = []

    try:
        if not settings.twilio_account_sid or "YOUR_" in settings.twilio_account_sid:
            is_simulated = True
            success = True
            message_ids = [f"sim_wa_{int(time.time())}"]
            logger.info(f"[WHATSAPP_SIM] No API key. Simulated send to {logical_phone}")
        else:
            client = Client(settings.twilio_account_sid, settings.twilio_auth_token)

            if settings.twilio_content_sid:
                # --- PRIMARY PATH: Twilio Content API (Approved Templates) ---
                content_vars = json.dumps({
                    "1": greeting[:500],
                    "2": draft[:1000],
                    "3": closing[:500]
                })
                try:
                    message = client.messages.create(
                        from_=settings.twilio_from_number,
                        to=to_phone,
                        content_sid=settings.twilio_content_sid,
                        content_variables=content_vars
                    )
                    if message.sid:
                        message_ids.append(message.sid)
                        logger.info(f"[WHATSAPP_AGENT] Template Msg Sent | SID: {message.sid} | To: {logical_phone}")
                    else:
                        raise Exception("Twilio returned no SID for template message")
                except Exception as tmpl_err:
                    err_str = str(tmpl_err)
                    if "63016" in err_str:
                        logger.error("[WHATSAPP_AGENT] Error 63016: Template failed.")
                    raise

            else:
                # --- FALLBACK PATH: Sequential messages ---
                logger.info("[WHATSAPP_AGENT] Fallback sequential send.")
                messages_to_send = [greeting, draft, closing]

                for msg_body in messages_to_send:
                    if not msg_body: continue
                    message = client.messages.create(
                        body=msg_body,
                        from_=settings.twilio_from_number,
                        to=to_phone
                    )
                    if message.sid:
                        message_ids.append(message.sid)
                        logger.info(f"[WHATSAPP_AGENT] Sequential Msg Sent | SID: {message.sid} | To: {logical_phone}")

        if message_ids:
            success = True

    except Exception as e:
        api_error = f"Twilio Request Failed: {str(e)}"
        logger.error(f"[WHATSAPP_AGENT] {api_error}")
        # Fallback to simulation
        is_simulated = True
        success = True
        message_ids = [f"err_sim_wa_{int(time.time())}"]
        logger.info(f"[WHATSAPP_SIM] API Failed. Falling back to simulated log for {logical_phone}")

    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        log_content = final_message or "\n\n".join(messages_to_send)
        status_label = "WHATSAPP_SENT" if not is_simulated else "WHATSAPP_SIMULATED"
        reason_label = f"Twilio Status: Success" if not is_simulated else f"Simulation: {api_error or 'No API key'}"
        
        await db.execute(
            "INSERT INTO interactions (policy_id, channel, message_direction, content, sentiment_score) VALUES (?, ?, ?, ?, ?)",
            (state["policy_id"], "WhatsApp", "OUTBOUND", log_content, 0.0)
        )
        await db.execute(
            "INSERT INTO audit_logs (policy_id, action_type, action_reason, triggered_by, prompt_version) VALUES (?, ?, ?, ?, ?)",
            (state["policy_id"], status_label, reason_label, "Twilio Agent",
             state.get("active_versions", {}).get("WHATSAPP_DRAFT"))
        )
        await db.execute(
            "UPDATE policy_state SET current_node=?, last_channel=?, last_message=?, updated_at=CURRENT_TIMESTAMP WHERE policy_id=?",
            ("AWAITING_RESPONSE", "WhatsApp", log_content, state["policy_id"])
        )
        await db.commit()

    return {
        "current_node": "COMPLETED",
        "messages_sent": [
            f"[WHATSAPP] {'SIMULATED' if is_simulated else 'SENT'} to {state['customer_name']}"
        ],
        "audit_trail": [f"[WHATSAPP_AGENT] WA message {'simulated' if is_simulated else 'sent'} | Policy: {state['policy_id']}"]
    }


if __name__ == "__main__":
    import asyncio
    # Mock state for standalone testing
    mock_state = {
        "policy_id": "TEST-TW-002",
        "customer_name": "Developer Twilio",
        "customer_phone": "+91XXXXXXXXXX",
        "policy_type": "Jeevan Raksha Yojana",
        "premium_due_date": "2026-07-20",
        "annual_premium": "5,000",
        "greeting": "Hi,",
        "draft_message": "Your Jeevan Raksha Yojana policy TEST-TW-002 is due for renewal.",
        "closing": "Reply 1 to renew\nReply HUMAN to speak to advisor"
    }

    async def run_test():
        print("--- Running WhatsApp Agent Standalone Test ---")
        result = await whatsapp_send_node(mock_state)
        print("Result:", result)

    asyncio.run(run_test())
