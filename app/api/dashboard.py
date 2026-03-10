"""
Dashboard API — metrics, escalations, audit logs, agent telemetry.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.core.security import get_current_user
from app.core.config import get_settings
import aiosqlite
import httpx

settings = get_settings()
router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/overview", summary="Get renewal operations overview")
async def get_overview(current_user: str = Depends(get_current_user)):
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        
        total = await (await db.execute("SELECT COUNT(*) as c FROM policies")).fetchone()
        active = await (await db.execute("SELECT COUNT(*) as c FROM policies WHERE status='ACTIVE'")).fetchone()
        ai_mode = await (await db.execute("SELECT COUNT(*) as c FROM policy_state WHERE mode='AI'")).fetchone()
        human_mode = await (await db.execute("SELECT COUNT(*) as c FROM policy_state WHERE mode='HUMAN_CONTROL'")).fetchone()
        distress = await (await db.execute("SELECT COUNT(*) as c FROM policy_state WHERE distress_flag=1")).fetchone()
        open_esc = await (await db.execute("SELECT COUNT(*) as c FROM escalation_cases WHERE status='OPEN'")).fetchone()
        
        channel_dist = await (await db.execute("""
            SELECT last_channel, COUNT(*) as count FROM policy_state 
            WHERE last_channel IS NOT NULL GROUP BY last_channel
        """)).fetchall()
        
        recent_actions = await (await db.execute("""
            SELECT action_type, COUNT(*) as count FROM audit_logs 
            WHERE created_at >= datetime('now', '-24 hours') GROUP BY action_type
        """)).fetchall()
    
    return {
        "total_policies": total["c"],
        "active_policies": active["c"],
        "ai_managed": ai_mode["c"],
        "human_managed": human_mode["c"],
        "distress_cases": distress["c"],
        "open_escalations": open_esc["c"],
        "channel_distribution": [dict(r) for r in channel_dist],
        "last_24h_actions": [dict(r) for r in recent_actions]
    }


@router.get("/escalations", summary="List all open escalation cases")
async def get_escalations(
    status: str = "OPEN",
    current_user: str = Depends(get_current_user)
):
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT ec.*, c.name as customer_name, p.policy_type, p.annual_premium
            FROM escalation_cases ec
            JOIN policies p ON ec.policy_id = p.policy_id
            JOIN customers c ON p.customer_id = c.customer_id
            WHERE ec.status = ?
            ORDER BY ec.priority_score DESC, ec.created_at ASC
        """, (status,))
        cases = await cursor.fetchall()
    return {"escalations": [dict(c) for c in cases], "count": len(cases)}


@router.patch("/escalations/{case_id}/resolve", summary="Resolve an escalation case")
async def resolve_escalation(
    case_id: int,
    current_user: str = Depends(get_current_user)
):
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            "UPDATE escalation_cases SET status='RESOLVED' WHERE case_id=?", (case_id,)
        )
        # Fetch policy_id to reset mode
        cursor = await db.execute("SELECT policy_id FROM escalation_cases WHERE case_id=?", (case_id,))
        row = await cursor.fetchone()
        if row:
            await db.execute(
                "UPDATE policy_state SET mode='AI', distress_flag=0, current_node='ORCHESTRATOR' WHERE policy_id=?",
                (row[0],)
            )
        await db.commit()
    return {"message": f"Case {case_id} resolved", "status": "RESOLVED"}


@router.get("/audit-logs/{policy_id}", summary="Get IRDAI-ready audit trail for a policy")
async def get_audit_logs(
    policy_id: str,
    current_user: str = Depends(get_current_user)
):
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM audit_logs WHERE policy_id=? ORDER BY created_at DESC",
            (policy_id,)
        )
        logs = await cursor.fetchall()
    return {"policy_id": policy_id, "audit_logs": [dict(l) for l in logs], "count": len(logs)}


@router.get("/customers", summary="List all customers with policy summary")
async def list_customers(
    segment: str = None,
    current_user: str = Depends(get_current_user)
):
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        if segment:
            cursor = await db.execute("""
                SELECT c.*, COUNT(p.policy_id) as policy_count 
                FROM customers c LEFT JOIN policies p ON c.customer_id=p.customer_id
                WHERE c.segment=? GROUP BY c.customer_id
            """, (segment,))
        else:
            cursor = await db.execute("""
                SELECT c.*, COUNT(p.policy_id) as policy_count 
                FROM customers c LEFT JOIN policies p ON c.customer_id=p.customer_id
                GROUP BY c.customer_id
            """)
        customers = await cursor.fetchall()
    return {"customers": [dict(c) for c in customers], "count": len(customers)}
