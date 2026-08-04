# skills/utility/system_check.py
import psutil


class SystemCheckSkill:
    def __init__(self):
        # The manifest is critical. The LLM reads this to know when to trigger the skill.
        self.manifest = {
            "name": "system_check",
            # Steering added when the machine-status skills arrived: this used to
            # be the only one, and "how is the machine doing" now has five
            # plausible answers.
            "description": (
                "Checks the current CPU and RAM usage of the system. Use this for a quick "
                "load reading only — disk_report for storage, network_status for "
                "connectivity, gpu_status for accelerators, and diagnose_self when the "
                "question is whether everything is working."
            ),
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
