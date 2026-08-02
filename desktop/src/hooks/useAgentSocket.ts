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
  const reconnectTimerRef = useRef<number | undefined>(undefined);
  // Distinguishes "the effect is tearing down (StrictMode remount, unmount)"
  // from "the network dropped us" — only the latter should schedule a
  // reconnect, or every unmount would leave a stray timer racing to open a
  // socket nothing is listening for anymore.
  const tearingDownRef = useRef(false);
  const nextEventIdRef = useRef(0);

  useEffect(() => {
    tearingDownRef.current = false;

    function connect() {
      const socket = new WebSocket(WS_URL);
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
        dispatch({ kind: "disconnected" });
        if (!tearingDownRef.current) {
          reconnectTimerRef.current = window.setTimeout(connect, RECONNECT_DELAY_MS);
        }
      };

      // A socket error is always followed by a close event in browsers'
      // WebSocket implementations; closing explicitly here just avoids
      // depending on that ordering being reliable across runtimes.
      socket.onerror = () => socket.close();
    }

    connect();
    return () => {
      tearingDownRef.current = true;
      window.clearTimeout(reconnectTimerRef.current);
      socketRef.current?.close();
    };
  }, []);

  const sendPrompt = useCallback((text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    if (socketRef.current?.readyState !== WebSocket.OPEN) return;
    dispatch({ kind: "prompt_sent" });
    socketRef.current.send(JSON.stringify({ type: "prompt", text: trimmed }));
  }, []);

  const sendCancel = useCallback(() => {
    if (socketRef.current?.readyState !== WebSocket.OPEN) return;
    socketRef.current.send(JSON.stringify({ type: "cancel" }));
  }, []);

  return { state, sendPrompt, sendCancel };
}
