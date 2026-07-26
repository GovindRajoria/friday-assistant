# skills/utility/system_check.py
import psutil


class SystemCheckSkill:
    def __init__(self):
        # The manifest is critical. The LLM reads this to know when to trigger the skill.
        self.manifest = {
            "name": "system_check",
            "description": "Checks the current CPU and RAM usage of the system.",
            "parameters": []
        }

    def execute(self, params=None):
        cpu_usage = psutil.cpu_percent(interval=1)
        ram_usage = psutil.virtual_memory().percent
        
        return {
            "status": "success",
            "message": f"System is operating normally. CPU at {cpu_usage}% and RAM at {ram_usage}%."
        }

# FRIDAY will look for this specific function to load the skill
def setup():
    return SystemCheckSkill()
