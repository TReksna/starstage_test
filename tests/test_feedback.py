"""Feedback endpoint + backward-compatibility tests (no LLM calls)."""
from fastapi.testclient import TestClient

import app as app_module

client = TestClient(app_module.app)


def test_feedback_table_is_created():
    app_module.ensure_feedback_table()
    with app_module.get_db() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='answer_feedback'"
        ).fetchone()
    assert row is not None


def test_post_feedback_saves_row_with_metadata():
    payload = {
        "user_input": "what does my chart say about money?",
        "bot_answer": "A grounded answer about finances.",
        "user_feedback": "Needs more specific timing detail.",
        "conversation_id": "test-conv",
        "answer_style": "BLENDED",
        "answer_depth": "STANDARD",
        "classifier_intent": "THEMATIC",
        "classifier_life_area": "MONEY",
        "model_used": "gpt-4o",
    }
    r = client.post("/api/feedback", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["feedback_id"], int)

    with app_module.get_db() as conn:
        row = conn.execute(
            "SELECT user_input, bot_answer, user_feedback, answer_style, "
            "classifier_life_area, app_version FROM answer_feedback WHERE id=?",
            (body["feedback_id"],),
        ).fetchone()
    assert row["user_input"] == payload["user_input"]
    assert row["bot_answer"] == payload["bot_answer"]
    assert row["user_feedback"] == payload["user_feedback"]
    assert row["answer_style"] == "BLENDED"
    assert row["classifier_life_area"] == "MONEY"
    assert row["app_version"]  # stamped by the server


def test_empty_feedback_is_rejected():
    r = client.post("/api/feedback", json={
        "user_input": "q", "bot_answer": "a", "user_feedback": "   ",
    })
    assert r.status_code == 400


def test_oversized_feedback_is_rejected():
    r = client.post("/api/feedback", json={
        "user_input": "q", "bot_answer": "a", "user_feedback": "x" * 10_001,
    })
    assert r.status_code == 400


def test_chat_request_is_backward_compatible():
    # Old clients omit answer_style / answer_depth entirely.
    req = app_module.ChatRequest(user_id="u", message="hi")
    assert req.answer_style is None
    assert req.answer_depth is None
    # New clients may supply them.
    req2 = app_module.ChatRequest(user_id="u", message="hi",
                                  answer_style="PLAIN", answer_depth="DEEP")
    assert req2.answer_style == "PLAIN"
    assert req2.answer_depth == "DEEP"
