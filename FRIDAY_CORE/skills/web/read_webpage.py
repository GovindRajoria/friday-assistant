# skills/web/read_webpage.py
"""Fetch a web page and hand back its readable text.

The natural partner to read_news: headlines say what happened, this says
what the article actually reported. Also the thing to reach for when the
operator pastes a link.

The text is extracted here and summarised by the reasoning loop rather than
by a second model call inside the skill. That keeps one model call per step,
and it means the summary is shaped by the question that was asked instead of
by a generic "summarise this" prompt that cannot know what the operator
wanted from the page.
"""
import requests
from bs4 import BeautifulSoup

REQUEST_TIMEOUT_SECONDS = 15
# A prompt budget, not a page limit. Enough for the substance of an article;
# short enough that one long page cannot crowd out the conversation.
MAX_CHARS = 6000
# Chrome's real UA. Plenty of sites serve a stripped or blocked page to
# anything that announces itself as a script.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Removed before extraction. Navigation and script text is not content, and
# leaving it in means the model summarises a cookie banner.
NOISE_TAGS = ("script", "style", "nav", "header", "footer", "aside", "form", "noscript", "iframe", "svg")


def extract_text(html: str, max_chars: int = MAX_CHARS) -> tuple[str, str]:
    """Return (title, text) from an HTML document.

    Split out from the fetch so it is testable against canned HTML with no
    network — extraction is the part that quietly degrades as pages change.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(NOISE_TAGS)):
        tag.decompose()

    title = (soup.title.get_text(strip=True) if soup.title else "").strip()

    # Prefer <article> when a page marks one — it is the publisher telling us
    # where the content is, which beats any heuristic we could write.
    root = soup.find("article") or soup.body or soup
    paragraphs = [p.get_text(" ", strip=True) for p in root.find_all("p")]
    # Very short fragments are almost always captions, bylines or bare links.
    text = "\n".join(part for part in paragraphs if len(part) > 40)
    if not text:
        # No usable <p> elements — fall back to the whole block of text
        # rather than reporting an empty page.
        text = root.get_text(" ", strip=True)

    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars].rstrip() + " […truncated]"
    return title, text


class ReadWebpageSkill:
    def __init__(self):
        self.manifest = {
            "name": "read_webpage",
            "description": (
                "Fetches a web page by URL and returns its readable text so you can "
                "summarise or answer questions about it. Use this when the operator "
                "gives you a link, or to read the full story behind a headline. "
                "Parameter: 'url'. This only reads pages — it cannot log in, fill "
                "forms or click anything."
            ),
            "parameters": ["url"],
        }

    def execute(self, params=None):
        url = str((params or {}).get("url") or "").strip()
        if not url:
            return {"status": "error", "message": "I need a URL to read."}
        if not url.startswith(("http://", "https://")):
            # Rejected rather than guessed at. Prepending https:// to
            # something that is not a URL at all produces a confusing DNS
            # error instead of a clear refusal.
            return {"status": "error", "message": f"'{url}' is not an http or https URL."}

        try:
            response = requests.get(url, headers={"User-Agent": USER_AGENT},
                                    timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
        except requests.RequestException as error:
            return {"status": "error", "message": f"I could not fetch that page: {error}"}

        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type and "text" not in content_type:
            return {"status": "error",
                    "message": f"That URL is {content_type or 'an unknown type'}, not a readable page."}

        title, text = extract_text(response.text)
        if not text:
            return {"status": "error", "message": "That page had no readable text in it."}

        heading = f"{title}\n\n" if title else ""
        return {
            "status": "success",
            "message": f"Content of {url}:\n{heading}{text}",
            "data": {"title": title, "url": url, "characters": len(text)},
        }


def setup():
    return ReadWebpageSkill()
