"""Deliver first, then enrich the existing control message with local OCR."""
import asyncio
import logging
import time
from weakref import WeakKeyDictionary

from bot.document_ocr import SOURCES, extract_documents, enabled, review_warnings
from bot.driver_card import details as driver_details

logger = logging.getLogger(__name__)
_LOCKS = WeakKeyDictionary()
_JOBS = WeakKeyDictionary()
_QUEUES = WeakKeyDictionary()
MAX_ATTEMPTS = 3
RETRY_DELAYS = (15, 60, 180)
PENDING_TEXT = '⏳ Hujjatlar tekshiruv navbatida. Operator hujjatlarni hoziroq ko‘rib chiqishi mumkin.'


def control_lock(chat_id, message_id):
    locks = _LOCKS.setdefault(asyncio.get_running_loop(), {})
    return locks.setdefault((int(chat_id), int(message_id)), asyncio.Lock())


def records(application, code, applicant_id):
    return [(key, record) for key, record in application.bot_data.get('applications', {}).items()
            if record.get('code') == code and record.get('applicant_id') == applicant_id
            and record.get('kind') == 'driver']


def current_record(application, code, applicant_id):
    matches = records(application, code, applicant_id)
    # A source may remain during failed cleanup. Follow the confirmed destination.
    destinations = [(key, record) for key, record in matches if not record.get('_transfer')]
    return destinations[-1] if destinations else None


def schedule_ocr(application, code, applicant_id):
    if not enabled() or application is None or not callable(getattr(application, 'create_task', None)):
        return
    jobs = _JOBS.setdefault(asyncio.get_running_loop(), {})
    key = (id(application), code, applicant_id)
    if key in jobs and not jobs[key].done():
        return
    task = application.create_task(enrich(application, code, applicant_id), name='local-ocr-enrichment')
    jobs[key] = task
    def finished(done):
        if jobs.get(key) is done:
            jobs.pop(key, None)
    task.add_done_callback(finished)


def cancel_if_removed(application, code, applicant_id):
    if application is None or records(application, code, applicant_id):
        return
    jobs = _JOBS.get(asyncio.get_running_loop(), {})
    task = jobs.get((id(application), code, applicant_id))
    if task is not None and task is not asyncio.current_task():
        task.cancel()


async def save(application):
    if application.persistence is not None:
        await application.update_persistence()
        await application.persistence.flush()


def merge_results(previous, current):
    """Keep useful earlier fields; a conflicting value is never silently chosen."""
    result = dict(current)
    previous = previous or {}
    values = dict(previous.get('values', {}))
    conflicts = set(previous.get('conflicting_fields', [])) | set(current.get('conflicting_fields', []))
    for key, value in current.get('values', {}).items():
        if not value:
            continue
        if values.get(key) and values[key] != value:
            conflicts.add(key)
        else:
            values[key] = value
    for key in conflicts:
        values[key] = None
    result['values'] = values
    result['conflicting_fields'] = sorted(conflicts)
    if conflicts and result.get('status') == 'complete':
        result['status'] = 'partial'
    return result


async def enrich(application, code, applicant_id):
    # A FIFO lock keeps concurrent requests out of the CPU worker without the old
    # two-second rejection. Only file IDs wait in tasks, not downloaded images.
    queue = _QUEUES.setdefault(asyncio.get_running_loop(), asyncio.Lock())
    async with queue:
        await _enrich(application, code, applicant_id)


