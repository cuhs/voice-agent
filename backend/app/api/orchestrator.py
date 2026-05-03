"""
LLM orchestration loop (Phase 1) for the Medi voice assistant.

Responsibilities:
  - Multi-round tool-calling loop with Groq (up to 5 rounds) to handle complex reasoning.
  - Hallucinated XML tag extraction and cleanup (since the model sometimes outputs <tool> tags).
  - Malformed JSON recovery from tool_use_failed errors (to make the agent more robust).
  - Dynamic temperature selection based on conversation state (warm for chat, cold for data).
  - Filler phrase dispatch to mask tool-call latency (keeps the user engaged).
  - Mid-execution interrupt support via cancel_event (allows the user to cut the bot off).
"""

import json
import random
import re
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from openai import AsyncOpenAI

from app.api.prompts import TOOLS, get_system_prompt, get_tools_for_state
from app.api.tools import execute_tool
from app.api.guardrails import classify_safety, validate_response
from app.core.config import settings

# ── Groq LLM Client ──────────────────────────────────────────────────────────
# We use the AsyncOpenAI client configured to point to Groq's OpenAI-compatible API.
# This allows us to use standard OpenAI libraries while taking advantage of Groq's fast inference.

llm_client = AsyncOpenAI(
    api_key=settings.groq_api_key,
    base_url="https://api.groq.com/openai/v1",
)

# ── Regex patterns for hallucinated tool tags (with or without closing tags) ──
# Sometimes the LLM fails to use the native function calling API and instead 
# hallucinates XML tags in its text response. We use regex to catch these 
# and convert them into actual tool calls.

_PATTERN_TAG   = r'<([a-zA-Z_]+)>(\s*\{.*?\}\s*)(?:</[^>]*>)?'
_PATTERN_FUNC  = r'<function=([a-zA-Z_]+)>(\s*\{.*?\}\s*)(?:</function[^>]*>)?'

# Limit the number of consecutive tool calls to prevent infinite loops.
MAX_TOOL_ROUNDS = 5

# ── Filler phrases to mask tool-call latency ─────────────────────────────────
# Because database lookups (and the subsequent LLM calls) take time, we 
# instantly play a filler phrase to let the user know we are working on their request.

FILLER_PHRASES = [
    "Let me check on that.",
    "One moment, please.",
    "Looking that up for you.",
    "Let me pull that up.",
    "Just a moment.",
    "Bear with me one second.",
]

# Tools where filler is unnecessary or counterproductive.
# - transition_state: instant, no data lookup, so adding a filler would feel unnatural.
# - Data tools (get_appointments, etc.): return a `template_response` immediately 
#   in this architecture, so filler would play right before the answer instead of masking latency.
# Only lookup_patient benefits from filler since it triggers another LLM call to process the result.
_SKIP_FILLER_TOOLS = {
    "transition_state",
    "get_appointments",
    "get_prescriptions",
    "get_labs",
    "get_available_slots",
}


def _next_filler() -> str:
    """Pick a random filler phrase from the list to keep responses varied."""
    return random.choice(FILLER_PHRASES)


def _fix_malformed_json(raw: str) -> str:
    """
    Fix unquoted JSON keys/values like {new_state: VERIFICATION}.
    The LLM sometimes returns invalid JSON syntax. This regex pipeline 
    attempts to add quotes around unquoted alphanumeric keys and values 
    to make the JSON parseable by standard libraries.
    """
    fixed = re.sub(r'(?<=[{,])\s*(\w+)\s*:', r' "\1":', raw)
    fixed = re.sub(r':\s*(?!")([A-Za-z_][A-Za-z_0-9]*)\s*(?=[,}])', r': "\1"', fixed)
    return fixed


def _extract_hallucinated_tools(content: str) -> tuple[str, list[dict]]:
    """
    Scan LLM content for hallucinated XML tool tags, extract them,
    and return (cleaned_content, extracted_tool_list).
    
    This function applies the regex patterns defined above, cleans up the text,
    fixes any JSON formatting issues in the arguments, and formats them
    into a structured list of tool calls simulating native API behavior.
    """
    clean = content
    tools: list[dict] = []

    for pattern in (_PATTERN_TAG, _PATTERN_FUNC):
        for match in re.finditer(pattern, content, re.DOTALL):
            clean = clean.replace(match.group(0), "")
            fixed_args = _fix_malformed_json(match.group(2))
            tools.append({
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "name": match.group(1),
                "arguments": fixed_args,
            })

    return clean, tools


