"""Local CPU OCR using Tesseract stdin/stdout, with labelled-field parsing."""
import asyncio
import csv
from dataclasses import dataclass
from io import BytesIO, StringIO
import logging
import os
import re
import shutil
import time
from weakref import WeakKeyDictionary

from PIL import Image, ImageOps, ImageFilter

logger = logging.getLogger(__name__)
_GATES = WeakKeyDictionary()
DATE = r'([0-9]{2})[./-]([0-9]{2})[./-]([0-9]{4})'
SERIAL = r'(?<![A-ZА-Я0-9])([A-ZАВСЕНКМОРТХ]{2,3}\s*[0-9](?:\s*[0-9]){5,8})(?![A-ZА-Я0-9])'


@dataclass
class Word:
    text: str
    confidence: float
    left: int
    top: int
    width: int
    height: int


def environment():
    return {'PATH': os.defpath, 'LANG': 'C.UTF-8', 'OMP_THREAD_LIMIT': '1'}


async def engine_info():
    binary = shutil.which('tesseract')
    if not binary:
        raise RuntimeError('Tesseract is not installed')
    process = await asyncio.create_subprocess_exec(binary, '--list-langs',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=environment())
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=5)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    installed = set(output.decode('utf-8', errors='replace').splitlines())
    languages = [name for name in ('eng', 'rus', 'uzb') if name in installed]
    if process.returncode or 'eng' not in languages:
        raise RuntimeError('Tesseract English language data is required')
    return binary, '+'.join(languages)


def image_view(raw, rotation=0):
    with Image.open(BytesIO(raw)) as original:
        if original.width * original.height > 30_000_000:
            raise ValueError('Image dimensions too large')
        image = ImageOps.exif_transpose(original).convert('RGB')
    if rotation:
        image = image.rotate(rotation, expand=True)
    if max(image.size) < 1800:
        scale = min(2.5, 1800 / max(image.size))
        image = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    image.thumbnail((2600, 2600), Image.Resampling.LANCZOS)
    image = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=.3)
    image = image.filter(ImageFilter.UnsharpMask(radius=1, percent=100, threshold=3))
    stream = BytesIO()
    image.save(stream, 'PNG')
    return stream.getvalue(), image.width, image.height


async def recognize(binary, languages, raw, psm=11):
    process = await asyncio.create_subprocess_exec(binary, 'stdin', 'stdout',
        '-l', languages, '--oem', '1', '--psm', str(psm), 'tsv',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=environment())
    try:
        output, _ = await asyncio.wait_for(process.communicate(raw), timeout=8)
        if process.returncode:
            raise RuntimeError('Local OCR subprocess failed')
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    words = []
    for row in csv.DictReader(StringIO(output.decode('utf-8', errors='replace')), delimiter='\t'):
        if row.get('level') != '5' or not row.get('text', '').strip():
            continue
        words.append(Word(row['text'].strip(), float(row['conf']),
            *(int(row[key]) for key in ('left', 'top', 'width', 'height'))))
    return words


def lines(words):
    groups = []
    for word in sorted(words, key=lambda item: (item.top + item.height / 2, item.left)):
        center = word.top + word.height / 2
        group = next((group for group in reversed(groups) if abs(
            center - sum(item.top + item.height / 2 for item in group) / len(group))
            < max(word.height, max(item.height for item in group)) * .55), None)
        if group is None:
            groups.append([word])
        else:
            group.append(word)
    result = []
    for group in groups:
        group.sort(key=lambda item: item.left)
        # Low-confidence words remain explicit gaps, never guessed/corrected.
        result.append((' '.join(item.text if item.confidence >= 65 else '?' for item in group), group))
    return result


