"""
System prompt builder and LLM tool definitions for the Medi voice assistant.

This module is the single source of truth for:
  - The base system prompt (voice-first rules, state context)
  - State-specific goal instructions
  - The TOOLS JSON schema array sent to Groq
"""


def get_system_prompt(current_state: str, verified_patient_id: str | None) -> str:
    """Build the system prompt dynamically based on conversation state."""
    base = (
        "You are Medi, an AI voice receptionist for Greenfield Medical Group. "
        "CRITICAL: The patient is SPEAKING to you by voice. They cannot type. "
        "NEVER mention formats like 'YYYY-MM-DD' or ask them to type anything. "
        "When they say a date aloud (e.g. 'august fourteen two thousand one'), "
        "YOU silently convert it to 2001-08-14 and use it in tool calls. "
        "Rules: "
        "1. Keep responses to 1-2 short sentences. "
        "2. Be warm and natural. "
        "3. Do not invent data. Only reference data returned by tools. "
        "4. Only answer medical administrative requests. "
        "5. NEVER provide clinical advice. "
        f"\nCURRENT CONVERSATION STATE: {current_state}\n"
    )

    if verified_patient_id:
        base += f"VERIFIED PATIENT ID: {verified_patient_id}. Use this ID for all data lookups.\n"

    state_goals = {
        "GREETING": (
            "Goal: Greet the patient warmly and ask how you can help today. "
            "Do NOT call any data tools yet. Use transition_state to VERIFICATION once they state a need."
        ),
        "VERIFICATION": (
            "Goal: Verify identity. Ask for their full name and date of birth. "
            "When they say it aloud, silently convert it to YYYY-MM-DD and call lookup_patient. "
            "NEVER tell the user what format you need. If found, call transition_state to AUTHENTICATED."
        ),
        "AUTHENTICATED": (
            "Goal: Confirm their identity. Ask what information they need "
            "(appointments, prescriptions, lab results, or scheduling). "
            "Then call transition_state to SERVICING."
        ),
        "SERVICING": (
            "Goal: Use the appropriate data tool (get_appointments, get_prescriptions, get_labs) "
            "to answer their question. If they want to book, call transition_state to SCHEDULING. "
            "If they're done, transition to CLOSING."
        ),
        "SCHEDULING": (
            "Goal: Use get_available_slots to show openings. Help them pick a slot. "
            "Then transition to CLOSING."
        ),
        "CLOSING": "Goal: Ask if they need anything else. If not, say a warm goodbye.",
    }

    return base + state_goals.get(current_state, state_goals["CLOSING"])


# ── Tool definitions sent to Groq's function-calling API ──────────────────────

TOOLS = [
    {"type": "function", "function": {
        "name": "transition_state",
        "description": "Move the conversation to a new phase. Call this to advance the workflow.",
        "parameters": {"type": "object", "properties": {
            "new_state": {
                "type": "string",
                "enum": ["GREETING", "VERIFICATION", "AUTHENTICATED",
                         "SERVICING", "SCHEDULING", "CLOSING"],
            }
        }, "required": ["new_state"]}
    }},
    {"type": "function", "function": {
        "name": "lookup_patient",
        "description": (
            "Verify patient identity. The user speaks their DOB aloud — you must convert "
            "spoken dates (e.g. 'march fifth nineteen eighty') to YYYY-MM-DD yourself "
            "before calling this tool. Never ask the user for a specific format."
        ),
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Patient full name"},
            "dob":  {"type": "string", "description": "DOB you converted to YYYY-MM-DD from their spoken date"}
        }, "required": ["name", "dob"]}
    }},
    {"type": "function", "function": {
        "name": "get_appointments",
        "description": "Retrieve appointments for a verified patient. Only call after identity is verified.",
        "parameters": {"type": "object", "properties": {
            "patient_id": {"type": "string", "description": "The verified patient ID"},
            "time_range": {
                "type": "string",
                "enum": ["upcoming", "past_30_days", "all"],
                "description": "Filter by time range. Defaults to upcoming.",
            }
        }, "required": ["patient_id"]}
    }},
    {"type": "function", "function": {
        "name": "get_prescriptions",
        "description": "Retrieve active prescriptions and refill status for a verified patient.",
        "parameters": {"type": "object", "properties": {
            "patient_id": {"type": "string", "description": "The verified patient ID"}
        }, "required": ["patient_id"]}
    }},
    {"type": "function", "function": {
        "name": "get_labs",
        "description": "Retrieve recent lab results with reference ranges for a verified patient.",
        "parameters": {"type": "object", "properties": {
            "patient_id": {"type": "string", "description": "The verified patient ID"},
            "test_name":  {"type": "string", "description": "Optional: filter by specific test name"}
        }, "required": ["patient_id"]}
    }},
    {"type": "function", "function": {
        "name": "get_available_slots",
        "description": "Find available appointment openings for scheduling.",
        "parameters": {"type": "object", "properties": {}}
    }},
]
