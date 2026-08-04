// Owns every side effect the HUD needs: the socket itself, reconnecting
// when it drops, and handing each parsed frame to the pure reducer in
// src/reducer.ts. Nothing in here decides what a "thought" event should do
// to the orb — that logic lives in the reducer precisely so it can be
// tested without a real (or fake) WebSocket.
import { useCallback, useEffect, useReducer, useRef } from "react";
import { isAgentEvent } from "../events";
import { initialState, reducer } from "../reducer";

const WS_URL = "ws://127.0.0.1:8756/ws";
// The backend has already been probed and, if necessary, spawned by
// electron/main.ts before this window is ever shown — a dropped
// connection here means the process died or the machine is asleep, not a
// slow cold start, so a short fixed delay is enough rather than backoff.
const RECONNECT_DELAY_MS = 1500;

export function useAgentSocket() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const socketRef = useRef<WebSocket | null>(null);
  const nextEventIdRef = useRef(0);

  useEffect(() => {
    // Per-effect-instance, deliberately not a ref.
    //
    // This was a ref shared across every run of the effect, and under
    // StrictMode's double mount that leaked a socket on every launch:
    // mount opens A, cleanup sets the flag and closes A, the second mount
    // clears the flag and opens B — and only then does A's close event
    // actually fire, sees a cleared flag, and reconnects as C. B and C both
    // stay open, both receive the same broadcast, and both dispatch it, so
    // the HUD rendered every thought, observation and answer twice and
    // counted every turn twice. A closure variable belongs to the run that
    // created the socket, which is the question being asked.
    let disposed = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | undefined;

    function connect() {
      if (disposed) return;
      socket = new WebSocket(WS_URL);
      socketRef.current = socket;

      socket.onopen = () => dispatch({ kind: "connected" });

      socket.onmessage = (message) => {
        let parsed: unknown;
        try {
          parsed = JSON.parse(message.data as string);
        } catch {
          return; // malformed frame; nothing sane to render
        }
        if (isAgentEvent(parsed)) {
          const id = String(nextEventIdRef.current++);
          dispatch({ kind: "agent_event", event: parsed, id });
        }
      };

      socket.onclose = () => {
        // A socket this effect run already disowned must not report the
        // disconnection either — the replacement is live, and dispatching
        // here would flip the HUD to a fault it has already recovered from.
        if (disposed) return;
        dispatch({ kind: "disconnected" });
        reconnectTimer = window.setTimeout(connect, RECONNECT_DELAY_MS);
      };

      // A socket error is always followed by a close event in browsers'
      // WebSocket implementations; closing explicitly here just avoids
      // depending on that ordering being reliable across runtimes.
      socket.onerror = () => socket?.close();
    }

    connect();
    return () => {
      disposed = true;
      window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, []);

  const sendPrompt = useCallback((text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    if (socketRef.current?.readyState !== WebSocket.OPEN) return;
    dispatch({ kind: "prompt_sent" });
    socketRef.current.send(JSON.stringify({ type: "prompt", text: trimmed }));
  }, []);

  // One recorded utterance, sent as a binary frame. Nothing else on this
  // socket is binary, so the frame needs no envelope of its own — see the
  // receive loop in server/app.py. The reply comes back as an ordinary
  // `transcript` event through onmessage above, like everything else.
  const sendAudio = useCallback((audio: ArrayBuffer) => {
    if (socketRef.current?.readyState !== WebSocket.OPEN) return false;
    socketRef.current.send(audio);
    return true;
  }, []);

  // Tells the backend how to read the binary frames that follow: a deliberate
  // recording to be transcribed into the prompt box for review, or room audio to
  // be gated on the wake word and acted on. It is a mode rather than a per-frame
  // flag because a binary WebSocket frame has nowhere to carry one.
  const sendListenMode = useCallback((ambient: boolean) => {
    if (socketRef.current?.readyState !== WebSocket.OPEN) return;
    socketRef.current.send(JSON.stringify({ type: "listen_mode", ambient }));
  }, []);

  const sendCancel = useCallback(() => {
    if (socketRef.current?.readyState !== WebSocket.OPEN) return;
    socketRef.current.send(JSON.stringify({ type: "cancel" }));
  }, []);

  // Answers a pending confirmation_required (Phase 6). Dispatched locally
  // right away rather than waiting for a server event to clear it — the
  // backend has no "confirmation acknowledged" event, only whatever the
  // turn does next, so the button has to update the HUD itself.
  const sendConfirm = useCallback((approved: boolean) => {
    if (socketRef.current?.readyState !== WebSocket.OPEN) return;
    socketRef.current.send(JSON.stringify({ type: "confirm", approved }));
    dispatch({ kind: "confirmation_resolved" });
  }, []);

  return { state, sendPrompt, sendAudio, sendListenMode, sendCancel, sendConfirm };
}
