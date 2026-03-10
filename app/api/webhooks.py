from fastapi import APIRouter, Request, BackgroundTasks
import aiosqlite
from app.core.config import get_settings
from app.utils.logger import logger
from datetime import datetime

router = APIRouter()
settings = get_settings()

@router.post("/sendgrid")
async def sendgrid_webhook(request: Request):
    """
    Handle SendGrid Event Webhooks.
    Docs: https://www.twilio.com/docs/sendgrid/for-developers/tracking-events/event
    """
    events = await request.json()
    
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        for event in events:
            event_type = event.get("event")
            # SendGrid returns sg_message_id which often has a part before the first dot
            # that matches the X-Message-Id header.
            sg_message_id = event.get("sg_message_id", "")
            
            # Shorten the ID if it contains a dot (SendGrid internal format)
            clean_id = sg_message_id.split('.')[0] if '.' in sg_message_id else sg_message_id

            if event_type == "open":
                timestamp = datetime.utcnow().isoformat()
                logger.info(f"[WEBHOOK] Email OPENED: {clean_id}")
                
                await db.execute(
                    "UPDATE interactions SET opened_at = ? WHERE message_id = ? OR message_id = ?",
                    (timestamp, clean_id, sg_message_id)
                )
        
        await db.commit()
    
    return {"status": "processed"}
