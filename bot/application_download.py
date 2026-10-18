"""Package one operator application from its Telegram file IDs in memory."""
import asyncio
from io import BytesIO
import re
from zipfile import ZipFile, ZIP_STORED

from telegram import InputFile

MAX_ARCHIVE_BYTES = 45_000_000
MAX_PHOTO_BYTES = 8_000_000


def folder_name(record):
    values = (record.get('ocr_result') or {}).get('values') or {}
    identity = ' '.join(str(values.get(field) or '') for field in ('surname', 'given_name')).strip()
    if not identity:
        identity = str(record.get('applicant_name') or '').strip()
    # A ZIP entry must never contain path separators, dot segments, or a user
    # supplied absolute path. Keep readable Uzbek/Cyrillic letters and spaces.
    identity = re.sub(r'[^\w -]', '', identity, flags=re.UNICODE).strip(' ._-')[:70]
    if not identity or identity.lower() in ('arizachi', 'foydalanuvchi'):
        identity = re.sub(r'[^A-Za-z0-9_-]', '', record.get('code') or 'Ariza')[:40]
    return identity or 'Ariza'


async def send_archive(bot, record, chat_id, reply_to_message_id):
    albums = record.get('media_albums')
    if not isinstance(albums, list) or not albums:
        raise ValueError('Bu arizada yuklab olinadigan rasm IDlari saqlanmagan.')
    items = []
    for album in albums:
        if not isinstance(album, dict) or not isinstance(album.get('photos'), list):
            raise ValueError('Ariza rasmlari ro‘yxati to‘liq emas.')
        name = re.sub(r'[^a-z0-9_-]', '', str(album.get('name', '')).lower())[:24] or 'hujjat'
        for number, photo in enumerate(album['photos'], 1):
            if not isinstance(photo, dict) or not photo.get('file_id'):
                raise ValueError('Ariza rasmlari ro‘yxati to‘liq emas.')
            items.append((f'{name}_{number:02d}.jpg', photo['file_id']))
    if not items or len(items) > 30 or len({name for name, _ in items}) != len(items):
        raise ValueError('Ariza rasmlari ro‘yxati noto‘g‘ri.')

    directory = folder_name(record)
    archive = BytesIO()
    total = 0
    with ZipFile(archive, 'w', compression=ZIP_STORED) as zipped:
        for name, file_id in items:
            telegram_file = await asyncio.wait_for(bot.get_file(file_id), timeout=10)
            if telegram_file.file_size and telegram_file.file_size > MAX_PHOTO_BYTES:
                raise ValueError('Arizadagi rasm hajmi yuklab olish chegarasidan oshgan.')
            contents = await asyncio.wait_for(telegram_file.download_as_bytearray(), timeout=20)
            total += len(contents)
            if len(contents) > MAX_PHOTO_BYTES or total > MAX_ARCHIVE_BYTES:
                raise ValueError('Arizadagi rasmlar hajmi yuklab olish chegarasidan oshgan.')
            zipped.writestr(f'{directory}/{name}', contents)
    if archive.tell() > MAX_ARCHIVE_BYTES:
        raise ValueError('ZIP hajmi Telegram chegarasidan oshgan.')
    archive.seek(0)
    code = re.sub(r'[^A-Za-z0-9_-]', '', record.get('code') or 'ariza')[:40]
    return await bot.send_document(chat_id=chat_id, document=InputFile(archive, filename=f'{code}_hujjatlar.zip'),
                            reply_to_message_id=reply_to_message_id,
                            caption=f'{code} · Hujjatlar ZIP arxivi')
