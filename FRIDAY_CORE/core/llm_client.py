# core/llm_client.py
"""The single point of contact with Ollama.

Every call in the project goes through here so that pointing inference at
another machine is a settings change, not a search-and-replace.

`ollama` is imported inside `get_client` rather than at module scope so that
importing the graph does not require the package. CI installs only `langgraph`
and `pytest`, and the routing tests mock `chat` — a module-scope import would
make them uncollectable for a dependency they never call.

Reachability: `llm.host` and `vlm.host` can point off-machine (Phase 5), and a
Pi 5 on wifi going quiet is an ordinary event, not an exotic one. Rather than
let every call fail against a dead host, `_resolved_host` probes it once,
falls back to localhost if it does not answer, and caches the result for the
rest of the process — one round trip on first use per configured host, not
one per chat() call. `resolve_host` is kept a plain function of (configured,
probe) so the fallback decision is unit-testable with a fake probe, with no
lru_cache or network module involved in the test.
"""
from functools import lru_cache

from core.config import SETTINGS

# The fallback target when a configured host does not answer. Also the
# default for both llm.host and vlm.host, so a stock install never probes
# itself — resolve_host short-circuits when configured already matches this.
LOCALHOST = "http://127.0.0.1:11434"

# Short on purpose: this blocks whichever thread first calls get_client() for
# a given host, once per process. A generous timeout here just makes a dead
# host slower to give up on for no benefit — the whole point is to fail fast
# and fall back rather than let the model call itself be the timeout.
PROBE_TIMEOUT_SECONDS = 1.5


def resolve_host(configured: str, probe) -> tuple[str, bool]:
    """Return (host_to_use, fell_back). `probe(host) -> bool` does the actual check.

    A configured host equal to LOCALHOST is trusted without probing — the
    default install never pays a startup round trip for itself. Anything
    else is probed; a False result or a raised exception both count as
    unreachable, since a probe failure and a probe that says "no" mean the
    same thing to a caller deciding where to send inference.
    """
    if configured.rstrip("/") == LOCALHOST.rstrip("/"):
        return configured, False
    try:
        reachable = probe(configured)
    except Exception:
        reachable = False
    if reachable:
        return configured, False
    return LOCALHOST, True


def _probe_reachable(host: str, timeout: float = PROBE_TIMEOUT_SECONDS) -> bool:
    """Cheap Ollama reachability check: GET /api/version, short timeout.

    `client.list()` also works but enumerates installed models — heavier
    than needed just to learn something is listening. `requests` is used
    directly rather than `ollama.Client` so a probe never constructs the
    real client for a host that turns out to be dead.
    """
    import requests

    try:
        response = requests.get(f"{host.rstrip('/')}/api/version", timeout=timeout)
        return response.status_code == 200
    except Exception:
        return False


@lru_cache(maxsize=None)
def _resolved_host(configured: str) -> str:
    host, fell_back = resolve_host(configured, _probe_reachable)
    if fell_back:
        # Printed rather than raised: an unreachable edge host must not take
        # down every subsequent call, it must be visible and then worked
        # around. See the module docstring for why this runs once per host,
        # not once per chat() call.
        print(f"[!] {configured} is unreachable; falling back to {host} for the rest of this run.")
    return host


@lru_cache(maxsize=None)
def get_client(host: str | None = None):
    import ollama

    configured = host or SETTINGS["llm"]["host"]
    return ollama.Client(host=_resolved_host(configured))


def chat(messages, model=None, fmt=None, temperature=None, host=None):
    """Blocking chat call. `fmt` takes a JSON schema dict for structured output."""
    options = {"temperature": SETTINGS["llm"]["temperature"] if temperature is None else temperature}
    response = get_client(host).chat(
        model=model or SETTINGS["llm"]["model"],
        messages=messages,
        format=fmt,
        options=options,
    )
    return response["message"]["content"]
