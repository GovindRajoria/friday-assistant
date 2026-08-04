// Continuous listening: hold the microphone open, cut utterances on silence.
//
// The backend decides whether an utterance was addressed to the assistant — see
// core/wake_word.py. This file's only job is to turn an open microphone into
// well-formed audio segments, one per thing somebody said.
//
// **The recorder runs continuously and is cut during silence.** That ordering is
// the whole design and the obvious alternative is broken: starting a recorder
// when speech is *detected* loses however long detection takes, which is 150ms
// of the first word — and the first word is the wake word. Getting "riday, what's
// the weather" would make the feature look unreliable when the microphone was
// fine. By already recording before speech starts, onset is never clipped; the
// only gap is the millisecond between one recorder stopping and the next
// starting, which lands inside a silence by construction.
//
// The energy threshold adapts. A fixed one fails in both directions — a quiet
// room makes it deaf, a noisy one makes it trigger on the air conditioning — so
// the noise floor is tracked while nothing is being said and the trigger sits a
// fixed multiple above it.
//
// The microphone stays open for as long as this is enabled, and the operating
// system's "microphone in use" indicator stays lit. That is honest and it is why
// this mode is opt-in: an assistant that holds the mic open permanently is
// exactly the thing this project's privacy guard exists to object to, so it must
// be a choice somebody made rather than a default they inherited.
import { useCallback, useEffect, useRef, useState } from "react";

const PREFERRED_MIME = "audio/webm;codecs=opus";

// How often the energy is sampled. 50ms is short enough to catch the end of a
// sentence promptly and long enough that the maths is free.
const FRAME_MS = 50;
// Silence this long ends an utterance. Shorter and it cuts people off mid-
// sentence at a comma; longer and every request feels laggy.
const SILENCE_MS_TO_END = 850;
// Speech must last at least this long to count, which discards a cough, a door,
// and a single keyboard clack.
const MIN_SPEECH_MS = 250;
// A segment is cut at this length whatever is happening, so one long monologue
// cannot grow an unbounded blob in memory.
const MAX_SEGMENT_MS = 20_000;
// A segment with no speech in it is thrown away and restarted this often, so
// silence does not accumulate into a large pointless recording.
const IDLE_RECYCLE_MS = 6_000;

// The trigger sits this far above the measured noise floor. 2.2 was chosen
// against a laptop microphone in a room with a fan: high enough to ignore the
// fan, low enough to catch a normal speaking voice from a metre away.
const SPEECH_MULTIPLIER = 2.2;
// A floor never drops below this, so a silent room cannot drive the threshold to
// zero and make every tiny noise "speech".
const MIN_NOISE_FLOOR = 0.004;
// How quickly the floor follows the room. Slow, so a sentence does not raise it.
const FLOOR_RISE = 0.02;
const FLOOR_FALL = 0.2;

const MIN_UTTERANCE_BYTES = 2000;

export type ListeningState =
  | { kind: "off" }
  | { kind: "starting" }
  // Open and waiting. This is the resting state of the mode.
  | { kind: "waiting" }
  // Somebody is talking right now.
  | { kind: "hearing" }
  | { kind: "denied" }
  | { kind: "unsupported"; detail: string };

