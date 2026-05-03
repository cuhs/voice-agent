# Voice Agent Execution Walkthrough

This walkthrough traces the exact path of data through the system from the moment you click "Start Voice Assistant" and begin speaking, all the way to the bot playing audio back to you.

---

### 1. The User Clicks Start & Microphone Access
When you click "Start Voice Assistant" in the UI, it fires `handleToggle` in `app/page.tsx`, which calls the `startRecording` function from the `useVoiceSession` hook.

Inside the `useVoiceSession` hook (`app/hooks/useVoiceSession.ts`), `startRecording` executes:
1. Opens a WebSocket connection to the Python backend via `connectWebSocket`.
2. Requests browser microphone permissions via `navigator.mediaDevices.getUserMedia`.
3. Initializes a local **VAD (Voice Activity Detection)** model via ONNX. This runs entirely in the browser.
4. Initializes the `AudioContext` and an `AudioWorkletNode` (loaded from `audio-processor.js`), which taps directly into your microphone stream.

### 2. Audio Capture and Streaming
As you speak, the `AudioWorkletNode` receives raw `Float32` audio data from your microphone. 
- Inside the `workletNode.port.onmessage` event listener in `useVoiceSession.ts`, we iterate over this array of floats and convert them into 16-bit PCM binary integers (`Int16Array`). This format is required by Deepgram.
- These binary chunks are immediately sent across the WebSocket to the backend.

### 3. Backend Routing to Deepgram (STT)
In the FastAPI backend, the connection is handled by the `websocket_audio_endpoint` function in `backend/app/api/websocket.py`.
- The backend opens a secondary WebSocket to Deepgram's API.
- It spins up an async `receiver()` function. This task sits in an infinite loop listening to the frontend. Whenever it receives binary audio bytes (`if "bytes" in data`), it forwards them blindly to Deepgram.

### 4. Real-time Transcripts
Simultaneously, an async `sender()` function loops, listening for responses *from* Deepgram.
- As you speak, Deepgram streams back JSON containing `Results` with `is_final: False`. These are "interim transcripts" (the bot guessing what you're saying before you finish).
- The `sender` function forwards these JSON messages back to the frontend, which updates the `interimTranscript` state and renders the typing/streaming effect in `ChatPanel.tsx`.

### 5. "UtteranceEnd" & Triggering the Brain
When you stop speaking, Deepgram's server-side VAD detects the silence and fires an `"UtteranceEnd"` event.
- The `sender` function catches this event, takes the accumulated final transcript, and appends it to the LLM conversation history array as a user message.
- It creates an asyncio task called `process_llm()`, which runs concurrently, so it doesn't block the WebSocket from receiving more audio or handling interruptions.
- `process_llm` delegates the core reasoning logic to the `run_orchestration` function in `backend/app/api/orchestrator.py`.

### 6. The LLM Orchestration Loop (Phase 1)
Inside `run_orchestration`:
1. **Safety Check:** The text is checked against emergency keywords using `classify_safety` (from `guardrails.py`).
2. **System Prompt Update:** It fetches the latest system instructions for the current State Machine phase (e.g., `GREETING`, `VERIFICATION`) using `get_system_prompt`.
3. **LLM Generation Loop:** It calls Groq (`llama-3.1-8b-instant`) up to `MAX_TOOL_ROUNDS` times in a `for` loop.
4. **Tool Execution:** If Groq wants to look up data (e.g., `lookup_patient`), it returns a tool call. The orchestrator extracts this and delegates execution to `execute_tool` in `backend/app/api/tools.py`.
5. **Filler Phrases:** If a data tool is called, the orchestrator immediately invokes the `filler_callback` (which maps back to `send_filler` in `websocket.py`) to send a phrase like "Let me check on that" to mask the lookup latency.
6. The results of the tools are fed back into Groq, until Groq decides to output a final text string.

### 7. Post-LLM Guardrails
Once Groq generates its final textual response, the orchestrator runs `validate_response` (from `guardrails.py`).
- This checks if the LLM hallucinated any medications or lab results that weren't actually in the JSON data returned by the tools.
- If it's safe, the orchestrator returns the final text tuple back to the `process_llm` task in `websocket.py`.

### 8. Text-to-Speech Streaming (Phase 2)
Back in `websocket.py`, the orchestrator has resolved with the final response text.
1. `process_llm` sends the text to the frontend so it appears in the chat UI.
2. It invokes `stream_tts(full_response, websocket)`.
3. `stream_tts` opens a WebSocket to **ElevenLabs**, sends the text payload, and starts an async `receive_audio` task.
4. As ElevenLabs generates the voice, it streams back `base64` audio chunks. The `receive_audio` task decodes them and sends the raw binary bytes straight to the frontend WebSocket.

### 9. Gapless Playback on the Frontend
Finally, the frontend WebSocket receives the binary audio blobs from the backend via the `ws.onmessage` listener in `useVoiceSession.ts`.
- It calls `playbackManagerRef.current?.scheduleChunk(arrayBuffer)`.
- Inside `audioManager.ts`, the `AudioPlaybackManager` class takes over. The `scheduleChunk` method converts the `Int16` chunks back into Web Audio API `Float32` arrays.
- It dynamically calculates the exact `nextStartTime` on the AudioContext timeline to seamlessly stitch the chunks together without popping or stuttering.
- The audio is played out of your speakers!

### Handling Interruptions (Bonus)
If you start speaking while the bot is mid-sentence:
1. The frontend VAD detects the start of speech in the `onSpeechStart` callback inside `useVoiceSession.ts`.
2. It immediately calls `playbackManagerRef.current?.interrupt()`, which instantly stops all queued audio chunk nodes on the frontend.
3. It sends a `{"type": "interrupt"}` JSON message to the backend.
4. The backend `receiver` task gets this message, triggers `cancel_event.set()`, and explicitly calls `cancel()` on the running LLM task (`current_llm_task`).
5. The system is instantly reset, discards the stale generation, and begins transcribing your new voice input seamlessly.
