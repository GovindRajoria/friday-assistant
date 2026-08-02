# core/llm_client.py
"""The single point of contact with Ollama.

Every call in the project goes through here so that pointing inference at
another machine is a settings change, not a search-and-replace.

`ollama` is imported inside `get_client` rather than at module scope so that
importing the graph does not require the package. CI installs only `langgraph`
and `pytest`, and the routing tests mock `chat` — a module-scope import would
make them uncollectable for a dependency they never call.
"""
from functools import lru_cache

from core.config import SETTINGS


@lru_cache(maxsize=None)
def get_client(host: str | None = None):
    import ollama

    return ollama.Client(host=host or SETTINGS["llm"]["host"])


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
