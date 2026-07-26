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
        
        try:
            if current_os == "Windows":
                # Using 'start' handles both standard executables and URI schemes (like spotify:)
                subprocess.Popen(f"start {target_app}", shell=True)
            elif current_os == "Darwin":
                subprocess.Popen(["open", "-a", target_app])
            elif current_os == "Linux":
                subprocess.Popen([target_app])
            else:
                return {"status": "error", "message": f"Unsupported OS: {current_os}"}
                
            return {"status": "success", "message": f"I have launched {app_name} for you."}
            
        except Exception as e:
            return {"status": "error", "message": f"Failed to launch {app_name}. Error: {str(e)}"}

def setup():
    return AppLauncherSkill()
