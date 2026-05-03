# Medi - Medical Voice Assistant

## Overview
Medi is a real-time, AI-powered voice assistant developed for the Greenfield Medical Group. It integrates low-latency Speech-to-Text (Deepgram), a fast LLM (Groq / Llama-3.1), and Text-to-Speech (ElevenLabs) to provide a seamless conversational interface. Patients can securely look up their appointments, verify prescriptions, retrieve lab results, and schedule new appointments.

## System Architecture

The system leverages a WebSocket-based streaming architecture to minimize audio latency and provide real-time transcriptions. The frontend captures raw PCM audio via the Web Audio API and streams it to the backend, which acts as the central router between the STT, LLM, and TTS services.

```mermaid
sequenceDiagram
    actor User
    participant App as Frontend
    participant API as Backend
    participant Deepgram as STT
    participant Groq as LLM
    participant TTS as ElevenLabs

    User->>App: Speaks
    App->>API: Stream PCM Audio
    API->>Deepgram: Forward Audio
    Deepgram-->>API: Interim Transcripts
    API-->>App: Update UI
    
    Deepgram->>API: UtteranceEnd (Final Transcript)
    
    Note over API,Groq: Phase 1: Tool Orchestration
    API->>Groq: Prompt + Tools + Transcript
    Groq-->>API: Tool Request (e.g., lookup_patient)
    API->>API: Execute Local Tool
    API->>Groq: Return Tool Data
    Groq->>API: Final Text Response
    
    Note over API,TTS: Phase 2: Audio Generation
    API->>App: Update UI (Text)
    API->>TTS: Stream Final Text
    TTS-->>API: Stream Audio Chunks
    API-->>App: Forward Audio Chunks
    App->>User: Play Audio
```

## State Machine

To enforce strict conversational flows, minimize hallucinations, and restrict tool access, the backend utilizes a State Machine. The LLM's system prompt and its available tools are dynamically updated depending on the active state.

```mermaid
stateDiagram-v2
    direction TB
    [*] --> GREETING
    
    GREETING --> VERIFICATION : user_states_need
    note right of GREETING
      Context: Awaiting user intent.
      Allowed Tools: transition_state
    end note
    
    VERIFICATION --> AUTHENTICATED : identity_verified
    note right of VERIFICATION
      Context: Requires Name AND Date of Birth.
      Allowed Tools: lookup_patient
    end note
    
    AUTHENTICATED --> SERVICING : data_requested
    note right of AUTHENTICATED
      Context: Ready for queries.
      Allowed Tools: get_appointments, get_prescriptions, etc.
    end note
    
    SERVICING --> SCHEDULING : appointment_requested
    SERVICING --> SERVICING : more_data_requested
    SERVICING --> CLOSING : user_finished
    note right of SERVICING
      Context: Presenting requested EHR data.
      Allowed Tools: All data retrieval tools
    end note
    
    SCHEDULING --> CLOSING : appointment_confirmed
    note right of SCHEDULING
      Context: Selecting a slot.
      Allowed Tools: transition_state
    end note
    
    CLOSING --> [*]
    CLOSING --> SERVICING : user_has_more_questions
```

## Security & Guardrails

- **Pre-LLM Safety Classifier:** A deterministic regex-based classifier intercepts emergency keywords (e.g., "heart attack", "suicide"). If triggered, the system immediately bypasses the LLM and streams an emergency redirect response.
- **Post-LLM Response Validation:** The orchestrator cross-references the LLM's generated response against the actual tool outputs. If the LLM hallucinates sensitive clinical data (such as a medication or lab test not returned by the database), the response is overridden with a generic fallback.
- **State-based Authorization:** Data retrieval tools (`get_appointments`, `get_prescriptions`, `get_labs`) are strictly blocked at the dispatcher level (`tools.py`) unless a valid `verified_patient_id` has been set in the session state.

## Known Issues & Limitations

- **Latency & Tool Thrashing:** The system can experience slow response times because the LLM occasionally makes redundant or excessive tool calls (such as transitioning states multiple times unnecessarily) before arriving at a final text response.
- **Data Leakage during Verification:** The LLM sometimes leaks PII prematurely. For example, if the user only provides a name (e.g., "James Wilson"), the LLM may hallucinate or infer the date of birth from its training data/context without explicitly asking the user to provide it.

## Key Components

### Backend (`/backend/app/api`)
- `orchestrator.py`: Manages the LLM conversation loop, malformed JSON recovery, hallucinatory XML tag parsing, and filler phrase dispatching.
- `websocket.py`: Main FastAPI entry point handling the concurrent binary streams between the frontend, Deepgram, and ElevenLabs.
- `prompts.py`: Dynamic prompt generation based on the active state machine phase.
- `tools.py`: Function definitions and dispatcher for the mock EHR database.
- `guardrails.py`: Fast pre-processing and post-processing safety checks.

### Frontend (`/app`)
- `hooks/useVoiceSession.ts`: React hook managing the Web Audio API, VAD (Voice Activity Detection), and WebSocket connection.
- `lib/audioManager.ts`: Handles PCM chunk scheduling for gapless audio playback and immediate interruptions.
- `components/`: UI components for the chat panel and real-time waveform visualizer.