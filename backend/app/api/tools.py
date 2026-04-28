"""
Tool execution dispatcher for the Medi voice assistant.

Handles executing tool calls returned by the LLM, including:
  - State guards (blocking data tools before patient verification)
  - State transitions
  - Patient lookup (delegates to endpoints.internal_lookup_patient)
  - Data retrieval (appointments, prescriptions, labs, available slots)
"""

import json

from app.api.endpoints import (
    internal_lookup_patient,
    MOCK_APPOINTMENTS,
    MOCK_PRESCRIPTIONS,
    MOCK_LABS,
    MOCK_AVAILABLE_SLOTS,
)

# Tools that require a verified patient before they can be called
DATA_TOOLS = ("get_appointments", "get_prescriptions", "get_labs")


def execute_tool(tool_name: str, args: dict, verified_patient_id: str | None) -> tuple[str, dict]:
    """
    Execute a single tool call and return (result_string, state_updates).

    state_updates is a dict that may contain:
      - "current_state": new state string (from transition_state)
      - "verified_patient_id": patient ID string (from lookup_patient)
    """
    state_updates: dict = {}

    # ── State Guards ──────────────────────────────────────────────────────
    if tool_name in DATA_TOOLS and not verified_patient_id:
        result = "ACCESS DENIED: Patient identity not yet verified. You must call lookup_patient first."
        print(f"[STATE GUARD]: Blocked {tool_name} — patient not verified")
        return result, state_updates

    # ── Dispatch ──────────────────────────────────────────────────────────
    if tool_name == "transition_state":
        new_state = args.get("new_state", "GREETING")
        state_updates["current_state"] = new_state
        result = f"State transitioned to {new_state}."

    elif tool_name == "lookup_patient":
        if not args.get("dob"):
            result = "DOB not provided. Ask the patient for their date of birth."
        else:
            patient = internal_lookup_patient(args.get("name", ""), args.get("dob", ""))
            if patient:
                state_updates["verified_patient_id"] = patient["id"]
                # Auto-transition to AUTHENTICATED so the LLM doesn't need
                # to remember a separate transition_state call.
                state_updates["current_state"] = "AUTHENTICATED"
                result = json.dumps(patient)
                print(f"[SESSION]: verified_patient_id = {patient['id']}")
            else:
                result = "Patient Not Found."

    elif tool_name == "get_appointments":
        data = MOCK_APPOINTMENTS.get(args.get("patient_id"), [])
        result = json.dumps(data)
        if not data:
            state_updates["template_response"] = "You don't have any upcoming appointments. Would you like to schedule one?"
        else:
            next_apt = data[0]
            state_updates["template_response"] = (
                f"I can see you have {len(data)} upcoming appointment(s). The next one is on "
                f"{next_apt['date']} at {next_apt['time']} with {next_apt['provider']} in "
                f"{next_apt['department']}. Would you like to hear about the others, or is there anything you'd like to change?"
            )

    elif tool_name == "get_prescriptions":
        data = MOCK_PRESCRIPTIONS.get(args.get("patient_id"), [])
        result = json.dumps(data)
        if not data:
            state_updates["template_response"] = "You don't have any active prescriptions. Is there anything else I can help with?"
        else:
            rx_details = ", and ".join([f"{rx['medication']} {rx['dosage']} with {rx['refills_remaining']} refills remaining" for rx in data])
            state_updates["template_response"] = (
                f"You have {len(data)} active prescription(s): {rx_details}. "
                "Would you like to request a refill for any of these?"
            )

    elif tool_name == "get_labs":
        data = MOCK_LABS.get(args.get("patient_id"), [])
        result = json.dumps(data)
        if not data:
            state_updates["template_response"] = "I couldn't find any recent lab results. Would you like to schedule a follow-up with your provider?"
        else:
            lab = data[0]
            ref_range = lab.get("reference_range", "unknown")
            state_updates["template_response"] = (
                f"Your most recent {lab['test']} from {lab['date']} showed a result of {lab['result']}. "
                f"The reference range is {ref_range}, and the status is {lab['status']}. "
                "Would you like me to help you schedule a follow-up?"
            )

    elif tool_name == "get_available_slots":
        result = json.dumps(MOCK_AVAILABLE_SLOTS)

    else:
        result = "Unknown tool."

    return result, state_updates
