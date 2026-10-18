"""Validate ordinary Uzbekistan private and company vehicle plates."""
import re


def normalize_plate(value):
    text = re.sub(r'[\s-]', '', str(value or '')).upper()
    # Accept the two supported layouts without guessing Cyrillic/Latin letters.
    return text if re.fullmatch(r'(?:[0-9]{2}[A-Z][0-9]{3}[A-Z]{2}|[0-9]{5}[A-Z]{3})', text) else None
