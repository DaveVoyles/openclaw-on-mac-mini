"""Tests for weighted decision logic and role-aware summaries."""

from decision_workflows import (
    DecisionVote,
    compute_weighted_outcome,
    parse_role_weights,
    role_aware_summary,
)


def test_parse_role_weights_filters_invalid_pairs():
    weights = parse_role_weights("PM:2, Eng:1.5, Broken, QA:0, Nope:abc")
    assert weights == {"pm": 2.0, "eng": 1.5}


def test_compute_weighted_outcome_applies_role_weights_and_dedupes_users():
    votes = [
        DecisionVote(user_id=1, user_name="alice", option_index=0, roles=["PM"]),
        DecisionVote(user_id=2, user_name="bob", option_index=1, roles=["Eng"]),
        DecisionVote(user_id=3, user_name="charlie", option_index=1, roles=[]),
        DecisionVote(user_id=1, user_name="alice", option_index=1, roles=["PM"]),  # duplicate user ignored
    ]
    outcome = compute_weighted_outcome(
        question="Pick release scope?",
        options=["Small", "Large"],
        votes=votes,
        role_weights={"pm": 2.0, "eng": 1.5},
    )
    assert outcome["weighted_totals"] == [2.0, 2.5]
    assert outcome["raw_totals"] == [1, 2]
    assert outcome["winner_option"] == "Large"
    assert outcome["participant_count"] == 3


def test_role_aware_summary_templates():
    decision = {
        "id": 42,
        "question": "Ship this sprint?",
        "options": ["Yes", "No"],
        "weighted_totals": [3.0, 1.0],
        "raw_totals": [2, 1],
        "winner_option": "Yes",
        "winner_weighted_score": 3.0,
        "participants": [{"user_id": 1}, {"user_id": 2}, {"user_id": 3}],
    }
    pm = role_aware_summary(decision, audience="pm")
    eng = role_aware_summary(decision, audience="eng")
    qa = role_aware_summary(decision, audience="qa")
    assert "PM focus" in pm
    assert "Eng focus" in eng
    assert "QA focus" in qa


def test_compute_weighted_outcome_breaks_ties_with_raw_votes():
    votes = [
        DecisionVote(user_id=1, user_name="alice", option_index=0, roles=["PM"]),
        DecisionVote(user_id=2, user_name="bob", option_index=1, roles=[]),
        DecisionVote(user_id=3, user_name="charlie", option_index=1, roles=[]),
    ]
    outcome = compute_weighted_outcome(
        question="Pick deployment slot?",
        options=["Noon", "Evening"],
        votes=votes,
        role_weights={"pm": 2.0},
    )
    assert outcome["weighted_totals"] == [2.0, 2.0]
    assert outcome["raw_totals"] == [1, 2]
    assert outcome["winner_option"] == "Evening"


def test_compute_weighted_outcome_ignores_invalid_options_and_uses_max_role_weight():
    votes = [
        DecisionVote(user_id=1, user_name="alice", option_index=0, roles=["Eng", "PM"]),
        DecisionVote(user_id=2, user_name="bob", option_index=9, roles=["PM"]),  # invalid option index
    ]
    outcome = compute_weighted_outcome(
        question="Ship now?",
        options=["Yes", "No"],
        votes=votes,
        role_weights={"pm": 2.0, "eng": 1.5},
    )
    assert outcome["participant_count"] == 1
    assert outcome["participants"][0]["applied_weight"] == 2.0


# --- Merged from test_decision_store.py ---
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
            {
                "user_id": 1,
                "user_name": "alice",
                "option_index": 0,
                "option": "Now",
                "roles": ["PM"],
                "applied_weight": 2.0,
            },
            {
                "user_id": 2,
                "user_name": "bob",
                "option_index": 1,
                "option": "Tomorrow",
                "roles": ["QA"],
                "applied_weight": 1.0,
            },
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
