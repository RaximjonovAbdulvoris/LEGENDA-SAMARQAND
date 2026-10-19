"""Bounded local neural OCR, isolated from bot credentials."""
import asyncio
import base64
import json
import logging
import os
from pathlib import Path
import sys
import time
from weakref import WeakKeyDictionary
from bot.fast_ocr import environment
from bot.document_ocr import FIELDS, SOURCES, guard_identifiers, normalize

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
_GATES = WeakKeyDictionary()


def concurrency():
    # Two single-threaded workers use at most two CPU cores per bot. Keep a
    # one-worker setting available for smaller servers, never allow more than 2.
    default = 2 if (os.cpu_count() or 1) >= 4 else 1
    try:
        return max(1, min(2, int(os.environ.get('DOCUMENT_OCR_WORKERS', default))))
    except ValueError:
        return default


async def worker(payload=None):
    gate = _GATES.setdefault(asyncio.get_running_loop(), asyncio.Semaphore(concurrency()))
    await gate.acquire()
    try:
        return await _worker(payload)
    finally:
        gate.release()


async def _worker(payload=None):
    arguments = [sys.executable, '-m', 'bot.neural_ocr_worker']
    if payload is None:
        arguments.append('--check')
    process = await asyncio.create_subprocess_exec(*arguments, cwd=ROOT, env=dict(environment(), OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1"),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    output = []
    async def communicate():
        process.stdin.write(json.dumps(payload or []).encode())
        await process.stdin.drain()
        process.stdin.close()
        while line := await process.stdout.readline():
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    output.append(row)
            except (ValueError, TypeError):
                logger.warning('Local OCR worker emitted invalid protocol line')
        await process.wait()
    timed_out = False
    try:
        await asyncio.wait_for(communicate(), timeout=40)
    except asyncio.TimeoutError:
        timed_out = True
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode and not timed_out and not output:
        raise RuntimeError('Local neural OCR worker failed')
    return output


async def engine_info():
    result = await worker()
    if result != [{'ready': True}]:
        raise RuntimeError('Local neural OCR not ready')


async def extract_documents(bot, data):
    start = time.monotonic()
    # Drivers can put a document in the wrong upload step. Inspect every
    # document slot, then attribute fields to the detected document type.
    # Read the two primary cards first. The worker streams completed results;
    # if a later fallback image consumes the time budget, their fields survive.
    order = ('license_front', 'tech_back', 'tech_front', 'passport_front', 'license_back')
    selected = [source for source in order if data.get(source)]
    async def download(source):
        file = await bot.get_file(data[source])
        if file.file_size and file.file_size > 5_000_000:
            raise ValueError('Image too large')
        image = await file.download_as_bytearray()
        if len(image) > 5_000_000:
            raise ValueError('Image too large')
        return {'source': source, 'image': base64.b64encode(image).decode('ascii')}
    downloaded = await asyncio.gather(*(asyncio.wait_for(download(s), timeout=6) for s in selected), return_exceptions=True)
    failed, payload = [], []
    for source, item in zip(selected, downloaded):
        if isinstance(item, BaseException):
            failed.append(source)
            logger.warning('Neural OCR download failed: source=%s error=%s', source, type(item).__name__)
        else:
            payload.append(item)
    rows = await worker(payload) if payload else []
    candidates = {key: set() for key in (*FIELDS, 'full_name', 'surname', 'given_name')}
    completed = set()
    latest = {}
    for row in rows:
        if row.get('source') in selected and 'error' not in row:
            latest[row['source']] = row
    for row in latest.values():
        source = row.get('source')
        if source not in selected or 'error' in row:
            continue
        if row.get('final', True):
            completed.add(source)
        kind = row.get('kind', source)
        if kind not in SOURCES:
            continue
        values = normalize(row.get('values', {}))
        from bot.neural_ocr_worker import clean_name
        for key in ('full_name', 'surname', 'given_name'):
            values[key] = clean_name(row.get('values', {}).get(key) or '') if kind == 'license_front' else None
        for key in (*SOURCES[kind], 'full_name', 'surname', 'given_name'):
            if values[key]:
                candidates[key].add(values[key])
    failed += [item['source'] for item in payload if item['source'] not in completed]
    values = {key: next(iter(items)) if len(items) == 1 else None for key, items in candidates.items()}
    values, rejected = guard_identifiers(values, data.get('car_plate'))
    source_errors = list(failed)
    # A failed slot may contain either document after a mix-up.
    failed = failed if not values.get('full_name') or not values.get('vehicle_registration') else []
    logger.info('Neural OCR finished: seconds=%.2f fields=%s failed_sources=%s',
        time.monotonic() - start, sum(bool(v) for v in values.values()), len(failed))
    return {'retryable': bool(failed), 'backend': 'rapid', 'status': 'complete' if all(values.values()) and not failed else 'partial',
        'values': values, 'failed_sources': failed, 'source_errors': source_errors, 'rejected_fields': rejected,
        'conflicting_fields': [key for key, items in candidates.items() if len(items) > 1]}
