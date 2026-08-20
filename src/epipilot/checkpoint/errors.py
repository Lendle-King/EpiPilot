"""Checkpoint validation and persistence errors."""

from __future__ import annotations


class CheckpointError(RuntimeError):
    """Base error for checkpoint validation or storage failures."""


class UnsupportedCheckpointSchema(CheckpointError):
    """Raised when a checkpoint schema version is not understood."""


class CheckpointChecksumMismatch(CheckpointError):
    """Raised when checkpoint bytes or protected metadata fail integrity validation."""


class CheckpointStateInvalid(CheckpointError):
    """Raised when decoded checkpoint state contradicts checkpoint metadata."""


class CheckpointStoreError(CheckpointError):
    """Raised when durable checkpoint storage cannot be decoded safely."""
