import math
from typing import Optional


def is_null_or_empty(s: Optional[str]) -> bool:
    if not s:
        return True
    return s.strip() == ""


def integer_ceiling_product(a: float, b: float, tolerance: float = 1e-9) -> int:
    _result = a * b
    if math.isclose(_result, round(_result), abs_tol=tolerance):
        return round(_result)
    return math.ceil(_result)
