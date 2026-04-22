"""
Safety guardrails for the Medi voice assistant.

Two layers:
  1. Pre-LLM safety classifier — intercepts emergencies and clinical questions
     before the LLM even sees the user's message.
  2. Post-LLM response validator — cross-checks the LLM's final response
     against actual tool results to catch hallucinated medications or lab tests.
"""

from app.api.endpoints import MOCK_PRESCRIPTIONS, MOCK_LABS

# ── Pre-LLM Safety Classifier ────────────────────────────────────────────────

EMERGENCY_KEYWORDS = [
    "suicide", "chest pain", "heart attack", "can't breathe",
    "emergency", "911", "bleeding out", "stroke", "overdose",
    "killing myself", "want to die",
]

CLINICAL_KEYWORDS = [
    "dosage", "should i take", "side effects", "diagnosis",
    "does this mean i have", "am i sick", "what is this pill",
    "is this cancer", "prescribe me",
]

EMERGENCY_RESPONSE = (
    "I'm a virtual assistant and cannot help with medical emergencies. "
    "If you are experiencing an emergency, please hang up and call 911 immediately."
)

CLINICAL_INJECTION = (
    "The patient is asking a clinical question. You are NOT a medical professional. "
    "Respond with empathy, do not provide diagnosis or treatment advice, and "
    "direct them to contact their provider."
)

HALLUCINATION_FALLBACK = (
    "I found your records, but let me make sure I give you accurate information. "
    "Could you please specify what you'd like to know? "
    "I can look up your appointments, prescriptions, or lab results."
)


def classify_safety(text: str) -> tuple[str | None, str | None]:
    """
    Check user input for emergency or clinical keywords.

    Returns (emergency_response, clinical_prompt_injection).
    If emergency_response is set, the LLM should be skipped.
    If clinical_prompt_injection is set, it should be appended to the system prompt.
    """
    text_lower = text.lower()
    if any(kw in text_lower for kw in EMERGENCY_KEYWORDS):
        return EMERGENCY_RESPONSE, None
    
    # Medication interaction questions
    if "can i take" in text_lower and "with" in text_lower or "interaction between" in text_lower:
        return "For questions about medication interactions, please speak directly with your pharmacist or provider.", None

    if any(kw in text_lower for kw in CLINICAL_KEYWORDS):
        return None, CLINICAL_INJECTION
        
    return None, None


def validate_response(full_response: str, tool_results: dict[str, str]) -> str | None:
    """
    Cross-check the LLM's response against actual tool data.

    Returns a safe fallback string if a hallucination is found, or None if clean.
    """
    if not tool_results:
        return None

    combined_tool_data = " ".join(tool_results.values()).lower()
    response_lower = full_response.lower()

    # Check medications
    all_meds: set[str] = set()
    for lst in MOCK_PRESCRIPTIONS.values():
        for rx in lst:
            all_meds.add(rx["medication"].lower())
    for med in all_meds:
        if med in response_lower and med not in combined_tool_data:
            print(f"[GUARDRAIL]: Hallucinated medication '{med}' not in tool results")
            return HALLUCINATION_FALLBACK

    # Check lab test names
    all_tests: set[str] = set()
    for lst in MOCK_LABS.values():
        for lab in lst:
            all_tests.add(lab["test"].lower())
    for test in all_tests:
        if test in response_lower and test not in combined_tool_data:
            print(f"[GUARDRAIL]: Hallucinated lab test '{test}' not in tool results")
            return HALLUCINATION_FALLBACK

    return None
