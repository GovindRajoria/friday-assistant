# skills/web/web_search.py
"""General web lookup, over published interfaces rather than scraped HTML.

This skill used to POST to DuckDuckGo's lite endpoint and pull text out of
`td.result-snippet`. That stopped working. Measured directly on 2026-08-03:
both `lite.duckduckgo.com/lite/` and `html.duckduckgo.com/html/` answer
**202 with zero result elements** — an anti-bot response, not a layout
change, so adjusting selectors would not have helped. Every web question the
assistant was asked failed, and it failed as "I couldn't find any results",
which is indistinguishable from a genuinely empty search.

Three sources are tried in order, all documented interfaces meant to be
called by programs, none needing an API key:

  1. DuckDuckGo's Instant Answer API — good at "what is X" / "who is X",
     silent for many queries, which is why it is not alone.
  2. The MediaWiki search API — encyclopedic breadth when the first is quiet.
  3. Google News RSS — anything current enough that neither of the above
     knows about it yet.

The first source that returns text wins; the local model then extracts the
answer from it. Nothing but the query leaves the machine.
"""
import xml.etree.ElementTree as ElementTree
from urllib.parse import quote_plus

import requests
from core import llm_client

REQUEST_TIMEOUT_SECONDS = 12
MAX_SNIPPETS = 3
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

NOT_FOUND = "Information not found."


def _get(url, params):
    response = requests.get(url, params=params, headers={"User-Agent": USER_AGENT},
                            timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response


def _from_instant_answer(query: str) -> list[str]:
    """DuckDuckGo's Instant Answer API — the JSON one, which is not blocked."""
    payload = _get("https://api.duckduckgo.com/",
                   {"q": query, "format": "json", "no_html": 1, "skip_disambig": 1}).json()
    snippets = []
    for key in ("AbstractText", "Answer", "Definition"):
        value = (payload.get(key) or "").strip()
        if value:
            snippets.append(value)
    for topic in payload.get("RelatedTopics", []):
        # Related topics arrive either as flat entries or as nested groups.
        # Only the flat ones carry Text; a group has no Text key at all.
        text = (topic.get("Text") or "").strip() if isinstance(topic, dict) else ""
        if text:
            snippets.append(text)
    return snippets


def _from_wikipedia(query: str) -> list[str]:
    """MediaWiki search, then the REST summary of the best-matching page."""
    hits = _get("https://en.wikipedia.org/w/api.php",
                {"action": "query", "list": "search", "srsearch": query,
                 "format": "json", "srlimit": 1}).json()
    results = hits.get("query", {}).get("search", [])
    if not results:
        return []
    title = results[0]["title"]
    summary = _get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote_plus(title)}", {}).json()
    extract = (summary.get("extract") or "").strip()
    return [f"{title}: {extract}"] if extract else []


def _from_news(query: str) -> list[str]:
    """Google News RSS — the fallback for anything too current for the other two."""
    response = _get("https://news.google.com/rss/search",
                    {"q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"})
    root = ElementTree.fromstring(response.text)
    titles = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if title:
            titles.append(title)
        if len(titles) >= MAX_SNIPPETS:
            break
    return titles


# Order matters: most precise first, broadest last.
SOURCES = (
    ("instant answer", _from_instant_answer),
    ("wikipedia", _from_wikipedia),
    ("news", _from_news),
)


def gather_snippets(query: str, sources=SOURCES) -> tuple[list[str], list[str]]:
    """Return (snippets, failures). Takes `sources` so it is testable with fakes.

    One source raising must not sink the search — a blip on Wikipedia is no
    reason to give up when the news feed would have answered. Failures are
    collected and only reported when nothing at all came back, so a partial
    outage stays invisible and a total one gets explained.
    """
    failures = []
    for name, fetch in sources:
        try:
            snippets = fetch(query)
        except Exception as error:  # noqa: BLE001 — one dead source must not end the search
            failures.append(f"{name}: {error}")
            continue
        if snippets:
            return snippets[:MAX_SNIPPETS], failures
    return [], failures


class WebSearchSkill:
    def __init__(self):
        self.manifest = {
            "name": "web_search",
            "description": (
                "Searches the internet for facts: weather, definitions, people, places, "
                "history, or any factual question you cannot already answer. "
                "DO NOT use this to open applications. For news headlines and current "
                "events, use read_news instead."
            ),
            "parameters": ["search_query"],
        }

    def execute(self, params=None):
        query = (params or {}).get("search_query")
        if not query:
            return {"status": "error", "message": "I need a specific query to search the web."}

        snippets, failures = gather_snippets(query)
        if not snippets:
            detail = f" ({'; '.join(failures)})" if failures else ""
            return {"status": "error",
                    "message": f"I couldn't find anything on the web for that.{detail}"}

        summary_prompt = (
            "You are a strict data extraction tool.\n"
            f'Based on the following web search results, extract ONLY the factual answer to: "{query}".\n'
            "Do not use conversational filler. Return the raw facts in one or two sentences. "
            f'If the answer is not in the text, reply "{NOT_FOUND}"\n\n'
            "Results:\n" + "\n".join(snippets)
        )
        try:
            summary = llm_client.chat([{"role": "user", "content": summary_prompt}]).strip()
        except Exception as error:  # noqa: BLE001 — the search worked; only the summary failed
            # Hand back the raw snippets rather than nothing. Less tidy than a
            # summary, and they still answer the question.
            return {"status": "success", "message": "\n".join(snippets), "data": {"summary_failed": str(error)}}

        if not summary or summary == NOT_FOUND:
            # Say what was actually found rather than insisting nothing was.
            # The old skill returned this bare string on every failed
            # extraction, which is exactly what a broken scraper looked like
            # from the outside for as long as it stayed broken.
            return {"status": "success",
                    "message": "I could not extract a direct answer. What the web returned:\n" + "\n".join(snippets)}

        return {"status": "success", "message": summary, "data": {"snippets": snippets}}


def setup():
    return WebSearchSkill()
