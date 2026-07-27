def is_null_or_empty(s: str | None) -> bool:
    if not s:
        return True
    return s.strip() == ""
