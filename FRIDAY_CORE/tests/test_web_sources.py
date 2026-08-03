# tests/test_web_sources.py
"""Parsing and source-fallback for the web skills, with no network.

The parts worth gating are the two that failed silently in production: an
upstream that stops returning what the parser expects, and a single dead
source taking the whole search down with it. Both are exercised here against
canned data and fake sources — the live endpoints are checked by hand,
because a test that reaches the internet fails for reasons that have nothing
to do with this code.
"""
import pytest
from skills.web.read_news import parse_headlines
from skills.web.web_search import gather_snippets

# Trimmed to the shape that matters: <item> with <title> and Google News's
# <source> child. Captured from a real feed response.
FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Top stories</title>
  <item>
    <title>Something happened today</title>
    <source url="https://example.com">Example Times</source>
  </item>
  <item>
    <title>A second thing happened</title>
    <source url="https://other.example">Other Herald</source>
  </item>
  <item>
    <title>A headline with no source element</title>
  </item>
  <item>
    <title>   </title>
  </item>
</channel></rss>
"""


def test_headlines_are_parsed_with_their_sources():
    headlines = parse_headlines(FEED, limit=10)

    assert headlines[0] == {"title": "Something happened today", "source": "Example Times"}
    assert headlines[1] == {"title": "A second thing happened", "source": "Other Herald"}


def test_a_missing_source_element_is_not_an_error():
    # Not every feed carries <source>; dropping the headline over it would
    # lose real news to a cosmetic difference.
    headlines = parse_headlines(FEED, limit=10)

    assert {"title": "A headline with no source element", "source": ""} in headlines


def test_blank_titles_are_skipped():
    headlines = parse_headlines(FEED, limit=10)

    assert all(item["title"].strip() for item in headlines)
    assert len(headlines) == 3


def test_the_limit_is_respected():
    assert len(parse_headlines(FEED, limit=2)) == 2


def test_an_unparseable_feed_raises_rather_than_returning_nothing():
    # The skill catches ParseError and says the feed was unreadable. Silently
    # returning [] here would surface as "no news today", which is a lie.
    import xml.etree.ElementTree as ElementTree

    with pytest.raises(ElementTree.ParseError):
        parse_headlines("<rss><channel><item>", limit=5)


def test_the_first_source_with_results_wins():
    calls = []

    def first(query):
        calls.append("first")
        return ["the answer"]

    def second(query):
        calls.append("second")
        return ["should not be reached"]

    snippets, failures = gather_snippets("anything", sources=(("first", first), ("second", second)))

    assert snippets == ["the answer"]
    assert failures == []
    assert calls == ["first"]


def test_a_raising_source_does_not_end_the_search():
    # The failure this exists for: one endpoint blocking or timing out took
    # the whole search down, and the user saw "no results" for a query the
    # next source would have answered.
    def broken(query):
        raise ConnectionError("blocked")

    def working(query):
        return ["found it anyway"]

    snippets, failures = gather_snippets("anything", sources=(("broken", broken), ("working", working)))

    assert snippets == ["found it anyway"]
    assert failures == ["broken: blocked"]


def test_a_silent_source_falls_through_to_the_next():
    # Returning [] is normal, not an error — the Instant Answer API is quiet
    # for most queries. It must not be mistaken for a failure.
    snippets, failures = gather_snippets(
        "anything",
        sources=(("quiet", lambda query: []), ("loud", lambda query: ["here"])),
    )

    assert snippets == ["here"]
    assert failures == []


def test_every_source_failing_reports_all_of_them():
    def one(query):
        raise TimeoutError("slow")

    def two(query):
        raise ConnectionError("refused")

    snippets, failures = gather_snippets("anything", sources=(("one", one), ("two", two)))

    assert snippets == []
    assert failures == ["one: slow", "two: refused"]
