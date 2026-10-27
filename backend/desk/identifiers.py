"""Strict database IDs; free-text search can ignore an invalid numeric candidate."""
from rest_framework.exceptions import ValidationError


def positive_id(value, error=None):
    valid = isinstance(value, str) and 1 <= len(value) <= 19 and value.isascii() and value.isdigit()
    number = int(value) if valid else 0
    if 0 < number <= 9223372036854775807:
        return number
    if error:
        raise ValidationError(error)
    return None