@router.get("/policies", summary="List all policies with customer names")
async def list_policies(current_user: str = Depends(get_current_user)):
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT p.*, c.name as customer_name, c.segment, c.phone as customer_phone, c.email as customer_email
            FROM policies p
            JOIN customers c ON p.customer_id = c.customer_id
            ORDER BY p.premium_due_date ASC
        """)
        policies = await cursor.fetchall()
    return {"policies": [dict(p) for p in policies], "count": len(policies)}


@router.get("/recent-activity", summary="Get recent global agent activity")
async def get_recent_activity(limit: int = 50, current_user: str = Depends(get_current_user)):
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT al.policy_id, al.action_type, al.action_reason as reason, 
                   al.triggered_by as agent_name, al.prompt_version, al.created_at,
                   c.name as customer_name, p.policy_type
            FROM audit_logs al
            LEFT JOIN policies p ON al.policy_id = p.policy_id
            LEFT JOIN customers c ON p.customer_id = c.customer_id
            ORDER BY al.created_at DESC LIMIT ?
        """, (limit,))
        activities = await cursor.fetchall()
    return {"activities": [dict(a) for a in activities]}


@router.get("/trace/{policy_id}", summary="Get complete activity trace for a policy")
async def get_policy_trace(policy_id: str, current_user: str = Depends(get_current_user)):
    """
    Get all agent activities, messages, and state changes for a specific policy.
    Used by the Trace button in Policies tab.
    """
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        
        # Get policy and customer info
        policy_cursor = await db.execute("""
            SELECT p.*, c.name as customer_name, c.segment, c.phone, c.email
            FROM policies p
            JOIN customers c ON p.customer_id = c.customer_id
            WHERE p.policy_id = ?
        """, (policy_id,))
        policy = await policy_cursor.fetchone()
        
        if not policy:
            return {"error": f"Policy {policy_id} not found", "policy_id": policy_id}
        
        # Get policy state
        state_cursor = await db.execute(
            "SELECT * FROM policy_state WHERE policy_id = ?", (policy_id,)
        )
        state = await state_cursor.fetchone()
        
        # Get all audit logs (agent actions) for this policy
        audit_cursor = await db.execute("""
            SELECT * FROM audit_logs 
            WHERE policy_id = ?
            ORDER BY created_at ASC
        """, (policy_id,))
        audit_logs = await audit_cursor.fetchall()
        
        # Get all interactions (messages) for this policy
        interactions_cursor = await db.execute("""
            SELECT * FROM interactions
            WHERE policy_id = ?
            ORDER BY created_at ASC
        """, (policy_id,))
        interactions = await interactions_cursor.fetchall()
        
        # Get workflow logs if they exist
        workflow_cursor = await db.execute("""
            SELECT * FROM workflow_logs
            WHERE policy_id = ?
            ORDER BY created_at ASC
        """, (policy_id,))
        workflow_logs = await workflow_cursor.fetchall()
        
        # Get any escalation case
        esc_cursor = await db.execute("""
            SELECT * FROM escalation_cases
            WHERE policy_id = ?
            ORDER BY created_at DESC LIMIT 1
        """, (policy_id,))
        escalation = await esc_cursor.fetchone()
        
        # Combine all activities in chronological order
        timeline = []
        
        for log in audit_logs:
            timeline.append({
                "type": "agent_action",
                "timestamp": log["created_at"],
                "agent": log["triggered_by"],
                "action": log["action_type"],
                "reason": log["action_reason"],
                "prompt_version": log["prompt_version"]
            })
        
        for interaction in interactions:
            timeline.append({
                "type": "message",
                "timestamp": interaction["created_at"],
                "direction": interaction["message_direction"],
                "channel": interaction["channel"],
                "content": interaction["content"],
                "sentiment": interaction["sentiment_score"]
            })
        
        for wlog in workflow_logs:
            timeline.append({
                "type": "workflow",
                "timestamp": wlog["created_at"],
                "node": wlog["node_name"],
                "content": wlog["content"]
            })
        
        # Sort by timestamp
        timeline = sorted(timeline, key=lambda x: x["timestamp"])
        
        return {
            "policy_id": policy_id,
            "policy": dict(policy) if policy else None,
            "policy_state": dict(state) if state else None,
            "escalation": dict(escalation) if escalation else None,
            "timeline": timeline,
            "summary": {
                "total_interactions": len(interactions),
                "total_agent_actions": len(audit_logs),
                "last_action": timeline[-1]["timestamp"] if timeline else None,
                "status": policy["status"] if policy else None
            }
        }


