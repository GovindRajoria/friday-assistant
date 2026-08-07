# skills/utility/world_time.py
"""The clock. Nothing in this project reported one until now.

"What time is it" is the most common thing anyone says to an assistant, and it
scored 0 out of 3 in tools/routing_bench.py — not because routing was confused,
but because there was no skill to route to and no date anywhere in the prompt.
The model answered from nothing, which for a clock means answering wrongly.

**Most of that gap is closed elsewhere, in one line.** The local date and time is
now injected into every prompt by core/prompts.py, so the ordinary case needs no
tool call at all — asking a language model to invoke a tool to learn what day it
is would be an absurd amount of machinery for a value the process already has.
This skill exists for the parts a static timestamp cannot answer: another
timezone, and arithmetic between dates.

Timezones come from `zoneinfo`, which on Windows needs the `tzdata` package (in
requirements.txt for exactly this). Imported lazily so that a host without it
still loads the skill and reports the reason, rather than vanishing from the
registry at import time.
"""
import re
from datetime import date, datetime, timedelta

# Places whose IANA name cannot be guessed from what people call them. Everything
# else is matched against the real timezone database rather than a table here,
# which is why this list is four entries instead of two hundred.
ALIASES = {
    "uk": "Europe/London", "britain": "Europe/London", "england": "Europe/London",
    "india": "Asia/Kolkata", "ist": "Asia/Kolkata", "bhopal": "Asia/Kolkata",
    "delhi": "Asia/Kolkata", "mumbai": "Asia/Kolkata", "bangalore": "Asia/Kolkata",
    "bengaluru": "Asia/Kolkata", "pune": "Asia/Kolkata", "hyderabad": "Asia/Kolkata",
    "us": "America/New_York", "usa": "America/New_York", "est": "America/New_York",
    "pst": "America/Los_Angeles", "california": "America/Los_Angeles",
    "utc": "UTC", "gmt": "UTC",
    "japan": "Asia/Tokyo", "china": "Asia/Shanghai", "korea": "Asia/Seoul",
    "germany": "Europe/Berlin", "france": "Europe/Paris", "uae": "Asia/Dubai",
    "australia": "Australia/Sydney", "singapore": "Asia/Singapore",
}

# Recurring dates people ask about by name rather than by date.
NAMED_DATES = {
    "christmas": (12, 25), "christmas day": (12, 25), "xmas": (12, 25),
    "new year": (1, 1), "new years": (1, 1), "new year's": (1, 1),
    "new years day": (1, 1), "new year's day": (1, 1),
}


