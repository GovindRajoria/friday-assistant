# skills/os_control/open_url.py
"""Open a link in the default browser.

Trivial, constantly useful, and the natural other half of `web_search`: finding a
page and then actually looking at it should not require typing the URL back in by
hand.

Not marked destructive, and that is a judgement worth stating. It opens a window;
it does not write, delete or synthesise input. The one thing that *would* make it
dangerous is a non-http scheme — `file://` would exfiltrate local files into a
browser, and on Windows a bare path or a `ms-`/`shell:` URI can launch an
application rather than navigate. So the scheme is allowlisted to http and https,
and anything else is refused rather than passed to the shell.
"""
import re
import webbrowser

ALLOWED_SCHEMES = ("http://", "https://")
# Anything of the form "word:" is a scheme, with or without the slashes. Windows
# is full of the slashless kind — ms-settings:, shell:, search-ms: — and they
# launch applications rather than navigating.
SCHEME_PREFIX = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


class OpenUrlSkill:
    def __init__(self):
        self.manifest = {
            "name": "open_url",
            "description": (
                "Opens a web address in the default browser so the user can look at it. "
                "Parameters: 'url'. Use this after finding a page with web_search when the "
                "user wants to see it themselves. Use read_webpage instead to fetch a page's "
                "text so you can answer from it — this one shows it to the human and returns "
                "nothing you can read."
            ),
            "parameters": ["url"],
        }

    def execute(self, params=None):
        params = params or {}
        url = str(params.get("url") or "").strip()
        if not url:
            return {"status": "error", "message": "I need a web address to open."}

        # A bare domain — "example.com/page" — is what a model usually produces,
        # so https is assumed for it. But only when there is no scheme at all.
        #
        # Testing for "://" alone is not enough, and this was found by a test
        # rather than by reading the code: `ms-settings:privacy` and
        # `shell:startup` contain no slashes, so they looked scheme-less and got
        # "https://" pasted on the front, producing a URL that passed the check
        # below. Slashless schemes are the ones that launch applications on
        # Windows, so they are exactly the wrong thing to wave through.
        if SCHEME_PREFIX.match(url):
            if not url.lower().startswith(ALLOWED_SCHEMES):
                scheme = url.split(":", 1)[0]
                return {
                    "status": "error",
                    "message": (f"I only open http and https addresses, not '{scheme}:'. "
                                "Other schemes can read local files or launch applications, "
                                "which is not what opening a link should do."),
                }
        else:
            url = f"https://{url}"

        try:
            opened = webbrowser.open(url)
        except Exception as error:                                    # noqa: BLE001
            return {"status": "error", "message": f"Could not open '{url}': {error}"}

        if not opened:
            return {
                "status": "error",
                "message": f"No browser was available to open '{url}'.",
            }
        return {
            "status": "success",
            "message": f"Opened {url} in the browser.",
            "data": {"url": url},
        }


def setup():
    return OpenUrlSkill()
