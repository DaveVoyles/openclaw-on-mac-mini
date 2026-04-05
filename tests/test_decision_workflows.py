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
