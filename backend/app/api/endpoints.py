from difflib import SequenceMatcher
from fastapi import APIRouter, HTTPException

router = APIRouter()
mock_router = APIRouter()

MOCK_PATIENTS = {
    "1001": {"id": "1001", "name": "Elena Smith", "dob": "1980-05-15", "primary_provider": "Dr. Sarah Jenkins", "insurance": "BlueCross", "allergies": ["Penicillin"], "active_conditions": ["Hypertension", "Asthma"]},
    "1002": {"id": "1002", "name": "Marcus Johnson", "dob": "1992-11-03", "primary_provider": "Dr. Emily Chen", "insurance": "Aetna", "allergies": ["Peanuts"], "active_conditions": []},
    "1003": {"id": "1003", "name": "Sofia Garcia", "dob": "1975-02-28", "primary_provider": "Dr. Michael Torres", "insurance": "Medicare", "allergies": ["Sulfa drugs"], "active_conditions": ["Type 2 Diabetes"]},
    "1004": {"id": "1004", "name": "James Wilson", "dob": "2001-08-14", "primary_provider": "Dr. Sarah Jenkins", "insurance": "Cigna", "allergies": ["None"], "active_conditions": ["Anxiety"]},
    "1005": {"id": "1005", "name": "Aisha Patel", "dob": "1968-09-30", "primary_provider": "Dr. Emily Chen", "insurance": "UnitedHealthcare", "allergies": ["Latex"], "active_conditions": ["Osteoarthritis"]},
    "1006": {"id": "1006", "name": "David Kim", "dob": "1985-12-05", "primary_provider": "Dr. Michael Torres", "insurance": "BlueShield", "allergies": ["Ibuprofen", "Dust"], "active_conditions": ["Insomnia", "GERD"]},
    "1007": {"id": "1007", "name": "Rachel Greene", "dob": "1995-04-18", "primary_provider": "Dr. Sarah Jenkins", "insurance": "Kaiser Permanente", "allergies": ["None"], "active_conditions": ["Migraines"]},
    "1008": {"id": "1008", "name": "Robert Taylor", "dob": "1955-07-22", "primary_provider": "Dr. Michael Torres", "insurance": "Medicare", "allergies": ["Codeine"], "active_conditions": ["Coronary Artery Disease", "High Cholesterol", "Gout"]}
}

MOCK_APPOINTMENTS = {
    "1001": [{"date": "2026-05-20", "time": "10:30 AM", "provider": "Dr. Sarah Jenkins", "department": "Primary Care", "type": "Follow-up: Hypertension"}],
    "1003": [{"date": "2026-04-18", "time": "02:15 PM", "provider": "Dr. Michael Torres", "department": "Endocrinology", "type": "Diabetes Checkup"}],
    "1006": [{"date": "2026-04-20", "time": "09:00 AM", "provider": "Dr. Emily Chen", "department": "Gastroenterology", "type": "Endoscopy Follow-up"}],
    "1007": [{"date": "2026-05-02", "time": "11:30 AM", "provider": "Dr. Sarah Jenkins", "department": "Neurology Base", "type": "Migraine Treatment Assessment"}],
    "1008": [{"date": "2026-04-22", "time": "01:00 PM", "provider": "Dr. Michael Torres", "department": "Cardiology", "type": "EKG Routine Screening"},
             {"date": "2026-05-15", "time": "03:45 PM", "provider": "Dr. Michael Torres", "department": "Primary Care", "type": "Medication Review"}]
}

MOCK_PRESCRIPTIONS = {
    "1001": [{"medication": "Lisinopril", "dosage": "10mg", "refills_remaining": 2, "provider": "Dr. Sarah Jenkins", "pharmacy": "CVS Main St", "status": "eligible"},
             {"medication": "Albuterol Inhaler", "dosage": "90mcg", "refills_remaining": 3, "provider": "Dr. Sarah Jenkins", "pharmacy": "CVS Main St", "status": "eligible"}],
    "1003": [{"medication": "Metformin", "dosage": "500mg", "refills_remaining": 0, "provider": "Dr. Michael Torres", "pharmacy": "Walgreens 1st Ave", "status": "needs_renewal"}],
    "1004": [{"medication": "Sertraline", "dosage": "50mg", "refills_remaining": 5, "provider": "Dr. Sarah Jenkins", "pharmacy": "Rite Aid Broad St", "status": "eligible"}],
    "1006": [{"medication": "Omeprazole", "dosage": "40mg", "refills_remaining": 1, "provider": "Dr. Emily Chen", "pharmacy": "Walgreens 1st Ave", "status": "eligible"},
             {"medication": "Zolpidem", "dosage": "5mg", "refills_remaining": 0, "provider": "Dr. Michael Torres", "pharmacy": "Walgreens 1st Ave", "status": "needs_renewal"}],
    "1008": [{"medication": "Atorvastatin", "dosage": "40mg", "refills_remaining": 4, "provider": "Dr. Michael Torres", "pharmacy": "CPA Pharmacy", "status": "eligible"},
             {"medication": "Allopurinol", "dosage": "100mg", "refills_remaining": 1, "provider": "Dr. Michael Torres", "pharmacy": "CPA Pharmacy", "status": "eligible"}]
}

MOCK_LABS = {
    "1001": [{"test": "Lipid Panel", "date": "2026-03-10", "result": "LDL 130 mg/dL", "reference": "< 100 mg/dL", "status": "review with provider"}],
    "1003": [{"test": "A1C", "date": "2026-04-01", "result": "7.2%", "reference": "< 5.7%", "status": "elevated"}],
    "1006": [{"test": "Helicobacter Pylori Breath Test", "date": "2026-04-05", "result": "Negative", "reference": "Negative", "status": "normal"}],
    "1008": [{"test": "Comprehensive Metabolic Panel", "date": "2026-04-10", "result": "BUN 22 mg/dL", "reference": "7-20 mg/dL", "status": "slightly elevated"},
             {"test": "Uric Acid", "date": "2026-04-10", "result": "8.5 mg/dL", "reference": "3.5-7.2 mg/dL", "status": "elevated"}]
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
    best_match = None
    best_ratio = 0.0
    for pid, p in MOCK_PATIENTS.items():
        if p["dob"] != dob:
            continue
        ratio = SequenceMatcher(None, p["name"].lower(), name.lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = p
    # 0.75 threshold: catches Sophia→Sofia, Aisha→Aysha but rejects unrelated names
    return best_match if best_ratio >= 0.75 else None
