# skills/web/track_price.py
"""Watch a number on a page over time, through the proactive scheduler.

**Shipped disabled, on purpose.** The project's own plan defers this until the
proactive scheduler has proven itself over a few weeks, and that has not happened —
so `skills.disabled` carries `track_price` by default and turning it on is a
deliberate act. Everything here works and none of it has run over the weeks that
would justify trusting it, and those are different statements.

Two honest limits, stated in the reply rather than buried here:

**Scraping a price is brittle.** A CSS-free regex over page text finds a currency
figure; a site redesign, a cookie wall, or a price rendered by JavaScript breaks it,
and the failure looks like "no price found" rather than an error. So a recorded
price always comes with the text it was taken from, and a check that finds nothing
says the page changed rather than that the price is gone.

**It records; it does not judge.** The skill stores observations and reports the
change between them. It does not decide a price is "good" — that would be a model
inferring intent from a number, and the operator can read a trend themselves.
"""
import re
from datetime import datetime, timezone

from core import notes_store

KIND = "tasks"          # same store, tagged; see _records() for why this is safe
TAG = "price_watch"
MAX_WATCHES = 25
# A currency figure: symbol-then-number or number-then-code, with thousands
# separators optional. Deliberately not trying to be exhaustive.
PRICE = re.compile(
    r"(?:(?P<symbol>[$£€¥₹])\s?(?P<amount1>\d[\d,\s]*(?:\.\d{1,2})?)"
    r"|(?P<amount2>\d[\d,\s]*(?:\.\d{1,2})?)\s?(?P<code>USD|EUR|GBP|INR|JPY|AUD|CAD))",
    re.IGNORECASE,
)


