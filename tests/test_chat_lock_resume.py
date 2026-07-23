"""Chat lock-resume: turn must outlive SSE disconnect; one turn per channel."""

import inspect
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHAT_PY = (ROOT / "src/dashboard/routes/chat.py").read_text()
MSG_JS = (ROOT / "src/dashboard/static/js/messaging.js").read_text()


def test_chat_turn_runs_in_background_task_not_only_sse_generator():
    assert "_channel_run_tasks" in CHAT_PY
    assert "async def _chat_turn_runner" in CHAT_PY
    assert "asyncio.create_task(_chat_turn_runner" in CHAT_PY
    # Observer detach must not be the cancel path for the turn
    assert "SSE observer detached" in CHAT_PY
    assert "turn continues" in CHAT_PY


def test_concurrent_send_rejected_while_turn_running():
    assert "already_processing" in CHAT_PY
    assert "status_code=409" in CHAT_PY


def test_client_detaches_instead_of_connection_error_on_stream_death():
    assert "detaching to status poll" in MSG_JS or "Still working" in MSG_JS
    assert "bindChatVisibilityResume" in MSG_JS
    assert "visibilitychange" in MSG_JS
    # Connection error only after status says not processing
    assert "data.processing" in MSG_JS


def test_status_poll_reloads_history_when_idle():
    assert "pollChatStatus" in MSG_JS
    assert "loadChat()" in MSG_JS