@router.get("/channel-stats", summary="Get real channel performance from outreach data")
async def get_channel_stats(current_user: str = Depends(get_current_user)):
    """
    Derive real sends, inbound replies, and conversion proxy from interactions + audit_logs.
    Powers the A/B Testing tab with actual outreach data.
    """
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row

        # Sends per channel (last 30 days)
        sends_cur = await db.execute("""
            SELECT channel, COUNT(*) as sends
            FROM interactions
            WHERE message_direction='OUTBOUND'
              AND created_at >= datetime('now', '-30 days')
            GROUP BY channel
        """)
        sends_rows = await sends_cur.fetchall()

        # Inbound replies (proxy for engagement) per channel
        replies_cur = await db.execute("""
            SELECT channel, COUNT(*) as replies
            FROM interactions
            WHERE message_direction='INBOUND'
              AND created_at >= datetime('now', '-30 days')
            GROUP BY channel
        """)
        replies_rows = await replies_cur.fetchall()

        # Escalations (failures/distress) per channel
        escalated_cur = await db.execute("""
            SELECT ps.last_channel, COUNT(*) as escalated
            FROM policy_state ps
            WHERE ps.distress_flag = 1
            GROUP BY ps.last_channel
        """)
        escalated_rows = await escalated_cur.fetchall()

        # Per-segment channel effectiveness from policy_state joined to customers
        segment_cur = await db.execute("""
            SELECT c.segment, ps.last_channel, COUNT(*) as count
            FROM policy_state ps
            JOIN policies p ON ps.policy_id = p.policy_id
            JOIN customers c ON p.customer_id = c.customer_id
            WHERE ps.last_channel IS NOT NULL
            GROUP BY c.segment, ps.last_channel
            ORDER BY c.segment, count DESC
        """)
        segment_rows = await segment_cur.fetchall()

        # Recent batch summary: policies triggered today  
        batch_cur = await db.execute("""
            SELECT COUNT(DISTINCT policy_id) as total,
                   SUM(CASE WHEN action_type='EMAIL_SENT' THEN 1 ELSE 0 END) as emails,
                   SUM(CASE WHEN action_type='WHATSAPP_SENT' THEN 1 ELSE 0 END) as whatsapps,
                   SUM(CASE WHEN action_type LIKE '%ESCALAT%' THEN 1 ELSE 0 END) as escalated,
                   SUM(CASE WHEN action_type LIKE '%ERROR%' OR action_type LIKE '%FAIL%' THEN 1 ELSE 0 END) as failed
            FROM audit_logs
            WHERE created_at >= datetime('now', '-24 hours')
              AND triggered_by IN ('Email Agent', 'WhatsApp Agent', 'Voice Agent', 'Batch Trigger', 'System')
        """)
        batch_row = await batch_cur.fetchone()

    sends_map = {r["channel"]: r["sends"] for r in sends_rows}
    replies_map = {r["channel"]: r["replies"] for r in replies_rows}
    escalated_map = {(r["last_channel"] or "Unknown"): r["escalated"] for r in escalated_rows}

    channels = list(set(list(sends_map.keys()) + list(replies_map.keys())))
    channel_stats = []
    for ch in channels:
        s = sends_map.get(ch, 0)
        r = replies_map.get(ch, 0)
        e = escalated_map.get(ch, 0)
        channel_stats.append({
            "channel": ch,
            "sends": s,
            "replies": r,
            "escalated": e,
            "reply_rate": round(r / s * 100, 1) if s > 0 else 0
        })

    # Build segment breakdown
    segment_data = {}
    for row in segment_rows:
        seg = row["segment"] or "Unknown"
        if seg not in segment_data:
            segment_data[seg] = []
        segment_data[seg].append({"channel": row["last_channel"], "count": row["count"]})

    batch_summary = dict(batch_row) if batch_row else {}

    return {
        "channel_stats": channel_stats,
        "segment_breakdown": segment_data,
        "last_24h_summary": {
            "total_processed": batch_summary.get("total", 0),
            "emails": batch_summary.get("emails", 0),
            "whatsapps": batch_summary.get("whatsapps", 0),
            "escalated": batch_summary.get("escalated", 0),
            "failed": batch_summary.get("failed", 0)
        }
    }