def parse_fields(words, source, width, height):
    from bot.document_ocr import normalize, guard_identifiers
    candidates = {field: set() for field in ('pinfl', 'birth_date', 'birth_place',
        'license_number', 'license_expiry', 'vehicle_registration')}
    def add(field, value):
        normalized = normalize({field: value})[field]
        if normalized:
            candidates[field].add(normalized)
    for text, group in lines(words):
        if source.startswith(('passport_', 'license_')):
            # A clearly printed complete 14-digit token is unambiguous in these documents.
            for match in re.finditer(r'(?<![0-9])([0-9]{14})(?![0-9])', text):
                add('pinfl', match[1])
        if source == 'license_front':
            birth = re.search(r'(?:^|\s)3[.)]?\s+(.+?)\s+' + DATE, text)
            if birth:
                if '?' not in birth[1] and not re.search(r'\b[48][a-d]?[.)]', birth[1]):
                    add('birth_place', birth[1].strip())
                add('birth_date', '.'.join(birth.groups()[1:]))
            expiry = re.search(r'[4AА]\s*[bвб][.)]?\s*' + DATE, text, re.I)
            if expiry:
                add('license_expiry', '.'.join(expiry.groups()))
            serial = re.search(r'(?:^|\s)5[.)]?\s+' + SERIAL, text)
            if serial:
                add('license_number', serial[1])
        if source.startswith('passport_'):
            birth = re.search(r'(?:DATE OF BIRTH|TUG.ILGAN SAN[AI]|ДАТА РОЖДЕНИЯ)\s*[:.]?\s*' + DATE, text, re.I)
            if birth:
                add('birth_date', '.'.join(birth.groups()))
        if source.startswith('tech_') and not re.search(
                r'(?:\b14[.)]|DVIGATEL|ENGINE|VIN|SHASSI|KUZOV)', text, re.I):
            # The certificate serial is at the upper right. Require an intact,
            # high-confidence token, not a partial VIN or an engine label.
            for word in group:
                if word.confidence < 65 or word.left < width * .45 or word.top > height * .75:
                    continue
                match = re.fullmatch(SERIAL, word.text.upper())
                if match:
                    add('vehicle_registration', match[1])
    values = {field: next(iter(items)) if len(items) == 1 else None for field, items in candidates.items()}
    values, _ = guard_identifiers(values)
    return values


async def extract_one(bot, source, file_id, binary, languages):
    from bot.document_ocr import SOURCES
    gate = _GATES.setdefault(asyncio.get_running_loop(), asyncio.Semaphore(2))
    async with gate:
        started = time.monotonic()
        stage = "telegram_metadata"
        try:
            file = await asyncio.wait_for(bot.get_file(file_id), timeout=10)
        except Exception as error:
            logger.warning("Fast OCR stage failed: source=%s stage=%s error=%s", source, stage, type(error).__name__)
            raise
        if file.file_size and file.file_size > 5_000_000:
            raise ValueError('Image too large')
        try:
            raw = await asyncio.wait_for(file.download_as_bytearray(), timeout=10)
        except Exception as error:
            logger.warning("Fast OCR stage failed: source=%s stage=telegram_download error=%s", source, type(error).__name__)
            raise
        if len(raw) > 5_000_000:
            raise ValueError('Image too large')
        candidates = {field: set() for field in SOURCES[source]}
        # Normal and sideways photographs, bounded to avoid minute-long inference.
        for rotation in (0, 90, 270):
            image, width, height = await asyncio.to_thread(image_view, raw, rotation)
            try:
                words = await recognize(binary, languages, image)
            except (asyncio.TimeoutError, RuntimeError) as error:
                logger.warning("Fast OCR stage failed: source=%s stage=tesseract rotation=%s error=%s",
                    source, rotation, type(error).__name__)
                continue
            result = parse_fields(words, source, width, height)
            for field in candidates:
                if result[field]:
                    candidates[field].add(result[field])
            if all(len(items) == 1 for items in candidates.values()):
                break
        logger.info("Fast OCR source finished: source=%s seconds=%.2f fields=%s",
            source, time.monotonic() - started, sum(len(items) == 1 for items in candidates.values()))
        return {field: next(iter(items)) if len(items) == 1 else None for field, items in candidates.items()}


async def extract_documents(bot, data):
    from bot.document_ocr import SOURCES, FIELDS, guard_identifiers
    binary, languages = await engine_info()
    selected = [(source, data[source]) for source in SOURCES if data.get(source)]
    tasks = [asyncio.create_task(extract_one(bot, source, file_id, binary, languages))
             for source, file_id in selected]
    if not tasks:
        return {'status': 'unavailable', 'values': {}, 'failed_sources': [], 'backend': 'fast'}
    try:
        _, pending = await asyncio.wait(tasks, timeout=60)
        for task in pending:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    candidates = {field: set() for field in FIELDS}
    failed = []
    for (source, _), result in zip(selected, results):
        if isinstance(result, BaseException):
            failed.append(source)
            logger.warning('Fast OCR source failed: source=%s, error=%s', source, type(result).__name__)
            continue
        for field, value in result.items():
            if value:
                candidates[field].add(value)
    values = {field: next(iter(items)) if len(items) == 1 else None for field, items in candidates.items()}
    values, rejected = guard_identifiers(values, data.get('car_plate'))
    return {'status': 'complete' if all(values.values()) and not failed else 'partial',
            'values': values, 'failed_sources': failed, 'backend': 'fast',
            'conflicting_fields': [field for field, items in candidates.items() if len(items) > 1],
            'rejected_fields': rejected}
