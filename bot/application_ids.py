"""Durable, process-safe application numbers shared by all regions and forms."""
import os
from pathlib import Path
import sqlite3


def application_code(data):
    if data.get('_application_code'):
        return data['_application_code']
    directory = Path(os.environ.get('PERSIST_DIR') or Path(__file__).resolve().parent.parent)
    directory.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(directory / 'application_numbers.sqlite3', timeout=15) as db:
        db.execute('CREATE TABLE IF NOT EXISTS sequence (id INTEGER PRIMARY KEY AUTOINCREMENT)')
        number = db.execute('INSERT INTO sequence DEFAULT VALUES').lastrowid
    code = f'LG-{number:06d}'
    data['_application_code'] = code
    return code