@router.get("/agent-stats", summary="Get per-agent telemetry stats")
async def get_agent_stats(current_user: str = Depends(get_current_user)):
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT 
                agent_name,
                COUNT(*) as total_calls,
                ROUND(AVG(execution_time_ms), 2) as avg_time_ms,
                ROUND(MAX(execution_time_ms), 2) as max_time_ms,
                SUM(tokens_input) as total_tokens_in,
                SUM(tokens_output) as total_tokens_out,
                SUM(CASE WHEN status='SUCCESS' THEN 1 ELSE 0 END) as success_count,
                MAX(created_at) as last_run
            FROM agent_telemetry
            GROUP BY agent_name
            ORDER BY total_calls DESC
        """)
        stats = await cursor.fetchall()

        # Also get recent raw runs
        cursor2 = await db.execute("""
            SELECT at.*, c.name as customer_name
            FROM agent_telemetry at
            LEFT JOIN policies p ON at.policy_id = p.policy_id
            LEFT JOIN customers c ON p.customer_id = c.customer_id
            ORDER BY at.created_at DESC LIMIT 100
        """)
        recent = await cursor2.fetchall()

    return {
        "agent_stats": [dict(s) for s in stats],
        "recent_runs": [dict(r) for r in recent]
    }


class ABTestCreate(BaseModel):
    test_name: str
    segment: str
    channel: str
    split_ratio: str = "50/50"
    variant_a_text: str
    variant_b_text: str
    success_metric: str
    status: str = "RUNNING"


@router.post("/abtests", summary="Create new A/B test")
async def create_abtest(req: ABTestCreate, current_user: str = Depends(get_current_user)):
    """Create a new A/B test for message variants."""
    import uuid
    import datetime
    
    test_id = f"AB-{str(uuid.uuid4())[:8]}"
    
    try:
        async with aiosqlite.connect(settings.sqlite_db_path) as db:
            await db.execute(
                """INSERT INTO ab_tests (id, name, segment, channel, variant_a, variant_b, status) 
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (test_id, req.test_name, req.segment, req.channel, req.variant_a_text, req.variant_b_text, req.status)
            )
            await db.commit()
            
            return {
                "test_id": test_id,
                "test_name": req.test_name,
                "segment": req.segment,
                "channel": req.channel,
                "variant_a": {"text": req.variant_a_text, "conversions": 0, "sends": 0},
                "variant_b": {"text": req.variant_b_text, "conversions": 0, "sends": 0},
                "success_metric": req.success_metric,
                "status": req.status,
                "created_at": datetime.datetime.utcnow().isoformat(),
                "message": "✅ A/B test created successfully. Monitor results on the A/B Testing tab."
            }
    except Exception as e:
        return {"error": str(e), "test_id": test_id}


@router.get("/abtests", summary="Get all A/B tests")
async def get_abtests(current_user: str = Depends(get_current_user)):
    """Retrieve all A/B tests (active and completed)."""
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM ab_tests ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        
    tests = []
    for r in rows:
        tests.append({
            "test_id": r["id"],
            "test_name": r["name"],
            "segment": r["segment"],
            "channel": r["channel"],
            "status": r["status"],
            "started_at": r["created_at"].split(" ")[0],
            "variant_a": {
                "text": r["variant_a"],
                "conversions": r["conv_a"] or 0,
                "sends": r["sends_a"] or 0,
                "conversion_rate": round((r["conv_a"] or 0) / r["sends_a"], 2) if (r["sends_a"] or 0) > 0 else 0
            },
            "variant_b": {
                "text": r["variant_b"],
                "conversions": r["conv_b"] or 0,
                "sends": r["sends_b"] or 0,
                "conversion_rate": round((r["conv_b"] or 0) / r["sends_b"], 2) if (r["sends_b"] or 0) > 0 else 0
            }
        })
    
    return {
        "active": [t for t in tests if t["status"] == "RUNNING"],
        "completed": [t for t in tests if t["status"] != "RUNNING"]
    }


