# skills/utility/translate.py
"""Translation through the local model. No API, no key, nothing leaves the machine.

Cheap to build because `core/llm_client.py` already exists, and consistent with the
project's constraint that no text is sent to a third party.

One thing worth being explicit about, in the skill and in the README: an 8B general
model is a mediocre translator. It is fine for a menu, a message or a comment in
someone else's code, and it should not be trusted for anything that matters —
contracts, medical text, or a language it has barely seen. The skill says which
model produced the translation so the answer carries its own caveat.

Temperature is pinned near zero. Translation is the one task in this project where
creativity is purely a source of error.
"""
from core import llm_client
from core.config import SETTINGS

MAX_INPUT_CHARS = 3000


class TranslateSkill:
    def __init__(self):
        self.manifest = {
            "name": "translate",
            "description": (
                "Translates text into another language using the local model, without "
                "sending anything to an external service. Parameters: 'text' to translate, "
                "'to' for the target language, and optionally 'from' if the source is "
                "ambiguous. Use this when asked to translate something or to say something "
                "in another language."
            ),
            "parameters": ["text", "to", "from"],
        }

    def execute(self, params=None):
        params = params or {}
        text = str(params.get("text") or "").strip()
        target = str(params.get("to") or "").strip()
        source = str(params.get("from") or "").strip()

        if not text:
            return {"status": "error", "message": "What would you like me to translate?"}
        if not target:
            return {"status": "error", "message": "Which language should I translate that into?"}
        if len(text) > MAX_INPUT_CHARS:
            return {
                "status": "error",
                "message": (f"That is {len(text)} characters and I translate up to "
                            f"{MAX_INPUT_CHARS} at a time. Give me a section of it."),
            }

        origin = f" from {source}" if source else ""
        instruction = (
            f"Translate the following text{origin} into {target}. "
            "Reply with the translation only — no preamble, no explanation, no quotes, "
            "and no comment on the translation. Preserve names, numbers and formatting. "
            "If a word has no good equivalent, keep the original word rather than "
            "inventing one."
        )

        try:
            reply = llm_client.chat(
                [{"role": "system", "content": instruction},
                 {"role": "user", "content": text}],
                temperature=0.0,
            )
        except Exception as error:                                    # noqa: BLE001
            return {"status": "error", "message": f"The local model could not translate that: {error}"}

        # core/llm_client.py:chat already unwraps to response["message"]["content"],
        # so this is a string.
        translated = str(reply or "").strip()
        if not translated:
            return {"status": "error", "message": "The model returned nothing for that translation."}

        model = SETTINGS["llm"]["model"]
        return {
            "status": "success",
            # The model is named in the answer on purpose: a general-purpose 8B
            # model is a passable translator and a poor one for anything that
            # matters, and the reply should carry that with it.
            "message": f"{translated}\n\n(Translated into {target} by the local {model} model.)",
            "data": {"target": target, "characters": len(translated), "model": model},
        }


def setup():
    return TranslateSkill()
