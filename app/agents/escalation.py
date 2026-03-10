"""
Escalation / Human Queue Manager
Routes distressed or complex cases to human agents.
"""
from app.agents.state import RenewalState
import aiosqlite
from app.core.config import get_settings
from datetime import datetime, timedelta

settings = get_settings()

PRIORITY_MAP = {
    "distress_flag": 1.0,
    "objection_threshold": 0.8,
    "hni_grievance": 0.9,
    "Critique B escalation": 0.7
}


async def escalation_node(state: RenewalState) -> dict:
    """Create escalation case and route to human queue."""
    
    reason = state.get("escalation_reason", "Auto escalation")
    priority = PRIORITY_MAP.get(reason, 0.6)
    
    # Dynamic SLA: 2h for High, 4h for Med, 8h for Low
    if priority >= 0.9:
        sla_hours = 2
    elif priority >= 0.7:
        sla_hours = 4
    else:
        sla_hours = 8
        
    sla_deadline = (datetime.utcnow() + timedelta(hours=sla_hours)).isoformat()

    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        cursor = await db.execute("""
            INSERT INTO escalation_cases 
            (policy_id, escalation_reason, priority_score, status, sla_deadline)
            VALUES (?, ?, ?, ?, ?)
        """, (state["policy_id"], reason, priority, "OPEN", sla_deadline))
        case_id = cursor.lastrowid
        
        await db.execute(
            "INSERT INTO audit_logs (policy_id, action_type, action_reason, triggered_by) VALUES (?, ?, ?, ?)",
            (state["policy_id"], "ESCALATION_CREATED", f"Case #{case_id} | Reason: {reason} | SLA: {sla_hours}h", "Escalation Manager")
        )
        await db.execute(
            "UPDATE policy_state SET current_node=?, mode=?, updated_at=CURRENT_TIMESTAMP WHERE policy_id=?",
            ("HUMAN_QUEUE", "HUMAN_CONTROL", state["policy_id"])
        )
        await db.commit()

    print(f"[ESCALATION] Case #{case_id} created | Policy: {state['policy_id']} | Priority: {priority} | Reason: {reason}")

    return {
        "current_node": "HUMAN_QUEUE",
        "mode": "HUMAN_CONTROL",
        "messages_sent": [f"[ESCALATION] Case #{case_id} created | Reason: {reason} | SLA: {sla_hours}h"],
        "audit_trail": [f"[ESCALATION_MGR] Case #{case_id} | Reason: {reason}"]
    }

async def check_stale_emails(threshold_minutes: int = 30):
    """
    Background check: Escalate if email sent but not opened.
    Used for testing with 30 mins as requested.
    """
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        # Find outbound emails with no opened_at after N minutes
        # We also check if they are already escalated to avoid duplicates
        query = f"""
            SELECT i.policy_id, i.message_id, i.created_at
            FROM interactions i
            LEFT JOIN escalation_cases e ON i.policy_id = e.policy_id AND e.status = 'OPEN'
            WHERE i.channel = 'Email' 
              AND i.message_direction = 'OUTBOUND' 
              AND i.opened_at IS NULL 
              AND i.message_id IS NOT NULL
              AND e.case_id IS NULL
              AND datetime(i.created_at) < datetime('now', '-{threshold_minutes} minutes')
        """
        async with db.execute(query) as cursor:
            stale_cases = await cursor.fetchall()
            
        for policy_id, msg_id, created_at in stale_cases:
            print(f"[SMART_ESCALATION] Policy {policy_id} ignored email {msg_id} for >{threshold_minutes}m. Escalating.")
            
            # Create escalation case manually
            priority = 0.7 # Medium priority for ignored emails
            sla_hours = 4
            sla_deadline = (datetime.utcnow() + timedelta(hours=sla_hours)).isoformat()
            reason = f"Email not opened since {created_at} (> {threshold_minutes}m)"
            
            cursor = await db.execute("""
                INSERT INTO escalation_cases 
                (policy_id, escalation_reason, priority_score, status, sla_deadline)
                VALUES (?, ?, ?, ?, ?)
            """, (policy_id, reason, priority, "OPEN", sla_deadline))
            case_id = cursor.lastrowid
            
            await db.execute(
                "INSERT INTO audit_logs (policy_id, action_type, action_reason, triggered_by) VALUES (?, ?, ?, ?)",
                (policy_id, "ESCALATION_CREATED", reason, "Smart Escalation")
            )
            await db.execute(
                "UPDATE policy_state SET current_node='HUMAN_QUEUE', mode='HUMAN_CONTROL', updated_at=CURRENT_TIMESTAMP WHERE policy_id=?",
                (policy_id,)
            )

        await db.commit()
