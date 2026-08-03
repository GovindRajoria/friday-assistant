# tests/test_new_skills.py
"""Formatting and extraction for the skills added alongside read_news.

Same principle as tests/test_web_sources.py: the network calls are checked by
hand, and what is gated here is the part that degrades quietly when an
upstream payload or a page's markup shifts under it.
"""
from skills.web.read_webpage import extract_text
from skills.web.weather import describe_code, format_report


def test_a_known_weather_code_reads_as_english():
    assert describe_code(95) == "thunderstorm"
    assert describe_code(0) == "clear sky"


def test_an_unknown_weather_code_says_so_rather_than_guessing():
    # Falling back to "clear sky" for an unmapped code would report good
    # weather in a storm. Open-Meteo can add codes at any time.
    assert describe_code(7) == "weather code 7"
    assert describe_code(None) == "unknown conditions"
    assert describe_code("banana") == "unknown conditions"


def test_the_report_carries_the_current_conditions():
    message = format_report(
        "Delhi, India",
        {"weather_code": 95, "temperature_2m": 29.8, "apparent_temperature": 36.5,
         "relative_humidity_2m": 81, "wind_speed_10m": 6.0},
        {},
    )

    assert "Delhi, India" in message
    assert "thunderstorm" in message
    assert "29.8" in message
    assert "36.5" in message


def test_the_outlook_pairs_each_day_with_its_own_numbers():
    message = format_report(
        "Somewhere",
        {"weather_code": 0, "temperature_2m": 20},
        {"time": ["2026-08-03", "2026-08-04"],
         "temperature_2m_max": [33.0, 30.9],
         "temperature_2m_min": [27.1, 25.9],
         "weather_code": [95, 3]},
    )

    assert "2026-08-03: 27.1–33.0°C, thunderstorm" in message
    assert "2026-08-04: 25.9–30.9°C, overcast" in message


def test_a_short_daily_series_truncates_instead_of_raising():
    # A payload missing one series is an upstream change, not a reason to
    # crash a turn. zip stopping at the shortest is the intended behaviour.
    message = format_report(
        "Somewhere",
        {"weather_code": 0, "temperature_2m": 20},
        {"time": ["2026-08-03", "2026-08-04"],
         "temperature_2m_max": [33.0],
         "temperature_2m_min": [27.1],
         "weather_code": [95]},
    )

    assert "2026-08-03" in message
    assert "2026-08-04" not in message


def test_a_daily_payload_with_nothing_in_it_gives_current_conditions_only():
    message = format_report("Somewhere", {"weather_code": 0, "temperature_2m": 20}, {})

    assert "Next few days" not in message
    assert "clear sky" in message


PAGE = """<html><head><title>An Article</title></head><body>
<nav>Home About Contact Subscribe now</nav>
<script>console.log("tracking pixel goes here and is quite long indeed");</script>
<article>
  <p>Short bit.</p>
  <p>This is the first real paragraph of the article and it is comfortably longer than the cutoff.</p>
  <p>And here is a second substantial paragraph, also well past the minimum length threshold.</p>
</article>
<footer>Copyright some publisher, all rights reserved, terms and conditions apply here</footer>
</body></html>"""


def test_the_title_and_body_paragraphs_are_extracted():
    title, text = extract_text(PAGE)

    assert title == "An Article"
    assert "first real paragraph" in text
    assert "second substantial paragraph" in text


# Deliberately has NO <article> element, so extraction falls back to <body>
# and the noise tags are genuinely in scope.
#
# The first version of the test below used PAGE, which does have an <article>.
# nav, script and footer sit outside it, so scoping alone excluded them and
# the assertions passed with NOISE_TAGS emptied out — verified by breaking it
# on purpose. A test that cannot fail is not a gate, and most real pages are
# this shape rather than PAGE's.
UNSTRUCTURED_PAGE = """<html><head><title>No Article Element</title></head><body>
<nav><p>Home About Contact Subscribe now to our newsletter for daily updates</p></nav>
<script>console.log("tracking pixel goes here and it is quite long indeed");</script>
<div>
  <p>This is the actual story text and it is comfortably longer than the cutoff.</p>
</div>
<footer><p>Copyright some publisher, all rights reserved, terms and conditions apply</p></footer>
</body></html>"""


def test_navigation_scripts_and_footers_are_stripped():
    # Left in, these are what the model ends up summarising — a cookie banner
    # instead of the story.
    _, text = extract_text(UNSTRUCTURED_PAGE)

    assert "actual story text" in text
    assert "Subscribe now" not in text
    assert "tracking pixel" not in text
    assert "all rights reserved" not in text


def test_caption_length_fragments_are_dropped():
    _, text = extract_text(PAGE)

    assert "Short bit." not in text


def test_long_pages_are_truncated_with_a_marker():
    long_page = "<html><body><p>" + ("word " * 4000) + "</p></body></html>"

    _, text = extract_text(long_page, max_chars=200)

    assert len(text) < 300
    assert "truncated" in text


def test_a_page_with_no_paragraphs_still_yields_its_text():
    # Some pages are one big <div>. Reporting "no readable text" for those
    # would be wrong — the text is right there, just not in <p> tags.
    _, text = extract_text("<html><body><div>Everything lives in a single div here.</div></body></html>")

    assert "single div" in text


def test_a_real_sentence_is_accepted_as_a_description():
    from skills.vision.describe_screen import looks_like_a_description

    assert looks_like_a_description("A computer screen with a text document open.")
    assert looks_like_a_description("Two browser windows side by side showing code")


def test_identifier_shaped_junk_is_rejected():
    # Observed twice from moondream: once with a bad image encoding, once
    # from a perfectly good PNG. Passed through, the assistant reports a URN
    # as the contents of the screen.
    from skills.vision.describe_screen import looks_like_a_description

    assert not looks_like_a_description("urn:ietf:wg:ac:200")
    assert not looks_like_a_description("urn:1f6c8b0")


def test_empty_and_near_empty_output_is_rejected():
    from skills.vision.describe_screen import looks_like_a_description

    assert not looks_like_a_description("")
    assert not looks_like_a_description("   ")
    assert not looks_like_a_description("a screen")


def test_a_sentence_that_merely_contains_a_colon_is_still_a_description():
    # The check has to look at the shape of the first token, not just for a
    # colon anywhere — plenty of real descriptions contain one.
    from skills.vision.describe_screen import looks_like_a_description

    assert looks_like_a_description("A terminal showing: build succeeded in 4 seconds")
