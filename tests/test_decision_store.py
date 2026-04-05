"""Tests for DecisionStore persistence and retrieval."""

from decision_workflows import DecisionStore


def test_decision_store_persists_and_lists_recent(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")
    outcome = {
        "question": "Choose deployment time?",
        "options": ["Now", "Tomorrow"],
        "weighted_totals": [2.5, 1.0],
        "raw_totals": [2, 1],
        "winner_option": "Now",
        "winner_weighted_score": 2.5,
        "participants": [
            {"user_id": 1, "user_name": "alice", "option_index": 0, "option": "Now", "roles": ["PM"], "applied_weight": 2.0},
            {"user_id": 2, "user_name": "bob", "option_index": 1, "option": "Tomorrow", "roles": ["QA"], "applied_weight": 1.0},
        ],
        "role_weights": {"pm": 2.0},
    }

    decision_id = store.log_decision(
        outcome,
        channel_id=111,
        channel_name="planning",
        thread_id=222,
        thread_name="release-thread",
        poll_message_id=333,
        created_by=999,
        created_at=1_700_000_000.0,
    )

    row = store.get_decision(decision_id)
    assert row is not None
    assert row["question"] == "Choose deployment time?"
    assert row["winner_option"] == "Now"
    assert row["participants"][0]["user_name"] == "alice"
    assert row["thread_name"] == "release-thread"

    recent = store.list_recent(limit=5, channel_id=111)
    assert len(recent) == 1
    assert recent[0]["id"] == decision_id
