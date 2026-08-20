"""Fail-closed errors raised by canonical project-state replay."""


class StateReplayError(ValueError):
    """Base error for invalid project event streams."""


class AggregateMismatch(StateReplayError):
    """Raised when an event belongs to a different project aggregate."""


class DuplicateAppliedEvent(StateReplayError):
    """Raised when the same event id appears more than once in a replay."""


class DuplicateEntity(StateReplayError):
    """Raised when an event tries to recreate an existing canonical entity."""


class MissingEntity(StateReplayError):
    """Raised when an event references canonical state that does not yet exist."""


class InvalidEventOrder(StateReplayError):
    """Raised when event ordering violates a domain state transition or dependency."""
