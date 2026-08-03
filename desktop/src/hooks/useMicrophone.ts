// Push-to-talk: record one utterance, hand the encoded blob to the caller.
//
// Recognition itself does not happen here. The renderer records and ships the
// bytes down the WebSocket; faster-whisper runs in the Python process, the
// same model the console entry point uses. The obvious shortcut — the browser's
// SpeechRecognition API — is deliberately not taken: on Chromium it streams
// audio to Google's servers, and this assistant's whole claim is that nothing
// said to it leaves the machine.
//
// The microphone is acquired per press and released on stop, rather than held
// open for the life of the window. That costs a couple of hundred milliseconds
// at the start of each recording, and it is what keeps the operating system's
// "microphone in use" indicator honest — an assistant that sits with the mic
// open permanently is exactly the thing this project's privacy guard exists to
// object to.
import { useCallback, useEffect, useRef, useState } from "react";

// Chromium records Opus in a WebM container. faster-whisper decodes it through
// PyAV without any conversion on either side; verified against a real blob
// before this file existed. If the runtime ever refuses it, the empty string
// lets MediaRecorder pick its own default, which PyAV can also read.
const PREFERRED_MIME = "audio/webm;codecs=opus";

// Below this an "utterance" is a mis-click, not speech: a WebM header with
// almost nothing after it. Sending it wastes a model pass to be told there was
// no speech in it.
const MIN_UTTERANCE_BYTES = 2000;

// Recording is a toggle rather than a hold, because the global hotkey can
// only be a toggle — Electron's globalShortcut fires on press and has no
// release event — and two different mental models for the same action is
// worse than either one. The cost of a toggle is that a forgotten one records
// until the window closes, so it stops itself. Long enough for any sensible
// request, short enough that walking away does not leave a live microphone.
const MAX_UTTERANCE_MS = 45_000;

export type MicState =
  | { kind: "idle" }
  // Between the button going down and the stream actually arriving. Shown as
  // its own state because it is the window in which speech is genuinely not
  // being captured yet, and telling the operator "recording" during it is a
  // lie that costs them the first word of the sentence.
  | { kind: "opening" }
  | { kind: "recording" }
  | { kind: "denied" }
  | { kind: "unsupported"; detail: string };

export function useMicrophone(onUtterance: (audio: ArrayBuffer) => void) {
  const [state, setState] = useState<MicState>({ kind: "idle" });
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  // Read inside the recorder's onstop, which is registered once per
  // recording; a ref keeps that callback looking at the current function
  // rather than the one that existed when recording started.
  const onUtteranceRef = useRef(onUtterance);
  onUtteranceRef.current = onUtterance;
  // True between a press and its release. Checked after the getUserMedia
  // await, because a short press can be released while that promise is still
  // pending: stop() would find no recorder to stop, and the recorder that
  // arrived a moment later would run until the operator noticed the mic light
  // and pressed again.
  const wantedRef = useRef(false);

  const timerRef = useRef<number | undefined>(undefined);

  const release = useCallback(() => {
    // Stopping the tracks, not just the recorder. A stopped MediaRecorder
    // leaves the underlying tracks live, so the microphone stays open and the
    // system indicator stays lit until the window closes.
    window.clearTimeout(timerRef.current);
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    recorderRef.current = null;
  }, []);

  const start = useCallback(async () => {
    if (recorderRef.current) return;
    if (typeof MediaRecorder === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      setState({ kind: "unsupported", detail: "This runtime has no microphone API." });
      return;
    }

    wantedRef.current = true;
    setState({ kind: "opening" });
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        // Browser-side cleanup before Whisper ever sees the audio. All three
        // are cheap and all three help a laptop microphone in a room, which
        // is the only kind of recording this will ever get.
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (error) {
      // Covers a denied permission prompt and a machine with no input device
      // at all. Both end with nothing to record, and the operator needs to be
      // told rather than left pressing a button that does nothing.
      setState({ kind: "denied" });
      console.error("[friday] microphone unavailable:", error);
      return;
    }

    if (!wantedRef.current) {
      // Released before the stream arrived. Hand the microphone straight back
      // rather than recording into a session nobody is waiting on.
      stream.getTracks().forEach((track) => track.stop());
      setState({ kind: "idle" });
      return;
    }

    const mimeType = MediaRecorder.isTypeSupported(PREFERRED_MIME) ? PREFERRED_MIME : "";
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    const chunks: Blob[] = [];
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data);
    };
    recorder.onstop = async () => {
      release();
      setState({ kind: "idle" });
      const blob = new Blob(chunks, { type: recorder.mimeType });
      if (blob.size < MIN_UTTERANCE_BYTES) return;
      onUtteranceRef.current(await blob.arrayBuffer());
    };

    streamRef.current = stream;
    recorderRef.current = recorder;
    recorder.start();
    setState({ kind: "recording" });
    timerRef.current = window.setTimeout(() => {
      if (recorder.state !== "inactive") recorder.stop();
    }, MAX_UTTERANCE_MS);
  }, [release]);

  const stop = useCallback(() => {
    wantedRef.current = false;
    const recorder = recorderRef.current;
    if (!recorder) {
      // Nothing was recording — but the button may have been released during
      // the `opening` window, before the stream arrived. Falling back to idle
      // keeps the UI from latching on a state that will never advance.
      setState((current) => (current.kind === "opening" ? { kind: "idle" } : current));
      return;
    }
    if (recorder.state !== "inactive") recorder.stop();
  }, []);

  // A window closed mid-recording must not leave the microphone open.
  useEffect(() => release, [release]);

  return { micState: state, startRecording: start, stopRecording: stop };
}
