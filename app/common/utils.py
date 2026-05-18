from typing import Optional


def is_null_or_empty(s: Optional[str]) -> bool:
    if not s:
        return True
    return s.strip() == ""
