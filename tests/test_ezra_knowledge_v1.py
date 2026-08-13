"""Ezra Knowledge v1 — isolated Functional Health sessions (no live Ollama)."""

from src.dashboard.routes import knowledge as kb


def test_pinned_tag_is_neo_dolphin():
    assert kb._PINNED_MODEL_TAG.startswith("hf.co/mishmashly/Neo-Dolphin-Mistral-7B-GGUF")


def test_default_direction_stays_on_health():
    text = kb._DEFAULT_DIRECTION.lower()
    assert "functional health" in text
    assert "day" in text and "deep" in text
    assert "calendar" in text or "logistics" in text
    assert kb._DEFAULT_DIRECTION == kb._DEFAULT_DIRECTION_FH
    assert "inventions" in kb._THREAD_KINDS
    assert "functional-health" in kb._THREAD_KINDS


def test_compose_prompt_is_ezra_not_a_generic_model():
    prompt = kb._compose_system_prompt(kb._DEFAULT_DIRECTION_FH, "## Active Memory\n- labs last week")
    low = prompt.lower()
    assert "you are ezra" in low
    assert "chatgpt" in low  # forbidden identity, named so he can refuse it
    assert "functional health" in low
    assert "labs last week" in low
    assert kb._knowledge_channel("functional-health") == "knowledge-functional-health"


def test_extractive_summary_skips_system_and_think():
    hist = [
        {"role": "system", "content": "seed"},
        {"role": "user", "content": "What helps recovery sleep?"},
        {"role": "assistant", "content": "<think>private</think>Magnesium glycinate is a common start."},
    ]
    summary = kb._extractive_summary(hist)
    assert "recovery sleep" in summary
    assert "Magnesium" in summary
    assert "private" not in summary
    assert "seed" not in summary


def test_router_has_expected_paths():
    paths = {getattr(r, "path", None) for r in kb.router.routes}
    assert "/knowledge" in paths
    assert "/api/knowledge/threads" in paths
    assert "/api/knowledge/sessions" in paths
    assert "/api/knowledge/sessions/{session_id}/chat" in paths


def test_think_only_output_is_usable_text():
    answer, thinking = kb._split_model_output("<think>magnesium first</think>")
    assert answer == ""
    assert "magnesium first" in thinking
    # Product rule: think-only is not an empty reply.
    shown = answer or thinking
    assert shown == "magnesium first"


def test_knowledge_keep_alive_unloads(monkeypatch):
    captured = {}

    class _FakeChat:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("langchain_ollama.ChatOllama", _FakeChat)
    monkeypatch.setattr(
        "src.models.provider._ollama_base_url",
        lambda: "http://ollama.test:11434",
    )
    client = kb._kb_ollama_client(kb._PINNED_MODEL_TAG, 0.4)
    assert isinstance(client, _FakeChat)
    assert captured.get("keep_alive") == "0"
    assert captured.get("model") == kb._PINNED_MODEL_TAG


def test_app_source_registers_knowledge_router():
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "dashboard" / "app.py"
    text = src.read_text()
    assert "knowledge," in text
    assert "from src.dashboard.routes import" in text
