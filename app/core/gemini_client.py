"""
Centralized async Gemini client for all agents.
Returns token usage data for telemetry.
"""
import google.generativeai as genai
import asyncio
import json
import re
import time
from dataclasses import dataclass
from app.core.config import get_settings
import os
from dotenv import load_dotenv
load_dotenv()

settings = get_settings()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

_model = None


@dataclass
class LLMResult:
    """Wraps an LLM response with telemetry data."""
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: float = 0.0


def get_model():
    global _model
    if _model is None:
        _model = genai.GenerativeModel(settings.gemini_model)
    return _model


async def call_llm_raw(
    system_prompt: str,
    user_prompt: str,
    expect_json: bool = False,
    temperature: float = 0.3
) -> LLMResult:
    """Async wrapper that returns LLMResult with token usage and timing."""
    model = get_model()
    full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"

    t_start = time.monotonic()
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: model.generate_content(
            full_prompt,
            generation_config=genai.GenerationConfig(
                temperature=temperature,
                max_output_tokens=2048,
            )
        )
    )
    duration_ms = (time.monotonic() - t_start) * 1000

    text = response.text.strip()
    if expect_json:
        text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("```").strip()

    # Extract token usage from Gemini response metadata
    tokens_in = 0
    tokens_out = 0
    try:
        usage = response.usage_metadata
        tokens_in = usage.prompt_token_count or 0
        tokens_out = usage.candidates_token_count or 0
    except Exception:
        pass

    return LLMResult(text=text, tokens_in=tokens_in, tokens_out=tokens_out, duration_ms=duration_ms)


async def call_llm(
    system_prompt: str,
    user_prompt: str,
    expect_json: bool = False,
    temperature: float = 0.3
) -> str:
    """Backward-compatible: returns plain text."""
    result = await call_llm_raw(system_prompt, user_prompt, expect_json, temperature)
    return result.text


async def call_llm_json(system_prompt: str, user_prompt: str) -> dict:
    """Call LLM expecting JSON response, returns parsed dict."""
    text = await call_llm(system_prompt, user_prompt, expect_json=True)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
        return {"error": "Could not parse JSON", "raw": text}