# Type alias for the filler callback the websocket layer provides.
# Signature: async def send_filler(phrase: str) -> None
FillerCallback = Callable[[str], Coroutine[Any, Any, None]]


async def run_orchestration(
    messages: list[dict],
    current_state: str,
    verified_patient_id: str | None,
    user_text: str,
    filler_callback: FillerCallback | None = None,
    cancel_event: "asyncio.Event | None" = None,
) -> tuple[str, str, str | None]:
    """
    Run the full Phase 1 orchestration loop.

    This is the core "brain" of the agent. It manages the conversational turns, 
    makes tool calls, handles state transitions, and enforces guardrails.

    Args:
        messages:            Mutable conversation history (modified in-place).
        current_state:       Current state machine state (e.g., GREETING, SERVICING).
        verified_patient_id: Patient ID if verified, else None.
        user_text:           The user's transcribed utterance from STT.
        filler_callback:     Optional async callable — invoked with a filler
                             phrase string when a data tool call is detected,
                             before execution begins. The websocket layer
                             uses this to immediately stream filler audio to
                             the frontend so the user hears continuous speech.
        cancel_event:        Optional asyncio.Event — set by the websocket
                             receiver when the user interrupts. Checked
                             between orchestration steps so we can bail early
                             instead of running stale work.

    Returns:
        (final_response, updated_state, updated_patient_id)
    """
    import asyncio  # local import to keep module-level light

    def _is_cancelled() -> bool:
        """Helper to check if the user has interrupted the bot."""
        return cancel_event is not None and cancel_event.is_set()

    # ── Pre-LLM safety check ─────────────────────────────────────────────
    # Run the user input through a fast, deterministic regex classifier before 
    # hitting the LLM. This catches emergencies immediately.
    safety_response, safety_injection = classify_safety(user_text)
    if safety_response:
        messages.append({"role": "assistant", "content": safety_response})
        return safety_response, current_state, verified_patient_id

    # ── Update system prompt for current state ───────────────────────────
    # The system prompt is dynamic. It changes instructions depending on the 
    # current state (e.g., in GREETING it instructs the bot to be brief, 
    # in VERIFICATION it instructs the bot to gather name and DOB).
    messages[0]["content"] = get_system_prompt(current_state, verified_patient_id)
    if safety_injection:
        messages[0]["content"] += "\n\n" + safety_injection

    full_response = ""
    last_tool_results: dict[str, str] = {}

    # The multi-round tool-calling loop. This allows the LLM to call a tool, 
    # get the result, and then call another tool or generate a final response.
    for _ in range(MAX_TOOL_ROUNDS):
        # ── Check for interrupt before each LLM round ────────────────
        if _is_cancelled():
            print("[Orchestrator] Cancelled before LLM call.")
            return "", current_state, verified_patient_id

        print(f"\n[Phase 1] Calling LLM (state={current_state}, with tools)...")

        # Dynamic temperature: warmer (more creative) for chat states like GREETING, 
        # colder (more deterministic) for data-heavy states to prevent hallucinations.
        temp = 0.4 if current_state in ("GREETING", "CLOSING") else 0.0

        extracted_tools: list[dict] = []
        content = ""
        clean_content = ""

        try:
            # Main LLM generation call
            completion = await llm_client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=messages,
                tools=get_tools_for_state(current_state),
                tool_choice="auto",
                temperature=temp,
            )
            choice = completion.choices[0]

            # Parse native tool calls from the API
            if choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    extracted_tools.append({
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    })

            content = choice.message.content or ""
            clean_content = content

        except Exception as api_err:
            # ── Recover from Groq tool_use_failed errors ─────────────
            # Sometimes Groq fails to return a valid JSON for the tool call, 
            # resulting in a 400 error. The error message often contains the 
            # failed raw generation text. We extract this text and try to parse it.
            error_body = getattr(api_err, "body", None)
            failed_gen = None

            if isinstance(error_body, dict):
                inner = error_body.get("error", error_body)
                if inner.get("code") == "tool_use_failed":
                    failed_gen = inner.get("failed_generation", "")

            if failed_gen is None:
                err_str = str(api_err)
                if "tool_use_failed" in err_str:
                    fg_match = re.search(
                        r"'failed_generation':\s*'(.*?)'(?:\s*})", err_str, re.DOTALL
                    )
                    failed_gen = fg_match.group(1) if fg_match else ""

            if failed_gen is not None:
                print("[Recovering from tool_use_failed]: parsing failed generation")
                content = failed_gen
                clean_content = content
            else:
                raise

        # ── Extract hallucinated XML tool tags from content ──────────
        # Process the content (either from a successful generation or a recovered failed generation)
        # to find any hallucinated XML tags and add them to our list of tool calls.
        clean_content, hallucinated_tools = _extract_hallucinated_tools(content)
        extracted_tools.extend(hallucinated_tools)

        if clean_content.strip():
            full_response = clean_content.strip() + " "

        # ── Execute tools or break ───────────────────────────────────
        if extracted_tools:
            # If the LLM requested tools, we must append its request to the message history
            # so the subsequent API call has the context of what it just did.
            tcs_for_history = []
            for tc in extracted_tools:
                tcs_for_history.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                })

            assist_msg: dict = {"role": "assistant"}
            if clean_content.strip():
                assist_msg["content"] = clean_content.strip()
            assist_msg["tool_calls"] = tcs_for_history
            messages.append(assist_msg)

            # ── Filler phrase: send immediately for data tools ────────
            # Only send filler when at least one tool is a "real" data lookup 
            # (not a simple instant state transition). This masks the latency of the tool execution.
            needs_filler = any(
                tc["name"] not in _SKIP_FILLER_TOOLS for tc in extracted_tools
            )
            if needs_filler and filler_callback:
                phrase = _next_filler()
                print(f"[Filler] Sending: \"{phrase}\"")
                try:
                    await filler_callback(phrase)
                except Exception as e:
                    print(f"[Filler] Callback error (non-fatal): {e}")

            # ── Check for interrupt after filler, before tool exec ───
            if _is_cancelled():
                print("[Orchestrator] Cancelled after filler, before tool exec.")
                return "", current_state, verified_patient_id

            # Execute all tools requested by the LLM
            for tc in extracted_tools:
                tool_name = tc["name"]
                try:
                    args = json.loads(tc["arguments"])
                except Exception:
                    args = {}

                print(f"[TOOL EXECUTED]: {tool_name}({args})")
                
                # Execute the actual logic in tools.py
                result, state_updates = execute_tool(tool_name, args, verified_patient_id)

                # Apply state updates returned by the tool
                if "current_state" in state_updates:
                    current_state = state_updates["current_state"]
                    # Update system prompt whenever state changes
                    messages[0]["content"] = get_system_prompt(current_state, verified_patient_id)
                    if safety_injection:
                        messages[0]["content"] += "\n\n" + safety_injection
                if "verified_patient_id" in state_updates:
                    verified_patient_id = state_updates["verified_patient_id"]
                    # Update system prompt whenever patient ID changes
                    messages[0]["content"] = get_system_prompt(current_state, verified_patient_id)
                    if safety_injection:
                        messages[0]["content"] += "\n\n" + safety_injection

                last_tool_results[tool_name] = result
                # Append the tool's result to the message history so the LLM can read it
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": tool_name,
                    "content": result,
                })

                # If the tool provides a template_response, we bypass another LLM round
                # and immediately return this deterministic string. This reduces latency 
                # and prevents hallucinations for highly sensitive data reads.
                if "template_response" in state_updates:
                    full_response = state_updates["template_response"]
                    messages.append({"role": "assistant", "content": full_response})
                    return full_response, current_state, verified_patient_id

            # ── Check for interrupt after tool exec, before next LLM ─
            if _is_cancelled():
                print("[Orchestrator] Cancelled after tool exec.")
                return "", current_state, verified_patient_id

            continue  # Loop back for next LLM round to let it interpret the tool results
        else:
            # If no tools were called, this is the final textual response.
            if clean_content.strip():
                messages.append({"role": "assistant", "content": clean_content.strip()})
            break

    if not full_response.strip():
        print("[Phase 1] No final text response from LLM.")
        return "", current_state, verified_patient_id

    # ── Post-LLM response validation ─────────────────────────────────────
    # Run the final text response through our hallucination checker. 
    # If it mentions a medication or test not present in the tool results, 
    # replace the response with a safe fallback.
    hallucination_replacement = validate_response(full_response, last_tool_results)
    if hallucination_replacement:
        full_response = hallucination_replacement
        if messages and messages[-1].get("role") == "assistant":
            messages[-1]["content"] = full_response
        print("[GUARDRAIL]: Response replaced with safe fallback")

    return full_response, current_state, verified_patient_id
