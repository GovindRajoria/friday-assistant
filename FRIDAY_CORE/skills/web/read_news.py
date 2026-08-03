# skills/web/read_news.py
"""Current headlines, from Google News RSS.

A feed rather than a scrape, and that is the whole point. The general
`web_search` skill has already had its HTML scraping broken once by an
anti-bot change on the search engine's side — measured directly: DuckDuckGo's
lite and html endpoints both answer 202 with no results at all now. An RSS
feed is a published interface meant to be consumed by programs, so it does
not rot the same way a page layout does.

Parsed with xml.etree from the standard library rather than BeautifulSoup's
"xml" mode, which needs lxml. lxml happens to be installed here as a
transitive dependency, and building on something requirements.txt never asked
for is how an install works on one machine and not the next.

No API key, and nothing leaves the machine except the query itself.
"""
import xml.etree.ElementTree as ElementTree
from urllib.parse import quote_plus

import requests

FEED_TIMEOUT_SECONDS = 12
# Enough for the model to spot a theme across several stories; few enough
# that the observation does not crowd out the rest of the prompt.
DEFAULT_HEADLINES = 8
MAX_HEADLINES = 20

# Google News localises heavily. These are India/English because that is where
# this install runs; they are the only two things to change for another
# region, and they are named rather than inlined for exactly that reason.
LOCALE = {"hl": "en-IN", "gl": "IN", "ceid": "IN:en"}

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def parse_headlines(feed_xml: str, limit: int) -> list[dict]:
    """Pull (title, source) pairs out of an RSS document.

    Split out from the fetch so it can be tested against a canned feed with no
    network — the parsing is the part that breaks silently when a format
    shifts, and it is the part worth a regression test.
    """
    root = ElementTree.fromstring(feed_xml)
    headlines = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        # Google News puts the publication in a <source> child. Its absence
        # is normal for some feeds, so it is optional rather than required.
        source_element = item.find("source")
        source = (source_element.text or "").strip() if source_element is not None else ""
        headlines.append({"title": title, "source": source})
        if len(headlines) >= limit:
            break
    return headlines


class ReadNewsSkill:
    def __init__(self):
        self.manifest = {
            "name": "read_news",
            "description": (
                "Fetches the current top news headlines, optionally about a specific "
                "topic. Use this for anything about the news, current events, "
                "headlines, or what is happening today, and then summarise the "
                "results for the user in your own words. Parameters: 'topic' "
                "(optional — leave it out for general top stories) and 'count' "
                "(optional, default 8). DO NOT use web_search for news; use this."
            ),
            "parameters": ["topic", "count"],
        }

    def execute(self, params=None):
        params = params or {}
        topic = str(params.get("topic") or "").strip()

        try:
            count = int(params.get("count") or DEFAULT_HEADLINES)
        except (TypeError, ValueError):
            count = DEFAULT_HEADLINES
        count = max(1, min(count, MAX_HEADLINES))

        if topic:
            url = f"https://news.google.com/rss/search?q={quote_plus(topic)}"
        else:
            url = "https://news.google.com/rss"

        try:
            response = requests.get(
                url,
                params=LOCALE,
                headers={"User-Agent": USER_AGENT},
                timeout=FEED_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            headlines = parse_headlines(response.text, count)
        except requests.RequestException as error:
            return {"status": "error", "message": f"I could not reach the news feed: {error}"}
        except ElementTree.ParseError as error:
            return {"status": "error", "message": f"The news feed came back unreadable: {error}"}

        if not headlines:
            subject = f"'{topic}'" if topic else "top stories"
            return {"status": "error", "message": f"The news feed returned nothing for {subject}."}

        subject = f"news about '{topic}'" if topic else "top news stories"
        lines = [
            f"{index}. {item['title']}" + (f" ({item['source']})" if item["source"] else "")
            for index, item in enumerate(headlines, start=1)
        ]
        return {
            "status": "success",
            # Headlines only, deliberately — the reasoning loop summarises them
            # in its own answer. Summarising here too would spend a second
            # model call to produce something the next step discards.
            "message": f"Current {subject}:\n" + "\n".join(lines),
            "data": {"headlines": headlines},
        }


def setup():
    return ReadNewsSkill()