class TrackPriceSkill:
    def __init__(self):
        self.manifest = {
            "name": "track_price",
            "description": (
                "Watches the price on a web page over time: adds a page to watch, checks it "
                "now and records what it found, or reports how a watched price has moved. "
                "Parameters: 'action' (watch, check, list, forget), 'url', 'label', and 'id'. "
                "Use read_webpage instead for a one-off look at a page — this one keeps a "
                "history so a change can be reported later."
            ),
            "parameters": ["action", "url", "label", "id"],
        }

    def execute(self, params=None):
        params = params or {}
        action = str(params.get("action") or "list").lower()

        if action in {"watch", "add", "track"}:
            return self._watch(params.get("url"), params.get("label"))
        if action in {"check", "update", "refresh"}:
            return self._check(params.get("id"))
        if action in {"forget", "remove", "stop", "delete"}:
            return self._forget(params.get("id"))
        if action in {"list", "show", "history"}:
            return self._list()
        return {"status": "error",
                "message": f"Unknown price action '{action}'. Use watch, check, list or forget."}

    # ---- actions ----------------------------------------------------------

    def _watch(self, url, label):
        url = str(url or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            return {"status": "error", "message": "I need the http or https address of a page to watch."}
        if len(self._records()) >= MAX_WATCHES:
            return {"status": "error",
                    "message": f"I am already watching {MAX_WATCHES} pages, which is the limit."}

        found, price, snippet, error = self._scrape(url)
        if not found:
            return {
                "status": "error",
                "message": (f"I could not find a price on that page, so I have not started "
                            f"watching it: {error}. If the price is drawn by JavaScript I "
                            "cannot see it — I only read the HTML the server sends."),
            }

        try:
            record = notes_store.add(KIND, {
                "tag": TAG,
                "text": str(label or url)[:120],
                "url": url,
                "observations": [{"price": price, "snippet": snippet,
                                  "at": datetime.now(timezone.utc).isoformat()}],
            })
        except (ValueError, OSError) as as_error:
            return {"status": "error", "message": f"I could not save that watch: {as_error}"}

        return {
            "status": "success",
            "message": (f"Watching '{record['text']}' at {price} (read from \"{snippet}\"). "
                        f"id {record['id']}. Ask me to check it later."),
            "data": {"id": record["id"], "price": price},
        }

    def _check(self, record_id):
        records = self._records()
        if not records:
            return {"status": "success", "message": "I am not watching any prices.",
                    "data": {"checked": 0}}

        targets = [r for r in records if r["id"] == str(record_id or "").strip()] if record_id else records
        if not targets:
            return {"status": "error", "message": f"There is no price watch with id {record_id}."}

        lines = []
        for record in targets:
            found, price, snippet, error = self._scrape(record.get("url", ""))
            if not found:
                lines.append(f"  {record['text']}: I could not read a price now ({error}). "
                             "The page has probably changed.")
                continue

            observations = list(record.get("observations") or [])
            previous = observations[-1] if observations else None
            observations.append({"price": price, "snippet": snippet,
                                 "at": datetime.now(timezone.utc).isoformat()})
            notes_store.update(KIND, record["id"], {"observations": observations[-50:]})

            lines.append(f"  {record['text']}: {price}{self._movement(previous, price)}")

        return {
            "status": "success",
            "message": f"Checked {len(targets)} watched page(s):\n" + "\n".join(lines),
            "data": {"checked": len(targets)},
        }

    def _list(self):
        records = self._records()
        if not records:
            return {"status": "success", "message": "I am not watching any prices.",
                    "data": {"watches": 0}}
        lines = []
        for record in records:
            observations = record.get("observations") or []
            first = observations[0]["price"] if observations else "?"
            latest = observations[-1]["price"] if observations else "?"
            movement = (f", first seen at {first}" if len(observations) > 1 and first != latest else "")
            lines.append(f"  {record['id']}: {record['text']} — {latest}"
                         f" ({len(observations)} reading(s){movement})")
        return {"status": "success",
                "message": f"{len(records)} watched page(s):\n" + "\n".join(lines),
                "data": {"watches": len(records)}}

    def _forget(self, record_id):
        record_id = str(record_id or "").strip()
        if not record_id:
            return {"status": "error", "message": "Which watch should I forget? Ask me to list them."}
        if notes_store.remove(KIND, record_id):
            return {"status": "success", "message": f"Stopped watching {record_id}."}
        return {"status": "error", "message": f"There is no price watch with id {record_id}."}

    # ---- helpers ----------------------------------------------------------

    @staticmethod
    def _records():
        """Only this skill's records.

        The tasks store is shared with task_list, and the tag is what keeps them
        apart. Filtering on it here means a price watch never appears in the
        to-do list and a task never gets scraped — checked by a test, because a
        shared store with an unenforced convention is a bug waiting for a
        rename.
        """
        return [record for record in notes_store.load(KIND) if record.get("tag") == TAG]

    def _scrape(self, url):
        """(found, price_text, snippet, error). Never raises."""
        if not url:
            return False, "", "", "no URL stored"
        try:
            import requests

            response = requests.get(
                url, timeout=15,
                headers={"User-Agent": "Mozilla/5.0 (compatible; local assistant)"},
            )
            response.raise_for_status()
        except Exception as error:                                    # noqa: BLE001
            return False, "", "", f"the page could not be fetched: {error}"

        try:
            from bs4 import BeautifulSoup

            text = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)
        except Exception:                                             # noqa: BLE001
            text = response.text

        match = PRICE.search(text)
        if not match:
            return False, "", "", "no currency figure appears in the page text"

        rendered = match.group(0).strip()
        start = max(0, match.start() - 40)
        snippet = " ".join(text[start:match.end() + 40].split())
        return True, rendered, snippet, ""

    @staticmethod
    def _movement(previous, current):
        """A plain statement of change. No judgement about whether it is a good price."""
        if not previous:
            return " (first reading)"
        before = previous.get("price", "")
        if before == current:
            return f" (unchanged since {previous.get('at', '')[:10]})"
        return f" (was {before})"


def setup():
    return TrackPriceSkill()
