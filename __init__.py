"""Portable academic proposals. Importing this module needs only Python's standard library."""

from .validator import MAX_BYTES, PlanValidationError, validate_plan
from .tools import get_plan_format, propose_academic_plan

__all__ = ["MAX_BYTES", "PlanValidationError", "validate_plan", "get_plan_format", "propose_academic_plan"]
