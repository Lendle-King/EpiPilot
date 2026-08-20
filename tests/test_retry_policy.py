from __future__ import annotations

from epipilot.supervisor.retry import (
    FailureAttempt,
    RetryAction,
    RetryProposal,
    decide_retry,
)


def test_first_attempt_can_be_retried() -> None:
    assert decide_retry((), RetryProposal(strategy_id="strategy-a")) is RetryAction.RETRY


def test_unchanged_failure_requires_new_strategy_or_information() -> None:
    history = (
        FailureAttempt(
            failure_signature="pytest:test_x:AssertionError",
            strategy_id="strategy-a",
            evidence_refs=("EV-1",),
        ),
    )

    action = decide_retry(history, RetryProposal(strategy_id="strategy-a"))

    assert action is RetryAction.REQUIRE_STRATEGY_CHANGE


def test_new_evidence_justifies_retry_without_strategy_change() -> None:
    history = (
        FailureAttempt(
            failure_signature="pytest:test_x:AssertionError",
            strategy_id="strategy-a",
            evidence_refs=("EV-1",),
        ),
    )

    action = decide_retry(
        history,
        RetryProposal(strategy_id="strategy-a", new_evidence_refs=("EV-2",)),
    )

    assert action is RetryAction.RETRY


def test_changed_strategy_justifies_retry() -> None:
    history = (
        FailureAttempt(
            failure_signature="pytest:test_x:AssertionError",
            strategy_id="strategy-a",
        ),
    )

    action = decide_retry(history, RetryProposal(strategy_id="strategy-b"))

    assert action is RetryAction.RETRY


def test_repeated_same_signature_reaches_escalation_cap() -> None:
    history = (
        FailureAttempt("same-failure", "strategy-a"),
        FailureAttempt("same-failure", "strategy-b"),
    )

    action = decide_retry(
        history,
        RetryProposal(strategy_id="strategy-c", new_evidence_refs=("EV-3",)),
        max_same_signature_attempts=2,
    )

    assert action is RetryAction.ESCALATE