class ABTestRecord(BaseModel):
    variant: str  # "A" or "B"
    event: str = "send"  # "send" or "conversion"


@router.patch("/abtests/{test_id}/record", summary="Record a send or conversion for an A/B test")
async def record_abtest_event(
    test_id: str,
    req: ABTestRecord,
    current_user: str = Depends(get_current_user)
):
    """Increment send or conversion count for A/B test variant."""
    variant = req.variant.upper()
    if variant not in ("A", "B"):
        return {"error": "variant must be 'A' or 'B'"}

    if req.event == "send":
        col = f"sends_{variant.lower()}"
    elif req.event == "conversion":
        col = f"conv_{variant.lower()}"
    else:
        return {"error": "event must be 'send' or 'conversion'"}

    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            f"UPDATE ab_tests SET {col} = {col} + 1 WHERE id = ?",
            (test_id,)
        )
        await db.commit()

    return {"success": True, "test_id": test_id, "variant": variant, "event": req.event}


@router.get("/prompt-stats/{agent_name}", summary="Get live telemetry stats for a prompt agent")
async def get_prompt_stats(agent_name: str, current_user: str = Depends(get_current_user)):
    """Return live performance data for a given agent from agent_telemetry + prompt_versions."""
    agent = agent_name.upper()
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row

        # Get all prompt versions for this agent
        versions_cur = await db.execute(
            "SELECT id, version, is_active, notes, created_at FROM prompt_versions WHERE agent_name=? ORDER BY version DESC",
            (agent,)
        )
        versions = await versions_cur.fetchall()

        # Get telemetry for this agent (last 30 days, grouped summary)
        # MAP sub-agent names to the actual workflow nodes used in agent_telemetry
        teleme_name = agent
        if agent in ["GREETING", "CLOSING", "EMAIL_DRAFT", "WHATSAPP_DRAFT", "VOICE_DRAFT"]:
            teleme_name = "DRAFT_AND_GREETING"
        
        telemetry_cur = await db.execute("""
            SELECT 
                COUNT(*) as total_runs,
                ROUND(AVG(execution_time_ms), 0) as avg_ms,
                SUM(CASE WHEN status='SUCCESS' THEN 1 ELSE 0 END) as successes,
                SUM(CASE WHEN status LIKE 'ERROR%' THEN 1 ELSE 0 END) as errors,
                AVG(tokens_input) as avg_tokens_in,
                AVG(tokens_output) as avg_tokens_out
            FROM agent_telemetry
            WHERE agent_name=? AND created_at >= datetime('now', '-30 days')
        """, (teleme_name,))
        telemetry = await telemetry_cur.fetchone()

    result_versions = []
    for v in versions:
        result_versions.append({
            "version": v["version"],
            "is_active": bool(v["is_active"]),
            "notes": v["notes"],
            "created_at": v["created_at"]
        })

    stats = dict(telemetry) if telemetry else {}

    return {
        "agent_name": agent,
        "versions": result_versions,
        "telemetry": {
            "total_runs": stats.get("total_runs", 0),
            "avg_ms": int(stats.get("avg_ms") or 0),
            "success_rate": round((stats.get("successes") or 0) / max((stats.get("total_runs") or 1), 1) * 100, 1),
            "errors": stats.get("errors", 0),
            "avg_tokens_in": int(stats.get("avg_tokens_in") or 0),
            "avg_tokens_out": int(stats.get("avg_tokens_out") or 0)
        }
    }


