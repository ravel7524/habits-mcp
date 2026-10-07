"""Portable academic proposals. Importing this module needs only Python's standard library."""

from .validator import MAX_BYTES, PlanValidationError, validate_plan
from .tools import get_plan_format, propose_academic_plan, get_semester_board_format, propose_semester_board
from .board_validator import validate_semester_board, validate_board_context, validate_capabilities

__all__ = ["MAX_BYTES", "PlanValidationError", "validate_plan", "get_plan_format", "propose_academic_plan",
           "get_semester_board_format", "propose_semester_board", "validate_semester_board", "validate_board_context", "validate_capabilities"]
