"""
Email Channel Agent — Step 6 (Email)
Modular: developers can extend this independently.
"""
from app.agents.state import RenewalState
import aiosqlite
import os
from app.core.config import get_settings
from datetime import datetime

settings = get_settings()


from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from app.utils.logger import logger
import time

async def email_send_node(state: RenewalState) -> dict:
    final_message = state.get("final_message", "")
    if not final_message:
        final_message = f"{state.get('greeting','')}\n\n{state.get('draft_message','')}\n\n{state.get('closing','')}".strip()

    email_subject = f"[Suraksha Life] Renewal Reminder — {state.get('policy_type', 'Policy')} | Due {state.get('premium_due_date', 'N/A')}"
    
    # Get customer email from state or mock it
    customer_email = state.get("customer_email", f"{state.get('customer_name', 'customer').replace(' ', '_')}@example.com")
    
    # Twilio Integration (SendGrid)
    success = False
    api_error = None
    is_simulated = False
    message_id = None
    
    try:
        # Use configured sender email as test recipient, or set SENDGRID_TEST_RECIPIENT in .env
        actual_recipient = os.getenv("SENDGRID_TEST_RECIPIENT", settings.sendgrid_from_email)
        
        # Add timestamp to subject for uniqueness (as verified in testing)
        display_subject = f"{email_subject} ({time.strftime('%H:%M:%S')})"
        
        if not settings.sendgrid_api_key or "YOUR_" in settings.sendgrid_api_key:
            is_simulated = True
            success = True
            message_id = f"sim_{int(time.time())}"
            logger.info(f"[EMAIL_SIM] No API key. Simulated send to {customer_email}")
        else:
            message = Mail(
                from_email=settings.sendgrid_from_email,
                to_emails=actual_recipient,
                subject=display_subject,
                plain_text_content=f"Logical Recipient: {customer_email}\n\n{final_message}"
            )

            sg = SendGridAPIClient(settings.sendgrid_api_key)
            response = sg.send(message)
            message_id = response.headers.get("X-Message-Id")
            
            if response.status_code in [200, 201, 202]:
                success = True
                logger.info(f"[EMAIL_AGENT] Logical Send to {customer_email} | Actual Send to {actual_recipient} | ID: {message_id}")
            else:
                api_error = f"SendGrid API Error {response.status_code}: {response.body}"
                logger.error(f"[EMAIL_AGENT] {api_error}")
            
    except Exception as e:
        api_error = f"SendGrid Request Failed: {str(e)}"
        logger.error(f"[EMAIL_AGENT] {api_error}")
        # Fallback to simulation on failure for showcase
        is_simulated = True
        success = True
        message_id = f"err_sim_{int(time.time())}"
        logger.info(f"[EMAIL_SIM] API Failed. Falling back to simulated log for {customer_email}")

    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        status_label = "EMAIL_SENT" if not is_simulated else "EMAIL_SIMULATED"
        reason_label = f"SendGrid Status: Success" if not is_simulated else f"Simulation: {api_error or 'No API key'}"
        
        await db.execute(
            "INSERT INTO interactions (policy_id, channel, message_direction, content, sentiment_score, message_id) VALUES (?, ?, ?, ?, ?, ?)",
            (state["policy_id"], "Email", "OUTBOUND", final_message, 0.0, message_id)
        )
        await db.execute(
            "INSERT INTO audit_logs (policy_id, action_type, action_reason, triggered_by, prompt_version) VALUES (?, ?, ?, ?, ?)",
            (state["policy_id"], status_label, reason_label, "SendGrid Agent", state.get("active_versions", {}).get("EMAIL_DRAFT"))
        )
        await db.execute(
            "UPDATE policy_state SET current_node=?, last_channel=?, last_message=?, updated_at=CURRENT_TIMESTAMP WHERE policy_id=?",
            ("AWAITING_RESPONSE", "Email", final_message, state["policy_id"])
        )
        await db.commit()

    return {
        "current_node": "COMPLETED",
        "messages_sent": [f"[EMAIL] {email_subject} → {customer_email} ({'SIMULATED' if is_simulated else 'DELIVERED'})"],
        "audit_trail": [f"[EMAIL_AGENT] Email {'simulated' if is_simulated else 'sent'} | Policy: {state['policy_id']}"]
    }

if __name__ == "__main__":
    import asyncio
    # Mock state for standalone testing
    mock_state = {
        "policy_id": "TEST-EMAIL-001",
        "customer_name": "Developer Test",
        "policy_type": "Term Life",
        "premium_due_date": "2026-05-01",
        "greeting": "Hello,",
        "draft_message": "This is a test renewal email.",
        "closing": "Regards, RenewAI"
    }
    
    async def run_test():
        print("--- Running Email Agent Standalone Test ---")
        result = await email_send_node(mock_state)
        print("Result:", result)
        
    asyncio.run(run_test())