@router.post("/voice-message", summary="Generate voice message from text")
async def generate_voice_message(
    text: str,
    language: str = "en-IN",
    current_user: str = Depends(get_current_user)
):
    """
    Generate a voice message (TTS) from text for playback in UI.
    Returns audio data in base64 format.
    """
    from app.agents.channels.voice_agent import generate_voice_message as gen_voice
    
    try:
        if not text or len(text.strip()) == 0:
            return {"success": False, "error": "Text cannot be empty"}
        
        voice_data = await gen_voice(text, language)
        return {
            "success": True,
            "voice_message": voice_data,
            "language": language,
            "text_length": len(text)
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/agent-performance", summary="Get agent performance metrics")
async def get_agent_performance(
    time_range: str = "24h",
    current_user: str = Depends(get_current_user)
):
    """
    Calculate agent performance metrics from audit logs and workflow logs.
    
    time_range: "24h", "7d", "30d", "all"
    Returns: Per-agent metrics including success_rate, avg_response_time, error_count, escalation_rate
    """
    
    # Parse time range
    import datetime
    now = datetime.datetime.utcnow()
    if time_range == "24h":
        cutoff = now - datetime.timedelta(hours=24)
    elif time_range == "7d":
        cutoff = now - datetime.timedelta(days=7)
    elif time_range == "30d":
        cutoff = now - datetime.timedelta(days=30)
    else:  # "all"
        cutoff = datetime.datetime(2020, 1, 1)
    
    cutoff_str = cutoff.isoformat()
    
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        
        # Fetch audit logs for the time range
        cursor = await db.execute("""
            SELECT 
                triggered_by as agent_name,
                COUNT(*) as total_actions,
                SUM(CASE WHEN action_type LIKE '%ERROR%' OR action_type LIKE '%FAILED%' THEN 1 ELSE 0 END) as error_count
            FROM audit_logs
            WHERE created_at >= ?
            GROUP BY triggered_by
        """, (cutoff_str,))
        
        agent_stats = {}
        for row in await cursor.fetchall():
            agent_name = row["agent_name"] or "Unknown"
            agent_stats[agent_name] = {
                "agent_name": agent_name,
                "total_actions": row["total_actions"],
                "error_count": row["error_count"] or 0,
                "success_rate": 100.0 if (row["error_count"] or 0) == 0 else (100.0 * (row["total_actions"] - (row["error_count"] or 0)) / row["total_actions"]),
                "avg_response_time_ms": 0,  # Not tracked in current schema
                "escalation_rate": 0.0
            }
        
        # Fetch escalation counts by triggering agent (if available)
        cursor2 = await db.execute("""
            SELECT COUNT(*) as escalation_count
            FROM escalation_cases
            WHERE created_at >= ? AND status = 'OPEN'
        """, (cutoff_str,))
        
        total_escalations = (await cursor2.fetchone())["escalation_count"] if await cursor2.fetchone() else 0
        
        # Calculate escalation rates
        for agent_name in agent_stats:
            if agent_stats[agent_name]["total_actions"] > 0:
                agent_stats[agent_name]["escalation_rate"] = round(
                    (total_escalations / agent_stats[agent_name]["total_actions"]) * 100.0,
                    1
                )
        
        # Build response with summary metrics
        total_actions = sum(s["total_actions"] for s in agent_stats.values())
        total_errors = sum(s["error_count"] for s in agent_stats.values())
        overall_success_rate = 100.0 if total_errors == 0 else (100.0 * (total_actions - total_errors) / total_actions) if total_actions > 0 else 0.0
        
        return {
            "time_range": time_range,
            "summary": {
                "total_actions": total_actions,
                "total_errors": total_errors,
                "overall_success_rate": round(overall_success_rate, 1),
                "open_escalations": total_escalations
            },
            "agents": sorted(agent_stats.values(), key=lambda x: x["total_actions"], reverse=True),
            "timestamp": now.isoformat()
        }

class TwilioTestRequest(BaseModel):
    phone: str
    message: str

@router.post("/test-whatsapp", summary="Test Twilio WhatsApp message sending")
async def test_whatsapp(
    req: TwilioTestRequest,
    current_user: str = Depends(get_current_user)
):
    """Send a test WhatsApp message via Twilio."""
    try:
        from twilio.rest import Client
        import json
        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        
        to_phone = req.phone.strip()
        if not to_phone.startswith("whatsapp:"):
            to_phone = f"whatsapp:{to_phone}"
            
        if settings.twilio_content_sid:
            # Test with template vars if SID exists
            message = client.messages.create(
                from_=settings.twilio_from_number,
                to=to_phone,
                content_sid=settings.twilio_content_sid,
                content_variables=json.dumps({"1": "Test Greeting", "2": req.message, "3": "Test Closing"})
            )
        else:
            message = client.messages.create(
                body=req.message,
                from_=settings.twilio_from_number,
                to=to_phone
            )
            
        return {
            "success": True,
            "sid": message.sid,
            "status": message.status,
            "using_content_sid": bool(settings.twilio_content_sid)
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
