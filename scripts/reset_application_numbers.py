"""Reset only the application counter. Stop the bot before running this script."""
import os
from pathlib import Path
import sqlite3
from datetime import datetime, timezone


def reset(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'application_numbers.sqlite3'
    backup = None
    with sqlite3.connect(path, timeout=15) as db:
        if path.stat().st_size:
            backup = directory / ('application_numbers.before-reset-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.sqlite3')
            with sqlite3.connect(backup) as copy:
                db.backup(copy)
            backup.chmod(0o600)
        db.execute('CREATE TABLE IF NOT EXISTS sequence (id INTEGER PRIMARY KEY AUTOINCREMENT)')
        db.execute('DELETE FROM sequence')
        db.execute("DELETE FROM sqlite_sequence WHERE name = 'sequence'")
    path.chmod(0o600)
    return backup


if __name__ == '__main__':
    from dotenv import load_dotenv
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / '.env')
    reset(os.environ.get('PERSIST_DIR') or root)
    print('Hisob 0 qilindi. Keyingi yangi haydovchi arizasi: LG-000001. Eski arizalar saqlandi.')
