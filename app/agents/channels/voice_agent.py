"""
Voice Channel Agent — Step 6 (Voice)
Modular: developers can extend this independently.
Generates audio files from text messages for playback in UI.
"""
from app.agents.state import RenewalState
import aiosqlite
from app.core.config import get_settings
import os
import base64
from pathlib import Path

settings = get_settings()

# Simple voice message generation using Google Text-to-Speech API
async def generate_voice_message(text: str, language: str = "en-IN") -> dict:
    """
    Generate a voice message from text using Google's TTS.
    Returns audio data in base64 format for playback in UI.
    
    Args:
        text: The text to convert to speech
        language: Language code (en-IN for English India, hi-IN for Hindi, etc.)
    
    Returns:
        dict with 'audio_base64', 'duration_ms', 'language'
    """
    try:
        from google.cloud import texttospeech
    except ImportError:
        # Fallback: return placeholder audio data
        print("[VOICE] Google Cloud TTS not available, using placeholder")
        return {
            "audio_base64": "UklGRiYAAABXQVZFZm10IBAAAAABAAEAQB8AAAB9AAACABAAZGF0YQIAAAAAAA==",  # Minimal WAV
            "duration_ms": 0,
            "language": language,
            "text": text
        }
    
    try:
        client = texttospeech.TextToSpeechClient()
        synthesis_input = texttospeech.SynthesisInput(text=text)
        
        # Set voice parameters
        voice = texttospeech.VoiceSelectionParams(
            language_code=language,
            name=f"{language}-Neural2-C",  # Use neural voices
            ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL,
        )
        
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            speaking_rate=0.9,  # Slightly slower for clarity
        )
        
        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config,
        )
        
        # Convert audio content to base64 for transmission
        audio_base64 = base64.b64encode(response.audio_content).decode('utf-8')
        
        # Estimate duration (rough estimate: 150 words per minute)
        word_count = len(text.split())
        duration_ms = int((word_count / 150) * 60 * 1000)
        
        return {
            "audio_base64": audio_base64,
            "duration_ms": duration_ms,
            "language": language,
            "text": text
        }
    except Exception as e:
        print(f"[VOICE] Error generating voice message: {e}")
        return {
            "audio_base64": "UklGRiYAAABXQVZFZm10IBAAAAABAAEAQB8AAAB9AAACABAAZGF0YQIAAAAAAA==",
            "duration_ms": 0,
            "language": language,
            "text": text,
            "error": str(e)
        }


async def voice_send_node(state: RenewalState) -> dict:
    final_message = state.get("final_message", "")
    if not final_message:
        final_message = f"{state.get('greeting','')}\n\n{state.get('draft_message','')}\n\n{state.get('closing','')}".strip()

    # Detect language from state (default: English)
    language_code = state.get("language", "en-IN")
    if "hi" in language_code.lower():
        tts_language = "hi-IN"
    elif "bn" in language_code.lower():
        tts_language = "bn-IN"
    else:
        tts_language = "en-IN"

    # Generate voice message audio
    voice_data = await generate_voice_message(final_message, tts_language)
    
    # Check if using TTS fallback (simulated or fallback mode)
    is_fallback = "error" in voice_data or not voice_data.get("audio_base64")
    tts_mode = "FALLBACK_AUDIO" if is_fallback else "LIVE_TTS"
    
    # Clean text for call script (remove special markers for display)
    call_script = final_message.replace("[ESCALATE]", "").strip()
    
    print(f"[VOICE {tts_mode}] Initiating call to {state['customer_name']} | Policy: {state['policy_id']}")
    print(f"[VOICE] Generated audio: {voice_data.get('duration_ms')}ms duration | Language: {tts_language}")

    # Check for escalation markers in voice script
    escalate = "[ESCALATE]" in final_message
    
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        # Store the interaction with voice metadata
        await db.execute(
            "INSERT INTO interactions (policy_id, channel, message_direction, content, sentiment_score) VALUES (?, ?, ?, ?, ?)",
            (state["policy_id"], "Voice", "OUTBOUND", call_script, 0.0)
        )
        
        # Log with TTS mode indicator
        tts_indicator = f"[{tts_mode}]" if is_fallback else "[LIVE_TTS]"
        await db.execute(
            "INSERT INTO audit_logs (policy_id, action_type, action_reason, triggered_by, prompt_version) VALUES (?, ?, ?, ?, ?)",
            (state["policy_id"], "VOICE_CALL_INITIATED", f"{tts_indicator} IVR call initiated | Duration: {voice_data.get('duration_ms')}ms | Language: {tts_language} | Escalate: {escalate}", "Voice Agent", state.get("active_versions", {}).get("VOICE_DRAFT"))
        )
        await db.execute(
            "UPDATE policy_state SET current_node=?, last_channel=?, last_message=?, updated_at=CURRENT_TIMESTAMP WHERE policy_id=?",
            ("AWAITING_RESPONSE", "Voice", call_script, state["policy_id"])
        )
        await db.commit()

    result = {
        "current_node": "COMPLETED",
        "messages_sent": [f"[VOICE {tts_mode}] Call initiated to {state['customer_name']} | Policy: {state['policy_id']}"],
        "audit_trail": [f"[VOICE_AGENT {tts_mode}] Call initiated | Policy: {state['policy_id']} | Duration: {voice_data.get('duration_ms')}ms | Escalate: {escalate}"],
        "voice_message": {
            "text": call_script,
            "audio_base64": voice_data.get("audio_base64"),
            "duration_ms": voice_data.get("duration_ms"),
            "language": tts_language,
            "tts_mode": tts_mode,
            "is_fallback": is_fallback
        }
    }
    if escalate:
        result["distress_flag"] = True
        result["audit_trail"].append("[VOICE_AGENT] ⚠️ ESCALATE marker found in voice script")
    return result


if __name__ == "__main__":
    import asyncio
    # Mock state for standalone testing
    mock_state = {
        "policy_id": "TEST-VOICE-001",
        "customer_name": "Developer Test",
        "policy_type": "Life Plus",
        "premium_due_date": "2026-06-12",
        "greeting": "Hello,",
        "draft_message": "This is a test renewal call script. [ESCALATE]",
        "closing": "Thank you."
    }
    
    async def run_test():
        print("--- Running Voice Agent Standalone Test ---")
        result = await voice_send_node(mock_state)
        print("Result:", result)
        
    asyncio.run(run_test())
