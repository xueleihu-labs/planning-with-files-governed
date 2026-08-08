"""Gate 2 extracted module: core/errors.py.

Generated from the Gate 1 planning.py baseline.
"""
from __future__ import annotations

class PlanningError(RuntimeError):
    """Expected, structured failure for PLAN runtime entries."""

    def __init__(self, code: str, message: str, *, result: str = "FAILED") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.result = result