async def _enrich(application, code, applicant_id):
    from bot.handlers.operator import build_operator_keyboard, is_review_record
    from bot.presentation import with_keyboard_fallback
    found = current_record(application, code, applicant_id)
    if found is None:
        return
    record = found[1]
    if record.get('deleted_source_ids') or record.get('archive_state', {}).get('kb'):
        return
    data = dict(record.get('ocr_input', {}))
    if not data and not record.get('ocr_delivery_pending'):
        return
    try:
        if data and int(record.get('ocr_attempts', 0)) >= MAX_ATTEMPTS and not record.get('ocr_delivery_pending'):
            record['ocr_result'] = dict(record.get('ocr_result') or {'status': 'unavailable', 'values': {}}, retry_scheduled=False)
            record['ocr_status'] = 'needs_review'
            record['ocr_delivery_pending'] = True
            record.pop('ocr_input', None)
            await save(application)
        if record.get('ocr_delivery_pending'):
            result = record['ocr_result']
        else:
            attempt = int(record.get('ocr_attempts', 0)) + 1
            record['ocr_attempts'] = attempt
            await save(application)
            try:
                current = await extract_documents(application.bot, data)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning('Background OCR extraction unavailable (%s)', type(error).__name__)
                current = {'status': 'unavailable', 'values': {}, 'failed_sources': [], 'retryable': True}
            found = current_record(application, code, applicant_id)
            if found is None:
                return
            record = found[1]
            record['ocr_attempts'] = attempt
            result = merge_results(record.get('ocr_result'), current)
            transient = current.get('retryable') or current.get('failed_sources') or current.get('status') == 'unavailable'
            retry = bool(transient and attempt < MAX_ATTEMPTS)
            result['retry_scheduled'] = retry
            record['ocr_result'] = result
            record['ocr_status'] = 'retry' if retry else ('complete' if result.get('status') == 'complete' and not review_warnings(result.get('values', {})) else 'needs_review')
            record['ocr_retry_at'] = time.time() + RETRY_DELAYS[min(attempt - 1, 2)] if retry else 0
            record['ocr_delivery_pending'] = True
            if not retry:
                record.pop('ocr_input', None)
            await save(application)
        while (found := current_record(application, code, applicant_id)) is not None:
            key, record = found
            async with control_lock(record['group_chat_id'], record['kb_msg_id']):
                latest = current_record(application, code, applicant_id)
                if latest is None:
                    return
                if latest[0] != key:
                    continue
                if record.get('deleted_source_ids') or record.get('archive_state', {}).get('kb'):
                    return
                card_data = record.get('ocr_card_data') or {}
                text = (record['ocr_prefix'] + driver_details(result, card_data.get('phone'),
                        card_data.get('plate')) + record['ocr_suffix'])
                progress = application.bot_data.get('progress_state', {}).get(key, False)
                try:
                    await with_keyboard_fallback(application.bot.edit_message_text,
                        chat_id=record['group_chat_id'], message_id=record['kb_msg_id'], text=text,
                        parse_mode='HTML', reply_markup=build_operator_keyboard(
                            applicant_id, in_progress=progress, review=is_review_record(record)))
                except Exception as error:
                    if 'message is not modified' not in str(error).lower():
                        raise
                record['ocr_delivery_pending'] = False
                record.pop('ocr_delivery_attempts', None)
                await save(application)
                return
    except asyncio.CancelledError:
        raise
    except Exception as error:
        found = current_record(application, code, applicant_id)
        if found:
            record = found[1]
            attempts = int(record.get('ocr_delivery_attempts', 0)) + 1
            record['ocr_delivery_attempts'] = attempts
            record['ocr_retry_at'] = time.time() + RETRY_DELAYS[min(attempts - 1, 2)]
            if attempts >= MAX_ATTEMPTS:
                record['ocr_delivery_pending'] = False
                record['ocr_status'] = 'needs_review'
                record.pop('ocr_input', None)
            await save(application)
        logger.warning('Background OCR card update unavailable (%s)', type(error).__name__)


async def resume_pending(context):
    application = context.application
    seen = set()
    for record in list(application.bot_data.get('applications', {}).values()):
        identity = (record.get('code'), record.get('applicant_id'))
        if identity in seen or not all(identity):
            continue
        seen.add(identity)
        active = current_record(application, *identity)
        if active is None:
            continue
        record = active[1]
        if record.get('deleted_source_ids') or record.get('archive_state', {}).get('kb'):
            continue
        due = record.get('ocr_retry_at', 0) <= time.time()
        extraction = record.get('ocr_status') in ('pending', 'retry') and record.get('ocr_input')
        if due and (extraction or record.get('ocr_delivery_pending')):
            schedule_ocr(application, *identity)