class WorldTimeSkill:
    def __init__(self):
        self.manifest = {
            "name": "world_time",
            "description": (
                "The clock and the calendar in places other than here, and arithmetic between "
                "dates. Use this for: what time is it in Tokyo, what is the time difference "
                "with London, how many days until Christmas, how long until the 3rd of March, "
                "what day of the week is the 14th. You are already told the local date and "
                "time, so you do not need this to answer what today is or what time it is "
                "here. Its answer is complete — the turn ends there."
            ),
            "parameters": ["action", "place", "date"],
            "terminal": True,
        }

    def _zone(self, place):
        """An IANA timezone for what somebody called a place, or None.

        Matched against `zoneinfo.available_timezones()` rather than a table:
        every name in that set already ends in the city it belongs to, so
        "tokyo" finds "Asia/Tokyo" and "sao paulo" finds "America/Sao_Paulo"
        without anyone maintaining a list of the world's cities here.

        **Several candidates are tried, not one.** The first live use of this
        skill was asked for the time and the model passed place="Delhi, India" —
        a perfectly ordinary way to name a place, and an exact match for nothing
        at all, so the answer to "what's the time" was a complaint about timezone
        names. A place arrives as the model or the operator writes it: with a
        country after it, with "city of" in front, occasionally with the question
        mark still attached. Splitting on the punctuation and trying each part
        costs one loop and removes a whole class of that.
        """
        from zoneinfo import ZoneInfo, available_timezones

        wanted = str(place or "").strip().lower().strip("?.!")
        if not wanted:
            return None

        # Whole string first, so "New York" is not answered by "York" and an
        # explicit "Asia/Kolkata" never gets taken apart.
        candidates = [wanted]
        candidates += [part.strip() for part in re.split(r"[,/;]| in | near ", wanted) if part.strip()]
        # Last resort, single words: "the time in delhi india please" survives this.
        candidates += wanted.replace(",", " ").split()

        zones = {name.lower(): name for name in available_timezones()}
        tails = {}
        for lowered, name in zones.items():
            # First one wins, so "Asia/Kolkata" is not shadowed by a later
            # deprecated alias pointing at the same place.
            tails.setdefault(lowered.rsplit("/", 1)[-1], name)

        for candidate in candidates:
            if candidate in ALIASES:
                return ZoneInfo(ALIASES[candidate])
            target = candidate.replace(" ", "_").replace("-", "_")
            if target in zones:
                return ZoneInfo(zones[target])
            if target in tails:
                return ZoneInfo(tails[target])
        return None

    def _as_date(self, raw):
        """A date from what the model passed, or None.

        Accepts an ISO date, a month-day pair, and the handful of dates people ask
        about by name. A bare month-day or a name means the *next* one — asking
        how long until Christmas on Boxing Day means next Christmas, not minus one.
        """
        text = str(raw or "").strip().lower().rstrip("?.")
        if not text:
            return None
        today = date.today()

        # The model passes date="now" alongside a place, and date="today" when it
        # means today. Both were unparseable, which turned an answerable question
        # into an error about date formats.
        if text in ("now", "today", "currently", "right now", "this day"):
            return today
        if text == "tomorrow":
            return today + timedelta(days=1)
        if text == "yesterday":
            return today - timedelta(days=1)

        if text in NAMED_DATES:
            month, day = NAMED_DATES[text]
            candidate = date(today.year, month, day)
            return candidate if candidate >= today else date(today.year + 1, month, day)

        for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y"):
            try:
                return datetime.strptime(text, pattern).date()
            except ValueError:
                continue

        for pattern in ("%m-%d", "%d %B", "%d %b", "%B %d", "%b %d"):
            try:
                parsed = datetime.strptime(text, pattern)
            except ValueError:
                continue
            candidate = date(today.year, parsed.month, parsed.day)
            return candidate if candidate >= today else date(today.year + 1, parsed.month, parsed.day)
        return None

    def _time_in(self, place):
        try:
            zone = self._zone(place)
        except Exception as error:
            return {"status": "error",
                    "message": (f"I could not look up timezones ({error}). The timezone database "
                                "may be missing — 'pip install tzdata' installs it.")}
        if zone is None:
            return {"status": "error",
                    "message": f"I do not know a timezone for '{place}'. A city or country name, "
                               "or an IANA name like Asia/Tokyo, will work."}

        there = datetime.now(zone)
        here = datetime.now().astimezone()
        return {"status": "success",
                "message": (f"It is {_clock(there)} on {_day(there)} in {zone}, "
                            f"{_relation(there, here)}.")}

    def _until(self, raw):
        target = self._as_date(raw)
        if target is None:
            return {"status": "error",
                    "message": f"I could not read '{raw}' as a date. A date like 2026-12-25, "
                               "or a name like Christmas, will work."}
        days = (target - date.today()).days
        when = target.strftime("%A %d %B %Y")
        if days == 0:
            return {"status": "success", "message": f"{when} is today."}
        if days == 1:
            return {"status": "success", "message": f"{when} is tomorrow."}
        if days == -1:
            return {"status": "success", "message": f"{when} was yesterday."}
        if days < 0:
            return {"status": "success", "message": f"{when} was {abs(days)} days ago."}
        weeks = days / 7
        rounded = f", about {weeks:.0f} weeks" if days >= 14 else ""
        return {"status": "success", "message": f"{days} days until {when}{rounded}."}

    def execute(self, params=None):
        params = params or {}
        action = str(params.get("action", "")).strip().lower().replace(" ", "_")
        place = params.get("place")
        raw_date = params.get("date")

        # The model routes here with the parameter filled in and the action
        # missing about as often as the other way round, so both are used as
        # evidence rather than trusting `action` alone. Getting this wrong means
        # answering a question about Tokyo with the local time, which reads as a
        # confident wrong answer rather than a failure.
        if action in ("until", "days_until", "how_long_until", "countdown") or (raw_date and not place):
            return self._until(raw_date or place)
        if place:
            return self._time_in(place)
        if action in ("time_in", "in", "timezone", "difference", "time_difference"):
            return {"status": "error", "message": "Which place did you mean?"}

        # Falling back to here rather than erroring. The prompt already carries
        # the local time, so arriving with nothing means the model reached for a
        # tool it did not need — answering it is more useful than a complaint.
        now = datetime.now().astimezone()
        return {"status": "success",
                "message": f"It is {_clock(now)} on {_day(now)} {now.year}."}


def _relation(there, here):
    """"5 hours 30 minutes behind here", not "5.5 hours".

    A sixth of the world is not on a whole-hour offset — Kolkata is +5:30 and
    Kathmandu +5:45, and this assistant runs in the first of those — so a decimal
    is both unnatural to hear and, rounded to one place, sometimes just wrong:
    the 45-minute gap between those two came out as "0.2 hours ahead".
    """
    minutes = round((there.utcoffset() - here.utcoffset()).total_seconds() / 60)
    if minutes == 0:
        return "the same time as here"
    direction = "ahead of" if minutes > 0 else "behind"
    hours, remainder = divmod(abs(minutes), 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if remainder:
        parts.append(f"{remainder} minute{'s' if remainder != 1 else ''}")
    return f"{' '.join(parts)} {direction} here"


def _day(moment):
    """"Friday 7 August". `%d` zero-pads, and "Friday oh-seven August" is not a
    date anybody says — which matters because this is read aloud."""
    return f"{moment:%A} {moment.day} {moment:%B}"


def _clock(moment):
    """"9:05 am", not "09:05 AM".

    The zero-padded form is what `%I` gives and it is not how anyone says a time
    — which matters more than usual here, because this answer is read aloud.
    `%-I` would do it on glibc and raises on the Windows CRT, so the padding is
    stripped by hand instead.
    """
    return moment.strftime("%I:%M %p").lstrip("0").lower()


def setup():
    return WorldTimeSkill()
