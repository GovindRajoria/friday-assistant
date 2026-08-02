# tests/test_ollama_vlm.py
"""OllamaVLM passes raw PNG bytes to llm_client.chat, never base64.

Verified on this machine that a base64 string returns garbage and no image
at all returns an empty description — this test guards against silently
"fixing" that back to base64. No PIL or mss needed: the input is already
PNG-shaped bytes, not a captured frame.
"""
from vision.describers.ollama_vlm import OllamaVLM


def test_describe_passes_raw_png_bytes_not_base64(monkeypatch):
    captured = {}

    def fake_chat(messages, model=None, fmt=None, temperature=None, host=None):
        captured["messages"] = messages
        captured["model"] = model
        captured["host"] = host
        return "a description of the screen"

    monkeypatch.setattr("core.llm_client.chat", fake_chat)

    png_bytes = b"\x89PNG\r\n\x1a\n not a real image but definitely raw bytes"
    result = OllamaVLM().describe(png_bytes)

    assert result == "a description of the screen"
    images = captured["messages"][0]["images"]
    assert images == [png_bytes]
    assert isinstance(images[0], bytes)
