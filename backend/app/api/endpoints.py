from fastapi import APIRouter, HTTPException

router = APIRouter()
mock_router = APIRouter()

MOCK_PATIENTS = {
    "1001": {"id": "1001", "name": "Elena Smith", "dob": "1980-05-15", "primary_provider": "Dr. Sarah Jenkins", "insurance": "BlueCross", "allergies": ["Penicillin"], "active_conditions": ["Hypertension", "Asthma"]},
    "1002": {"id": "1002", "name": "Marcus Johnson", "dob": "1992-11-03", "primary_provider": "Dr. Emily Chen", "insurance": "Aetna", "allergies": ["Peanuts"], "active_conditions": []},
    "1003": {"id": "1003", "name": "Sofia Garcia", "dob": "1975-02-28", "primary_provider": "Dr. Michael Torres", "insurance": "Medicare", "allergies": ["Sulfa drugs"], "active_conditions": ["Type 2 Diabetes"]},
    "1004": {"id": "1004", "name": "James Wilson", "dob": "2001-08-14", "primary_provider": "Dr. Sarah Jenkins", "insurance": "Cigna", "allergies": ["None"], "active_conditions": ["Anxiety"]},
    "1005": {"id": "1005", "name": "Aisha Patel", "dob": "1968-09-30", "primary_provider": "Dr. Emily Chen", "insurance": "UnitedHealthcare", "allergies": ["Latex"], "active_conditions": ["Osteoarthritis"]}
}

MOCK_APPOINTMENTS = {
    "1001": [{"date": "2026-05-20", "time": "10:30 AM", "provider": "Dr. Sarah Jenkins", "department": "Primary Care", "type": "Follow-up: Hypertension"}],
    "1003": [{"date": "2026-04-18", "time": "02:15 PM", "provider": "Dr. Michael Torres", "department": "Endocrinology", "type": "Diabetes Checkup"}]
}

MOCK_PRESCRIPTIONS = {
    "1001": [{"medication": "Lisinopril", "dosage": "10mg", "refills_remaining": 2, "provider": "Dr. Sarah Jenkins", "pharmacy": "CVS Main St", "status": "eligible"}],
    "1003": [{"medication": "Metformin", "dosage": "500mg", "refills_remaining": 0, "provider": "Dr. Michael Torres", "pharmacy": "Walgreens 1st Ave", "status": "needs_renewal"}],
    "1004": [{"medication": "Sertraline", "dosage": "50mg", "refills_remaining": 5, "provider": "Dr. Sarah Jenkins", "pharmacy": "Rite Aid Broad St", "status": "eligible"}],
}

MOCK_LABS = {
    "1001": [{"test": "Lipid Panel", "date": "2026-03-10", "result": "LDL 130 mg/dL", "reference": "< 100 mg/dL", "status": "review with provider"}],
    "1003": [{"test": "A1C", "date": "2026-04-01", "result": "7.2%", "reference": "< 5.7%", "status": "elevated"}]
}

MOCK_AVAILABLE_SLOTS = [
    {"date": "2026-04-20", "time": "09:00 AM", "provider": "Dr. Sarah Jenkins", "department": "Primary Care"},
    {"date": "2026-04-20", "time": "11:00 AM", "provider": "Dr. Emily Chen", "department": "Primary Care"},
    {"date": "2026-04-21", "time": "02:00 PM", "provider": "Dr. Michael Torres", "department": "Endocrinology"}
]

@router.get("/status")
async def get_status():
    return {"status": "ok", "message": "API is running"}

@mock_router.get("/api/patients/{patient_id}")
async def get_patient(patient_id: str):
    if patient_id not in MOCK_PATIENTS:
        raise HTTPException(status_code=404, detail="Patient not found")
    return MOCK_PATIENTS[patient_id]

@mock_router.get("/api/patients/{patient_id}/appointments")
async def get_appointments(patient_id: str):
    return MOCK_APPOINTMENTS.get(patient_id, [])

@mock_router.get("/api/patients/{patient_id}/prescriptions")
async def get_prescriptions(patient_id: str):
    return MOCK_PRESCRIPTIONS.get(patient_id, [])

@mock_router.get("/api/patients/{patient_id}/labs")
async def get_labs(patient_id: str):
    return MOCK_LABS.get(patient_id, [])

@mock_router.get("/api/appointments/available")
async def get_available_slots():
    return MOCK_AVAILABLE_SLOTS

def internal_lookup_patient(name: str, dob: str):
    for pid, p in MOCK_PATIENTS.items():
        if p["name"].lower() == name.lower() and p["dob"] == dob:
            return p
    return None
