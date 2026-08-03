# tests/test_explain_architecture.py
"""FRIDAY's account of itself has to come from the file, not from the model.

Asked how it works, a language model produces a fluent description of some
assistant it saw in training — right in shape, wrong in every specific, with
nothing marking which is which. This skill exists so the answer is a document
that lives beside the code, and these tests exist so it stays one.
"""
from pathlib import Path

import pytest
from core.config import PROJECT_ROOT
from skills.utility.explain_architecture import parse_sections, resolve, setup

SAMPLE = """# Title

Preamble that belongs to no section at all.

## Overview

It runs locally.

## Reasoning loop

It is a state machine.
"""


def test_sections_are_keyed_on_the_first_word_of_the_heading():
    sections = parse_sections(SAMPLE)

    assert set(sections) == {"overview", "reasoning"}
    assert sections["reasoning"][0] == "Reasoning loop"
    assert sections["reasoning"][1] == "It is a state machine."
    # The preamble is not a section and must not be returned as one.
    assert "preamble" not in sections
    assert "Preamble" not in sections["overview"][1]


def test_the_words_people_actually_use_reach_the_right_section():
    sections = parse_sections(SAMPLE)

    # Nobody asks about the "reasoning" section by name. They ask what
    # LangGraph is for, and the model passes that word straight through.
    assert resolve("langgraph", sections) == "reasoning"
    assert resolve("the reasoning loop", sections) == "reasoning"
    assert resolve("state machine", sections) == "reasoning"


def test_an_unknown_topic_falls_back_to_the_overview():
    # A wrong guess from the model should answer the question roughly rather
    # than not at all — an error here reads to the operator as "it does not
    # know how it works".
    sections = parse_sections(SAMPLE)

    assert resolve("quantum tunnelling", sections) == "overview"
    assert resolve("", sections) == "overview"


def test_the_real_document_carries_every_advertised_topic():
    # The manifest description lists the topics for the model to choose from.
    # If a heading is renamed in docs/ARCHITECTURE.md and the description is
    # not, the model picks a topic that silently resolves to the overview and
    # answers the wrong question confidently.
    skill = setup()
    sections = parse_sections((PROJECT_ROOT.parent / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8"))
    advertised = {
        "overview", "reasoning", "skills", "voice",
        "vision", "safety", "proactive", "interface", "configuration",
    }

    assert advertised <= set(sections)
    for topic in advertised:
        assert topic in skill.manifest["description"]


def test_the_answer_is_the_section_verbatim():
    # Not a summary. A summary is generated text, and generated text about how
    # this works is exactly the invention the skill exists to prevent.
    skill = setup()
    result = skill.execute({"topic": "langgraph"})
    sections = parse_sections((PROJECT_ROOT.parent / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8"))

    assert result["status"] == "success"
    assert sections["reasoning"][1] in result["message"]


def test_the_turn_ends_when_it_returns():
    # Enforced by core/graph.py, not requested by the prompt. See
    # tests/test_terminal_skill.py for what happens without it.
    assert setup().manifest["terminal"] is True


def test_a_missing_document_says_so_rather_than_improvising(monkeypatch):
    skill = setup()
    monkeypatch.setattr(
        "skills.utility.explain_architecture.CANDIDATE_PATHS",
        (Path("no", "such", "architecture.md"),),
    )

    result = skill.execute({"topic": "overview"})

    assert result["status"] == "error"
    assert "not found" in result["message"]


@pytest.mark.parametrize("topic", ["voice", "safety", "proactive"])
def test_every_section_answers_with_its_own_heading(topic):
    result = setup().execute({"topic": topic})

    assert result["status"] == "success"
    assert result["message"].lower().startswith(topic)
