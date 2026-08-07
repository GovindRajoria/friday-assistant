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
                # "How is the machine doing" is how this gets asked out loud, and
                # the description contained none of those words — not "machine",
                # not "computer", not "slow". Adding the vocabulary somebody
                # actually uses to the skill that is genuinely the right answer is
                # not the same as the description tuning that was measured moving
                # the attractor around; that was a skill claiming ground next to
                # it, this is a skill failing to claim its own.
                "Checks how the machine is doing right now: current CPU and RAM usage, "
                "whether the computer is busy or running slow. Use this for a quick "
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
