# tests/test_event_vocabulary_drift.py
"""Static gate: the renderer's event-type union must match server/events.py.

The HUD switches on the `type` field of every envelope coming over the
socket; the source of truth for what types exist is events.ALL_TYPES.
Nothing wires the two sides together at build time — a type added to one
and forgotten on the other fails silently at runtime, in a build the Node
CI job would not catch either (tsc only checks that AgentEventType is used
consistently within the TypeScript files, not that the set is complete
against the backend).

Read the union with a regex rather than a TypeScript toolchain, for the
same reason tools/check_manifests.py reads skill manifests with `ast`
instead of importing them: this test has to run in the CI job that
deliberately does not install requirements.txt, let alone Node.
"""
import re
from pathlib import Path

from server import events

EVENTS_TS = Path(__file__).resolve().parent.parent.parent / "desktop" / "src" / "events.ts"

# Matches the `export type AgentEventType = ... ;` declaration and captures
# everything between `=` and the closing `;`, across lines.
UNION_PATTERN = re.compile(r"export type AgentEventType\s*=\s*(?P<body>.*?);", re.DOTALL)
STRING_LITERAL_PATTERN = re.compile(r'"([a-z_]+)"')


def _renderer_event_types() -> set[str]:
    assert EVENTS_TS.exists(), f"expected the renderer's event-type union at {EVENTS_TS}"
    source = EVENTS_TS.read_text(encoding="utf-8")

    match = UNION_PATTERN.search(source)
    assert match, f"could not find `export type AgentEventType = ...` in {EVENTS_TS}"

    types = set(STRING_LITERAL_PATTERN.findall(match.group("body")))
    # A pattern that matches zero literals would pass set-equality against
    # nothing, which is a gate that can never fail — worth asserting away
    # explicitly rather than trusting the regex silently.
    assert types, f"found the AgentEventType union in {EVENTS_TS} but extracted no string literals from it"
    return types


def test_renderer_event_types_match_server_events():
    assert _renderer_event_types() == events.ALL_TYPES