export function useAlwaysListening(onUtterance: (audio: ArrayBuffer) => void) {
  const [state, setState] = useState<ListeningState>({ kind: "off" });
  const enabledRef = useRef(false);
  const streamRef = useRef<MediaStream | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const timerRef = useRef<number | undefined>(undefined);

  const onUtteranceRef = useRef(onUtterance);
  onUtteranceRef.current = onUtterance;

  // Per-segment bookkeeping, in refs because the analysis loop is an interval
  // and must not re-create itself on every frame.
  const chunksRef = useRef<Blob[]>([]);
  const heardSpeechRef = useRef(false);
  const speechFramesRef = useRef(0);
  const silenceFramesRef = useRef(0);
  const segmentStartRef = useRef(0);
  const floorRef = useRef(MIN_NOISE_FLOOR);

  const teardown = useCallback(() => {
    window.clearInterval(timerRef.current);
    timerRef.current = undefined;
    const recorder = recorderRef.current;
    recorderRef.current = null;
    if (recorder && recorder.state !== "inactive") {
      // Detach first: the handler would otherwise ship or recycle a segment for
      // a mode that is being switched off.
      recorder.onstop = null;
      recorder.stop();
    }
    contextRef.current?.close().catch(() => undefined);
    contextRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, []);

  const startSegment = useCallback(() => {
    const stream = streamRef.current;
    if (!stream || !enabledRef.current) return;

    const mimeType = MediaRecorder.isTypeSupported(PREFERRED_MIME) ? PREFERRED_MIME : "";
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    chunksRef.current = [];
    heardSpeechRef.current = false;
    speechFramesRef.current = 0;
    silenceFramesRef.current = 0;
    segmentStartRef.current = Date.now();

    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunksRef.current.push(event.data);
    };
    recorder.onstop = async () => {
      const shipIt = heardSpeechRef.current;
      const blob = new Blob(chunksRef.current, { type: recorder.mimeType });
      chunksRef.current = [];
      // Start the next segment before the await below, so the gap between
      // segments is as small as possible. Anything said during an await is
      // audio nobody recorded.
      if (enabledRef.current) startSegment();
      if (shipIt && blob.size >= MIN_UTTERANCE_BYTES) {
        onUtteranceRef.current(await blob.arrayBuffer());
      }
    };

    recorderRef.current = recorder;
    recorder.start();
  }, []);

  const cutSegment = useCallback(() => {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") recorder.stop();
  }, []);

  const start = useCallback(async () => {
    if (enabledRef.current) return;
    if (typeof MediaRecorder === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      setState({ kind: "unsupported", detail: "This runtime has no microphone API." });
      return;
    }

    enabledRef.current = true;
    setState({ kind: "starting" });

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (error) {
      enabledRef.current = false;
      setState({ kind: "denied" });
      console.error("[friday] microphone unavailable:", error);
      return;
    }
    if (!enabledRef.current) {
      // Switched off while the permission prompt was open.
      stream.getTracks().forEach((track) => track.stop());
      return;
    }

    streamRef.current = stream;
    const context = new AudioContext();
    contextRef.current = context;
    const analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    context.createMediaStreamSource(stream).connect(analyser);
    const samples = new Float32Array(analyser.fftSize);

    startSegment();
    setState({ kind: "waiting" });

    const silenceFramesToEnd = Math.ceil(SILENCE_MS_TO_END / FRAME_MS);
    const speechFramesToCount = Math.ceil(MIN_SPEECH_MS / FRAME_MS);

    timerRef.current = window.setInterval(() => {
      analyser.getFloatTimeDomainData(samples);
      let sum = 0;
      for (const sample of samples) sum += sample * sample;
      const level = Math.sqrt(sum / samples.length);

      const speaking = level > Math.max(floorRef.current * SPEECH_MULTIPLIER, MIN_NOISE_FLOOR);

      if (speaking) {
        speechFramesRef.current += 1;
        silenceFramesRef.current = 0;
        if (speechFramesRef.current >= speechFramesToCount && !heardSpeechRef.current) {
          heardSpeechRef.current = true;
          setState({ kind: "hearing" });
        }
      } else {
        silenceFramesRef.current += 1;
        speechFramesRef.current = 0;
        // The floor only follows the room while nobody is talking, so a long
        // sentence cannot raise the threshold above the speaker's own voice.
        const rate = level > floorRef.current ? FLOOR_RISE : FLOOR_FALL;
        floorRef.current = Math.max(
          MIN_NOISE_FLOOR,
          floorRef.current + (level - floorRef.current) * rate,
        );
      }

      const age = Date.now() - segmentStartRef.current;
      const endedBySilence = heardSpeechRef.current && silenceFramesRef.current >= silenceFramesToEnd;
      const tooLong = age >= MAX_SEGMENT_MS;
      const idleTooLong = !heardSpeechRef.current && age >= IDLE_RECYCLE_MS;

      if (endedBySilence || tooLong || idleTooLong) {
        if (heardSpeechRef.current) setState({ kind: "waiting" });
        cutSegment();
      }
    }, FRAME_MS);
  }, [cutSegment, startSegment]);

  const stop = useCallback(() => {
    enabledRef.current = false;
    teardown();
    setState({ kind: "off" });
  }, [teardown]);

  // A window closed while listening must not leave the microphone open.
  useEffect(() => () => {
    enabledRef.current = false;
    teardown();
  }, [teardown]);

  return { listeningState: state, startListening: start, stopListening: stop };
}
