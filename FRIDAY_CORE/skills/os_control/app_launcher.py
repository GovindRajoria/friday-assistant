# skills/os_control/app_launcher.py
import platform
import subprocess


class AppLauncherSkill:
    def __init__(self):
        self.manifest = {
            "name": "launch_application",
            "description": "ONLY use this to open local desktop programs (like Chrome, Notepad, Calculator, Spotify). DO NOT use this to search for information, answer questions, or check the weather.",
            "parameters": ["app_name"]
        }
        
        # A map to handle variations in how OSs name their executables
        self.app_map = {
            # Standard Apps
            "chrome": {"Windows": "chrome", "Darwin": "Google Chrome", "Linux": "google-chrome"},
            "google": {"Windows": "chrome", "Darwin": "Google Chrome", "Linux": "google-chrome"}, 
            "notepad": {"Windows": "notepad", "Darwin": "TextEdit", "Linux": "gedit"},
            "calculator": {"Windows": "calc", "Darwin": "Calculator", "Linux": "gnome-calculator"},
            "spotify": {"Windows": "spotify:", "Darwin": "Spotify", "Linux": "spotify"},
            
            # Windows System Tools (The Fix)
            "system configuration": {"Windows": "msconfig"},
            "task manager": {"Windows": "taskmgr"},
            "control panel": {"Windows": "control"},
            "command prompt": {"Windows": "cmd"},
            "settings": {"Windows": "start ms-settings:"}
        }

    def execute(self, params=None):
        if not params or "app_name" not in params:
            return {"status": "error", "message": "I need to know which application to launch."}
        
        app_name = params["app_name"].lower().strip()
        current_os = platform.system()

        # Resolve the app name based on the OS, or default to what the LLM provided
        target_app = app_name
        if app_name in self.app_map and current_os in self.app_map[app_name]:
            target_app = self.app_map[app_name][current_os]

        missing = self._not_installed(target_app, current_os)
        if missing:
            return self._failed(app_name, missing)

        try:
            if current_os == "Windows":
                # Using 'start' handles both standard executables and URI schemes (like spotify:)
                launched = subprocess.Popen(f"start {target_app}", shell=True)
            elif current_os == "Darwin":
                launched = subprocess.Popen(["open", "-a", target_app])
            elif current_os == "Linux":
                launched = subprocess.Popen([target_app])
            else:
                return {"status": "error", "message": f"Unsupported OS: {current_os}"}

            return self._report(app_name, launched)

        except Exception as e:
            return self._failed(app_name, str(e))

    @staticmethod
    def _not_installed(target_app, current_os):
        """Why this cannot be launched, or "" if it looks launchable.

        Checked BEFORE launching, not after, because the exit code cannot be
        trusted to say: `start notepadd` **returns 0**. It pops a "Windows cannot
        find" dialog and reports success, so the previous version replied "I have
        launched notepadd for you" for any name at all — the failure this project
        treats as the worst available, and worse here than most, because the
        operator then watches the screen for a window that is never coming.
        Waiting on the process was tried first and measured useless for exactly
        that reason.

        A URI scheme (`spotify:`, `start ms-settings:`) is left alone: there is no
        executable to find, the shell hands it to whatever is registered, and
        deciding whether that registration exists is a different question.
        """
        if ":" in target_app:
            return ""
        import shutil

        # Only the first word: an entry may carry arguments.
        executable = target_app.split()[0] if target_app.split() else target_app
        if shutil.which(executable):
            return ""
        if current_os == "Windows":
            # `where` also sees App Execution Aliases and the App Paths registry,
            # which is how Windows finds several things that are not on PATH.
            try:
                found = subprocess.run(["where", executable], capture_output=True, timeout=3)
                if found.returncode == 0:
                    return ""
            except Exception:  # noqa: BLE001 — a failed probe must not block a launch
                return ""
        return "I could not find a program by that name on this machine"

    def _report(self, app_name, launched):
        """Say it launched only once it has not immediately failed.

        The pre-flight check above is what actually catches a wrong name; this
        catches the rarer case of a program that exists and refuses to start.
        """
        try:
            code = launched.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            # Still running after half a second, which for `start` and `open`
            # means it is doing something real.
            return {"status": "success", "message": f"I have launched {app_name} for you."}
        if code == 0:
            return {"status": "success", "message": f"I have launched {app_name} for you."}
        return self._failed(app_name, f"the system could not start it (exit code {code})")

    def _failed(self, app_name, reason):
        """Report the failure, and name the closest thing it does know.

        Speech is how this gets asked, and `small.en` mishears program names the
        same way it mishears place names. The suggestion is offered as a question
        and never acted on — see core/nearest.py for why that distinction is not
        timidity but a measured necessity.
        """
        from core.nearest import did_you_mean

        return {"status": "error",
                "message": f"I could not launch {app_name}: {reason}."
                           + did_you_mean(app_name, self.app_map)}

def setup():
    return AppLauncherSkill()
