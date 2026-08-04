# skills/dev/inspect_repo.py
"""Read-only git: what branch, what is dirty, what changed recently.

This answers "what was I doing in this repo", which is the question that comes up
after a weekend. There is no `gh` CLI on this machine and no network call here —
everything comes from the local repository.

Read-only is enforced by construction rather than by intention: the subcommands
are a fixed allowlist of `status`, `log`, `diff --stat`, `branch` and
`remote -v`, assembled here as argument lists. The model chooses *which* of those
runs, never the arguments, so there is no path by which "inspect the repo" becomes
`git reset --hard`. That is why this skill is not marked destructive while
`run_tests` next door is.
"""
import subprocess

from core.project_roots import resolve_in, roots, unconfigured

GIT_TIMEOUT_SECONDS = 20
MAX_LINES = 40


class InspectRepoSkill:
    def __init__(self):
        self.manifest = {
            "name": "inspect_repo",
            "description": (
                "Reports the state of a local git repository: current branch, uncommitted "
                "changes, recent commits, and what changed in them. Use this when asked "
                "what you were working on, what is uncommitted, what changed recently, or "
                "which branch a project is on. Parameters: 'path' for the repository and "
                "'action' (status, log, diff, branches). Read-only — it cannot commit, push "
                "or change anything. Use search_code to find code inside a project instead."
            ),
            "parameters": ["path", "action"],
        }

    def execute(self, params=None):
        params = params or {}
        if not roots("projects"):
            return unconfigured("projects", "look at a repository")

        resolved = resolve_in("projects", params.get("path"))
        if resolved is None:
            from core.project_roots import outside
            return outside("projects", str(params.get("path")))
        if not (resolved / ".git").exists():
            return {"status": "error", "message": f"'{resolved}' is not a git repository."}

        action = str(params.get("action") or "status").lower()
        try:
            if action in {"log", "commits", "history"}:
                return self._log(resolved)
            if action in {"diff", "changes", "changed"}:
                return self._diff(resolved)
            if action in {"branches", "branch"}:
                return self._branches(resolved)
            return self._status(resolved)
        except FileNotFoundError:
            return {"status": "error", "message": "git is not installed, or not on PATH."}
        except subprocess.TimeoutExpired:
            return {"status": "error", "message": f"git did not respond within {GIT_TIMEOUT_SECONDS}s."}

    # ---- actions ----------------------------------------------------------

    def _status(self, repo):
        branch = self._git(repo, ["branch", "--show-current"]).strip() or "(detached HEAD)"
        porcelain = self._git(repo, ["status", "--porcelain"]).splitlines()
        last = self._git(repo, ["log", "-1", "--pretty=%h %s (%cr)"]).strip()

        if not porcelain:
            state = "The tree is clean."
        else:
            shown = [f"  {line}" for line in porcelain[:MAX_LINES]]
            more = f"\n  ... and {len(porcelain) - MAX_LINES} more" if len(porcelain) > MAX_LINES else ""
            state = f"{len(porcelain)} uncommitted change(s):\n" + "\n".join(shown) + more

        return {
            "status": "success",
            "message": (f"{repo.name}: on branch '{branch}'. {state}\n"
                        f"Last commit: {last or '(no commits yet)'}"),
            "data": {"branch": branch, "dirty": len(porcelain), "path": str(repo)},
        }

    def _log(self, repo):
        output = self._git(repo, ["log", "-10", "--pretty=%h %s (%an, %cr)"]).strip()
        if not output:
            return {"status": "success", "message": f"{repo.name} has no commits yet."}
        return {
            "status": "success",
            "message": f"Last {len(output.splitlines())} commit(s) in {repo.name}:\n" +
                       "\n".join(f"  {line}" for line in output.splitlines()),
            "data": {"commits": len(output.splitlines())},
        }

    def _diff(self, repo):
        unstaged = self._git(repo, ["diff", "--stat"]).strip()
        staged = self._git(repo, ["diff", "--cached", "--stat"]).strip()
        if not unstaged and not staged:
            return {
                "status": "success",
                "message": f"Nothing is changed in {repo.name} — no staged or unstaged edits.",
                "data": {"changed": False},
            }
        parts = []
        if staged:
            parts.append("Staged:\n" + "\n".join(f"  {line}" for line in staged.splitlines()[:MAX_LINES]))
        if unstaged:
            parts.append("Unstaged:\n" + "\n".join(f"  {line}" for line in unstaged.splitlines()[:MAX_LINES]))
        return {"status": "success", "message": f"{repo.name}:\n" + "\n".join(parts),
                "data": {"changed": True}}

    def _branches(self, repo):
        output = self._git(repo, ["branch", "-vv"]).strip()
        remotes = self._git(repo, ["remote", "-v"]).strip().splitlines()
        remote_line = f"\nRemote: {remotes[0]}" if remotes else "\nNo remote configured."
        return {
            "status": "success",
            "message": (f"Branches in {repo.name}:\n" +
                        "\n".join(f"  {line}" for line in output.splitlines()[:MAX_LINES]) +
                        remote_line),
            "data": {"branches": len(output.splitlines())},
        }

    # ---- the only place a git process is started --------------------------

    @staticmethod
    def _git(repo, arguments: list[str]) -> str:
        """Run one read-only git subcommand. `arguments` is built here, never by the model."""
        result = subprocess.run(
            ["git", "-C", str(repo), *arguments],
            capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS,
        )
        # git writes advice to stderr and still succeeds; only a non-zero exit
        # with no stdout is worth reporting as a failure.
        if result.returncode != 0 and not result.stdout.strip():
            raise RuntimeError(result.stderr.strip() or f"git exited {result.returncode}")
        return result.stdout


def setup():
    return InspectRepoSkill()
