"""
Renewal API — trigger renewal workflow, handle webhooks, status queries.
"""
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
import aiosqlite
from app.core.security import get_current_user
from app.core.config import get_settings
from app.agents.workflow import get_workflow
from app.agents.state import RenewalState
from app.utils.logger import logger

settings = get_settings()
router = APIRouter(prefix="/renewal", tags=["Renewal Workflow"])


class TriggerRenewalRequest(BaseModel):
    policy_id: str
    override_channel: Optional[str] = None


class WebhookInboundRequest(BaseModel):
    policy_id: str
    channel: str  # Email/WhatsApp/Voice
    content: str
    customer_id: str


from app.agents.escalation import check_stale_emails

async def load_policy_state(policy_id: str) -> Optional[RenewalState]:
    """Load full policy + customer context from SQLite."""
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        
        cursor = await db.execute("""
            SELECT p.*, c.name, c.age, c.city, c.preferred_channel,
                   c.preferred_language, c.segment, c.customer_id as cust_id, 
                   c.phone as cust_phone, c.email as cust_email,
                   ps.current_node, ps.last_channel, ps.waiting_for,
                   ps.sentiment_score, ps.distress_flag, ps.objection_count, ps.mode
            FROM policies p
            JOIN customers c ON p.customer_id = c.customer_id
            LEFT JOIN policy_state ps ON p.policy_id = ps.policy_id
            WHERE p.policy_id = ?
        """, (policy_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        
        # Load interaction history
        cursor2 = await db.execute("""
            SELECT channel, message_direction as direction, content, sentiment_score, created_at
            FROM interactions WHERE policy_id=? ORDER BY created_at DESC LIMIT 20
        """, (policy_id,))
        interactions = [dict(r) for r in await cursor2.fetchall()]
        
        return RenewalState(
            policy_id=policy_id,
            customer_id=row["cust_id"],
            customer_name=row["name"],
            customer_age=row["age"] or 0,
            customer_city=row["city"] or "",
            customer_phone=row["cust_phone"],
            customer_email=row["cust_email"],
            preferred_channel=row["preferred_channel"] or "Email",
            preferred_language=row["preferred_language"] or "English",
            segment=row["segment"] or "Standard",
            policy_type=row["policy_type"] or "",
            sum_assured=row["sum_assured"] or 0,
            annual_premium=row["annual_premium"] or 0,
            premium_due_date=str(row["premium_due_date"] or ""),
            payment_mode=row["payment_mode"] or "",
            fund_value=row["fund_value"],
            policy_status=row["status"] or "ACTIVE",
            current_node=row["current_node"] or "ORCHESTRATOR",
            selected_channel=None,
            channel_justification=None,
            critique_a_result=None,
            execution_plan=None,
            draft_message=None,
            greeting=None,
            closing=None,
            final_message=None,
            critique_b_result=None,
            distress_flag=bool(row["distress_flag"]),
            objection_count=row["objection_count"] or 0,
            mode=row["mode"] or "AI",
            escalate=False,
            escalation_reason=None,
            interaction_history=interactions,
            rag_policy_docs=None,
            rag_objections=None,
            rag_regulations=None,
            messages_sent=[],
            audit_trail=[],
            active_versions={},
            error=None
        )


@router.post("/trigger", summary="Trigger renewal workflow for a policy")
async def trigger_renewal(
    req: TriggerRenewalRequest,
    background_tasks: BackgroundTasks,
    current_user: str = Depends(get_current_user)
):
    state = await load_policy_state(req.policy_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Policy {req.policy_id} not found")
    
    if state["mode"] == "HUMAN_CONTROL":
        # Get escalation details for better user feedback
        async with aiosqlite.connect(settings.sqlite_db_path) as db:
            cursor = await db.execute(
                "SELECT case_id, escalation_reason, priority_score FROM escalation_cases WHERE policy_id=? AND status='OPEN' LIMIT 1",
                (req.policy_id,)
            )
            esc = await cursor.fetchone()
        
        esc_detail = ""
        if esc:
            esc_detail = f" (Case #{esc[0]}: {esc[1]})"
        
        raise HTTPException(
            status_code=400, 
            detail=f"❌ Policy is in HUMAN_CONTROL mode{esc_detail}. This policy has been escalated for human review. Please resolve the escalation case first and then retry."
        )
    
    # Override channel if specified
    if req.override_channel:
        state["preferred_channel"] = req.override_channel

    workflow = get_workflow()
    
    async def run_workflow():
        logger.info(f"[WORKFLOW] Starting background task for {req.policy_id}")
        try:
            async for chunk in workflow.astream(state, stream_mode="updates"):
                logger.debug(f"[WORKFLOW] Chunk: {list(chunk.items())}")
                # chunk is a dict: {node_name: {updates}}
                for node_name, updates in chunk.items():
                    current_node = updates.get("current_node", node_name.upper())
                    audit_entry = updates.get("audit_trail", ["Node execution"])[-1]
                    
                    logger.info(f"[WORKFLOW] {req.policy_id} -> {node_name} -> {current_node}")
                    
                    async with aiosqlite.connect(settings.sqlite_db_path) as db:
                        # Update current state
                        await db.execute(
                            "UPDATE policy_state SET current_node=?, updated_at=CURRENT_TIMESTAMP WHERE policy_id=?",
                            (current_node, req.policy_id)
                        )
                        # Log to workflow_logs
                        await db.execute(
                            "INSERT INTO workflow_logs (policy_id, node_name, content) VALUES (?, ?, ?)",
                            (req.policy_id, node_name, audit_entry)
                        )
                        await db.commit()
                        
            logger.info(f"[WORKFLOW] Completed for {req.policy_id}")
        except Exception as e:
            logger.error(f"[WORKFLOW ERROR] {req.policy_id}: {e}")
            async with aiosqlite.connect(settings.sqlite_db_path) as db:
                await db.execute(
                    "INSERT INTO audit_logs (policy_id, action_type, action_reason, triggered_by) VALUES (?, ?, ?, ?)",
                    (req.policy_id, "WORKFLOW_ERROR", str(e), "System")
                )
                await db.commit()

    background_tasks.add_task(run_workflow)
    
    return {
        "status": "triggered",
        "policy_id": req.policy_id,
        "customer": state["customer_name"],
        "preferred_channel": state["preferred_channel"],
        "message": "Renewal workflow started in background"
    }


@router.post("/webhook/inbound", summary="Handle inbound customer reply (Email/WhatsApp/Voice)")
async def inbound_webhook(req: WebhookInboundRequest):
    """Process inbound customer messages and update policy state."""
    
    # Distress detection - more comprehensive keywords
    distress_keywords = [
        "lost job", "husband passed", "death", "can't pay", "hardship", "hospital",
        "human", "escalate", "emergency", "urgent", "complaint", "help needed",
        "assist", "agent", "manager", "supervisor", "transfer",
        "call me", "call back", "please call"
    ]
    distress = any(kw in req.content.lower() for kw in distress_keywords)
    
    # Objection detection
    objection_keywords = ["not interested", "too expensive", "cancel", "can't afford", "later"]
    is_objection = any(kw in req.content.lower() for kw in objection_keywords)
    
    # Payment detection
    payment_keywords = ["paid", "renew", "payment done", "processed"]
    is_payment = any(kw in req.content.lower() for kw in payment_keywords)
    
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            "INSERT INTO interactions (policy_id, channel, message_direction, content, sentiment_score) VALUES (?, ?, ?, ?, ?)",
            (req.policy_id, req.channel, "INBOUND", req.content, 0.5 if is_payment else (-0.5 if distress else 0.0))
        )
        
        # Log to workflow_logs so it appears in the Trace modal for UI showcase
        log_node = "PAYMENT_RECEIVED" if is_payment else "CUSTOMER_REPLY"
        await db.execute(
            "INSERT INTO workflow_logs (policy_id, node_name, content) VALUES (?, ?, ?)",
            (req.policy_id, log_node, f"Inbound {req.channel}: {req.content}")
        )
        
        if is_payment:
            # Update policy status to PAID
            await db.execute(
                "UPDATE policies SET status='PAID' WHERE policy_id=?",
                (req.policy_id,)
            )
            # Update policy state
            await db.execute(
                "UPDATE policy_state SET current_node='COMPLETED', distress_flag=0, objection_count=0, mode='AI', updated_at=CURRENT_TIMESTAMP WHERE policy_id=?",
                (req.policy_id,)
            )
            # Log to audit logs
            await db.execute(
                "INSERT INTO audit_logs (policy_id, action_type, action_reason, triggered_by) VALUES (?, ?, ?, ?)",
                (req.policy_id, "POLICY_RENEWED", f"Customer confirmed payment/renewal via {req.channel}", "System")
            )
        elif distress:
            await db.execute(
                "UPDATE policy_state SET distress_flag=1, mode='HUMAN_CONTROL', updated_at=CURRENT_TIMESTAMP WHERE policy_id=?",
                (req.policy_id,)
            )
            await db.execute(
                "INSERT INTO escalation_cases (policy_id, escalation_reason, priority_score, status) VALUES (?, ?, ?, ?)",
                (req.policy_id, f"Inbound distress detected: '{req.content}' (via {req.channel})", 0.95, "OPEN")
            )
        elif is_objection:
            await db.execute(
                "UPDATE policy_state SET objection_count=objection_count+1, updated_at=CURRENT_TIMESTAMP WHERE policy_id=?",
                (req.policy_id,)
            )
        
        await db.commit()
    
    return {
        "status": "received",
        "policy_id": req.policy_id,
        "distress_detected": distress,
        "objection_detected": is_objection,
        "channel": req.channel,
        "message": req.content
    }


@router.get("/status/{policy_id}", summary="Get current status of a policy renewal")
async def get_renewal_status(
    policy_id: str,
    current_user: str = Depends(get_current_user)
):
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        
        cursor = await db.execute("""
            SELECT ps.*, p.policy_type, p.annual_premium, p.premium_due_date,
                   c.name, c.preferred_channel
            FROM policy_state ps
            JOIN policies p ON ps.policy_id = p.policy_id
            JOIN customers c ON p.customer_id = c.customer_id
            WHERE ps.policy_id = ?
        """, (policy_id,))
        state = await cursor.fetchone()
        if not state:
            raise HTTPException(status_code=404, detail=f"Policy {policy_id} not found")
        
        cursor2 = await db.execute(
            "SELECT * FROM escalation_cases WHERE policy_id=? AND status='OPEN' ORDER BY created_at DESC LIMIT 1",
            (policy_id,)
        )
        escalation = await cursor2.fetchone()
        
        cursor3 = await db.execute(
            "SELECT channel, message_direction, content, created_at FROM interactions WHERE policy_id=? ORDER BY created_at DESC LIMIT 5",
            (policy_id,)
        )
        interactions = [dict(r) for r in await cursor3.fetchall()]
        
        return {
            "policy_id": policy_id,
            "customer_name": state["name"],
            "policy_type": state["policy_type"],
            "current_node": state["current_node"],
            "mode": state["mode"],
            "last_channel": state["last_channel"],
            "last_message": state["last_message"],
            "distress_flag": bool(state["distress_flag"]),
            "objection_count": state["objection_count"],
            "escalation": dict(escalation) if escalation else None,
            "recent_interactions": interactions
        }


@router.post("/check-escalations", summary="Manually trigger 'Not Opened' escalation check")
async def manual_check_escalations(threshold_minutes: int = 30):
    await check_stale_emails(threshold_minutes)
    return {"status": "check_completed", "threshold": f"{threshold_minutes}m"}


@router.get("/logs/{policy_id}", summary="Get detailed workflow logs for a policy")
async def get_workflow_logs(
    policy_id: str,
    current_user: str = Depends(get_current_user)
):
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT node_name, content, created_at FROM workflow_logs WHERE policy_id=? ORDER BY created_at ASC",
            (policy_id,)
        )
        logs = await cursor.fetchall()
    return {"policy_id": policy_id, "logs": [dict(l) for l in logs]}


class BatchTriggerRequest(BaseModel):
    days_ahead: int = 7           # Policies due within N days
    segment: Optional[str] = None # Filter by segment (optional)
    limit: int = 50               # Max policies to process in one batch


@router.post("/batch", summary="Trigger renewal workflows for all policies due within N days")
async def trigger_batch_renewal(
    req: BatchTriggerRequest,
    background_tasks: BackgroundTasks,
    current_user: str = Depends(get_current_user)
):
    """
    Find all ACTIVE/LAPSED policies with premium_due_date within the next N days,
    skip those in HUMAN_CONTROL mode, and trigger a renewal workflow for each.
    """
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row

        query = """
            SELECT p.policy_id, p.policy_type, p.premium_due_date,
                   c.name, c.segment, ps.mode, ps.current_node
            FROM policies p
            JOIN customers c ON p.customer_id = c.customer_id
            LEFT JOIN policy_state ps ON p.policy_id = ps.policy_id
            WHERE p.status IN ('ACTIVE', 'LAPSED')
              AND p.premium_due_date BETWEEN DATE('now') AND DATE('now', ? || ' days')
              AND (ps.mode IS NULL OR ps.mode != 'HUMAN_CONTROL')
        """
        params = [str(req.days_ahead)]

        if req.segment:
            query += " AND c.segment = ?"
            params.append(req.segment)

        query += f" ORDER BY p.premium_due_date ASC LIMIT {req.limit}"

        cursor = await db.execute(query, params)
        policies = await cursor.fetchall()

    triggered = []
    skipped = []
    errors = []

    workflow = get_workflow()

    async def run_one(policy_id: str, customer_name: str):
        state = await load_policy_state(policy_id)
        if not state:
            return
        try:
            async for chunk in workflow.astream(state, stream_mode="updates"):
                for node_name, updates in chunk.items():
                    current_node = updates.get("current_node", node_name.upper())
                    audit_entry = updates.get("audit_trail", ["Node execution"])[-1]
                    async with aiosqlite.connect(settings.sqlite_db_path) as db:
                        await db.execute(
                            "UPDATE policy_state SET current_node=?, updated_at=CURRENT_TIMESTAMP WHERE policy_id=?",
                            (current_node, policy_id)
                        )
                        await db.execute(
                            "INSERT INTO workflow_logs (policy_id, node_name, content) VALUES (?, ?, ?)",
                            (policy_id, node_name, audit_entry)
                        )
                        await db.commit()
            logger.info(f"[BATCH] Completed workflow for {policy_id}")
        except Exception as e:
            logger.error(f"[BATCH ERROR] {policy_id}: {e}")
            async with aiosqlite.connect(settings.sqlite_db_path) as db:
                await db.execute(
                    "INSERT INTO audit_logs (policy_id, action_type, action_reason, triggered_by) VALUES (?, ?, ?, ?)",
                    (policy_id, "WORKFLOW_ERROR", str(e)[:300], "Batch Trigger")
                )
                await db.commit()

    for row in policies:
        policy_id = row["policy_id"]
        customer_name = row["name"]
        if row["mode"] == "HUMAN_CONTROL":
            skipped.append(policy_id)
            continue
        try:
            background_tasks.add_task(run_one, policy_id, customer_name)
            triggered.append({"policy_id": policy_id, "customer": customer_name, "due": str(row["premium_due_date"])})
        except Exception as e:
            errors.append({"policy_id": policy_id, "error": str(e)})

    logger.info(f"[BATCH] Triggered {len(triggered)} | Skipped {len(skipped)} | Errors {len(errors)}")

    return {
        "status": "batch_started",
        "triggered_count": len(triggered),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "days_ahead": req.days_ahead,
        "segment_filter": req.segment,
        "triggered": triggered,
        "skipped": skipped,
        "errors": errors
    }
