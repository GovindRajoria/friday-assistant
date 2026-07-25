# skills/utility/identity.py

class CoreIdentitySkill:
    def __init__(self):
        self.manifest = {
            "name": "core_identity",
            "description": "Use this specifically when the user asks who you are, what you can do, what your skills are, or what your purpose is. DO NOT use this for web searches.",
            "parameters": []
        }

    def execute(self, params=None):
        identity_speech = (
            "I am FRIDAY, your autonomous AI assistant. My current Antigravity modules "
            "allow me to search the live web for news and weather, control your operating "
            "system, launch applications, check system diagnostics, and draft documents. "
            "How can I help you today?"
        )
        return {"status": "success", "message": identity_speech}

def setup():
    return CoreIdentitySkill()
