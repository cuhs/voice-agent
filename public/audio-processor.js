class AudioProcessor extends AudioWorkletProcessor {
  process(inputs, outputs, parameters) {
    // inputs is an array of inputs, each input is an array of channels,
    // each channel is a Float32Array of samples
    const input = inputs[0];
    
    if (input && input.length > 0) {
      const channelData = input[0];
      // Post the raw PCM Float32Array data back to the main thread
      this.port.postMessage(channelData);
    }
    
    // Return true to keep the processor alive
    return true;
  }
}

registerProcessor('audio-processor', AudioProcessor);
