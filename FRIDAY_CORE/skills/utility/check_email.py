# skills/utility/check_email.py
"""Unread subjects and senders over IMAP. Read-only, and only ever read-only.

`send_email` is a separate and later decision, and this skill is written so that
adding it would be a new file rather than a new parameter here — there is no code
path in this module that can send, reply to, delete or mark anything.

**The password is never in a config file.** It comes from an environment variable
(`FRIDAY_EMAIL_PASSWORD` by default), because `config/settings.yaml` is a plain
file that gets opened in editors, pasted into issues, and read back by the settings
skill. An app-specific password is what belongs there — not an account password,
because this is a local assistant and not a place to put the keys to an identity.

**Bodies are never fetched.** Subjects, senders and dates only. A subject line is
enough to answer "anything important?", and pulling bodies would put arbitrary
email — including whatever a stranger chose to send — into a language model's
prompt, which is a prompt-injection surface with no upside here.

Unverified: there is no mail account configured on the development machine, so this
skill has been exercised against its own error paths and its parsing, never against
a live IMAP server. Said plainly in the README too.
"""
import email
import email.header
import imaplib
import os

from core.config import SETTINGS

MAX_MESSAGES = 20
TIMEOUT_SECONDS = 20


class CheckEmailSkill:
    def __init__(self):
        self.manifest = {
            "name": "check_email",
            "description": (
                "Checks an email inbox over IMAP and reports unread messages — who they are "
                "from, their subjects and when they arrived. Parameters: optionally 'count' "
                "for how many, and 'mailbox' for a folder other than the inbox. Read-only: "
                "it cannot send, reply to, delete or mark anything, and it never reads "
                "message bodies. Use this when asked about email, unread messages or the "
                "inbox."
            ),
            "parameters": ["count", "mailbox"],
        }

    def execute(self, params=None):
        params = params or {}
        config = SETTINGS.get("email", {})
        host = str(config.get("imap_host") or "").strip()
        username = str(config.get("username") or "").strip()

        if not host or not username:
            return {
                "status": "error",
                "message": ("No mail account is configured. Set email.imap_host and "
                            "email.username in config/settings.yaml, and put the app password "
                            f"in the {config.get('password_env_var', 'FRIDAY_EMAIL_PASSWORD')} "
                            "environment variable — never in the config file."),
            }

        variable = str(config.get("password_env_var") or "FRIDAY_EMAIL_PASSWORD")
        password = os.environ.get(variable, "")
        if not password:
            return {
                "status": "error",
                "message": (f"The {variable} environment variable is not set, so I have no "
                            "password for the mail account. Use an app-specific password, not "
                            "the account password."),
            }

        try:
            count = max(1, min(int(params.get("count") or MAX_MESSAGES), MAX_MESSAGES))
        except (TypeError, ValueError):
            count = MAX_MESSAGES
        mailbox = str(params.get("mailbox") or config.get("mailbox") or "INBOX")

        return self._fetch(host, int(config.get("imap_port", 993)), username, password,
                           mailbox, count)

    def _fetch(self, host, port, username, password, mailbox, count):
        connection = None
        try:
            connection = imaplib.IMAP4_SSL(host, port, timeout=TIMEOUT_SECONDS)
            connection.login(username, password)
            # readonly=True is belt and braces: nothing below writes, and this
            # also stops the server marking messages seen on examine.
            status, _ = connection.select(mailbox, readonly=True)
            if status != "OK":
                return {"status": "error", "message": f"There is no mailbox called '{mailbox}'."}

            status, data = connection.search(None, "UNSEEN")
            if status != "OK":
                return {"status": "error", "message": "The server refused the search for unread mail."}

            ids = (data[0] or b"").split()
            if not ids:
                return {
                    "status": "success",
                    "message": f"No unread messages in {mailbox}.",
                    "data": {"unread": 0, "mailbox": mailbox},
                }

            # Newest first, and only the headers — never RFC822, which would pull
            # the body and put arbitrary email into a model's prompt.
            wanted = ids[-count:][::-1]
            lines = []
            for message_id in wanted:
                status, payload = connection.fetch(
                    message_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"
                )
                if status != "OK" or not payload or not isinstance(payload[0], tuple):
                    continue
                headers = email.message_from_bytes(payload[0][1])
                lines.append(f"  {self._decode(headers.get('From'))} — "
                             f"{self._decode(headers.get('Subject')) or '(no subject)'} "
                             f"({self._decode(headers.get('Date'))})")

            return {
                "status": "success",
                "message": (f"{len(ids)} unread message(s) in {mailbox}"
                            + (f", most recent {len(lines)}" if len(ids) > len(lines) else "")
                            + ":\n" + "\n".join(lines)),
                "data": {"unread": len(ids), "shown": len(lines), "mailbox": mailbox},
            }
        except imaplib.IMAP4.error as error:
            # Login failures land here, and the message must not echo the password.
            return {
                "status": "error",
                "message": (f"The mail server rejected the connection for {username}: {error}. "
                            "If two-factor authentication is on, this needs an app-specific "
                            "password rather than the account password."),
            }
        except OSError as error:
            return {"status": "error", "message": f"Could not reach {host}:{port} — {error}"}
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:                                     # noqa: BLE001
                    pass
                try:
                    connection.logout()
                except Exception:                                     # noqa: BLE001
                    pass

    @staticmethod
    def _decode(raw):
        """MIME-encoded headers to readable text. 'Subject: =?utf-8?B?...?=' is normal."""
        if not raw:
            return ""
        try:
            parts = email.header.decode_header(raw)
        except Exception:                                             # noqa: BLE001
            return str(raw)
        rendered = []
        for value, charset in parts:
            if isinstance(value, bytes):
                rendered.append(value.decode(charset or "utf-8", errors="replace"))
            else:
                rendered.append(str(value))
        return " ".join(" ".join(rendered).split())


def setup():
    return CheckEmailSkill()
