# vision/describers/ollama_vlm.py
"""VLM description via Ollama — the only backend actually implemented today.

Images are passed as raw PNG bytes in messages[0]["images"], never base64.
Verified directly on this machine: a base64-encoded string returned garbage
('urn:1f6c8b0') and passing no image at all returned an empty string. Do not
"fix" this to base64 — it was tried, and it does not work.
"""
from core import llm_client
from core.config import SETTINGS


class OllamaVLM:
    def describe(self, png: bytes) -> str:
        messages = [{
            "role": "user",
            "content": "Describe what is currently on screen in one or two short sentences.",
            "images": [png],
        }]
        response = llm_client.chat(messages, model=SETTINGS["vlm"]["model"], host=SETTINGS["vlm"]["host"])
        return response.strip()
