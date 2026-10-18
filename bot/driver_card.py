"""Operator-facing driver card, with copyable fields in a fixed order."""
from html import escape
import re

from bot.document_ocr import review_warnings


def details(result, phone='', plate=''):
    values = result.get('values') or {}
    phone_digits = re.sub(r'\D', '', str(phone or ''))
    missing = '⚠️ Aniqlanmadi — tekshiring'

    def value(field):
        item = values.get(field)
        if not item:
            return missing
        return f'<code>{escape(str(item))}</code>'

    def entered(item):
        return f'<code>{escape(str(item))}</code>' if item else missing

    lines = [
        f"Familiya: {value('surname')}",
        f"Ism: {value('given_name')}",
        f"Tug‘ilgan sana: {value('birth_date')}",
        f"Telefon raqami: {entered(phone_digits)}",
        f"PINFL: {value('pinfl')}",
        f"Tug‘ilgan joy: {value('birth_place')}",
        f"Prava raqami: {value('license_number')}",
        f"Amal qilish muddati: {value('license_expiry')}",
        f"Avtomobil raqami: {entered(plate)}",
        f"Texpasport seriya raqami: {value('vehicle_registration')}",
    ]
    if result.get('retry_scheduled'):
        lines.append('⏳ O‘qilmagan ma’lumotlar qayta tekshirilmoqda.')
    lines.extend('⚠️ ' + escape(warning) for warning in review_warnings(values))
    lines.append('OCR natijasini hujjat bilan solishtiring.')
    return '\n'.join(lines)
