"""Operator callbacks and the operator/applicant comment relay.

An application is identified by the pair ``(source group, keyboard message)``.
The applicant's Telegram id is deliberately only an attribute of an
application: one applicant can have more than one regional application at the
same time.
"""
import logging
from html import escape as h

from telegram import InputMediaPhoto, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Forbidden, RetryAfter, TimedOut, NetworkError
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from bot.regions import archive_group, region_name
from bot.presentation import button as styled_button, with_keyboard_fallback
from bot.brand_cleanup import is_plain_application_message, remove_buttons_command

logger = logging.getLogger(__name__)


def _refresh_reason(error):
    # Only known categories are logged: exception text can contain user data.
    if isinstance(error, Forbidden):
        return 'forbidden'
    if isinstance(error, RetryAfter):
        return 'rate_limit'
    if isinstance(error, BadRequest):
        text = str(error).lower()
        if 'not found' in text:
            return 'message_missing'
        if "can't be edited" in text or 'not enough rights' in text:
            return 'not_editable_or_rights'
        return 'bad_request'
    if isinstance(error, (TimedOut, NetworkError)):
        return 'network'
    return 'unexpected'

# Process-local only: persistence must never turn an interrupted archive into
# a permanently locked application after a restart.
_ARCHIVES_IN_FLIGHT: set[tuple[int, int]] = set()

READY_TEXT = (
    "✅ <b>Arizangiz bo‘yicha ish yakunlandi</b>\n\n"
    "Murojaatingiz operator tomonidan ko‘rib chiqildi va yakunlandi.\n"
    "Savollar uchun: +998 78 150-50-50."
)


INCOMPLETE_TEXT = (
    "📄 <b>Hujjatlarni qayta yuborishingizni so‘raymiz</b>\n\n"
    "Arizangizni ko‘rib chiqdik. Yuborilgan hujjat rasmlarida kamchiliklar "
    "aniqlandi yoki ayrim ma’lumotlar aniq ko‘rinmayapti.\n\n"
    "Iltimos, bot menyusidagi «📝 Ulanish uchun Ariza» bo‘limi orqali arizani "
    "qaytadan to‘ldirib, barcha so‘ralgan hujjat va avtomobil rasmlarini "
    "to‘liq, tiniq va chetlari kesilmagan holda yuboring.\n\n"
    "Savollaringiz bo‘lsa, biz bilan bog‘laning: +998 78 150-50-50."
)
OTHER_PARK_TEXT = (
    "🚕 <b>Arizangiz bo‘yicha ma’lumot</b>\n\n"
    "Arizangizni ko‘rib chiqdik. Siz boshqa taksopark orqali ro‘yxatdan "
    "o‘tganingiz sababli hozircha sizni WB LEGENDA taksoparki bazasiga "
    "qo‘sha olmadik.\n\n"
    "Iltimos, ro‘yxatdan o‘tgan taksoparkingiz bilan bog‘lanib, "
    "WB LEGENDA taksoparkiga o‘tish masalasini hal qiling. "
    "Shundan so‘ng biz bilan bog‘lanishingiz mumkin.\n\n"
    "Yordam uchun: +998 78 150-50-50."
)


def build_operator_keyboard(
    applicant_user_id: int, in_progress: bool = False, review: bool = False
) -> InlineKeyboardMarkup:
    def button(label, action):
        style = {"ready": "success", "delete": "danger"}.get(action, "primary")
        icon = ('PROGRESS_ACTIVE' if in_progress else 'PROGRESS_IDLE') if action == 'progress' else action.upper()
        return styled_button(label, action=f"op:{action}:{applicant_user_id}",
                             style=style, icon=icon)
    rows = [[button("✅ Tayyor", "ready"),
             button("Jarayonda (faol) 🟡" if in_progress else "Jarayonda (bo‘sh) 🔴", "progress")]]
    if not review:
        rows.append([button("📷 Chala rasm", "incomplete"), button("🚕 Boshqa park", "otherpark")])
    rows.append([button("🗑 O‘chirish", "delete")])
    if not review:
        rows.append([button("📦 Hujjatlarni yuklab olish", "download")])
    return InlineKeyboardMarkup(rows)



def _comment_only_keyboard(applicant_user_id: int):
    # Existing conversations can finish, but no new comment buttons are sent.
    return None


def is_review_record(record: dict) -> bool:
    """Recognize both current and migrated NAMANTOSH review queue records."""
    return record.get('queue_kind') in ('incomplete', 'otherpark') or record.get('queue_stage') in ('incomplete', 'other')


async def refresh_driver_keyboards(application) -> None:
    """Upgrade stored driver controls on deployment; never touch plain forms."""
    from bot.background_ocr import control_lock
    seen = set()
    for store in ("applications", "app_messages"):
        for record in list(application.bot_data.get(store, {}).values()):
            if not isinstance(record, dict) or record.get("kind", "driver") != "driver":
                continue
            chat_id, message_id = record.get("group_chat_id"), record.get("kb_msg_id")
            user_id = record.get("applicant_id")
            if not chat_id or not message_id or user_id is None:
                continue
            key = _application_key(chat_id, message_id)
            if key in seen:
                continue
            seen.add(key)
            async with control_lock(chat_id, message_id):
                from bot.background_download import active
                if active(application, key):
                    continue
                if record.get('deleted_source_ids') or record.get('_transfer') or _record_for_key(application, key) is None:
                    continue
                try:
                    await with_keyboard_fallback(application.bot.edit_message_reply_markup,
                        chat_id=chat_id, message_id=message_id,
                        reply_markup=build_operator_keyboard(
                            user_id, application.bot_data.get("progress_state", {}).get(key, False),
                            review=is_review_record(record)
                        ),
                    )
                except BadRequest as error:
                    if "message is not modified" not in str(error).lower():
                        reason = _refresh_reason(error)
                        if reason == 'message_missing':
                            logger.info("Driver control refresh skipped: source=%s reason=%s", key, reason)
                        else:
                            logger.warning("Driver control refresh failed: source=%s reason=%s", key, reason)
                except Exception as error:
                    logger.warning("Driver control refresh failed: source=%s reason=%s", key, _refresh_reason(error))


def _forget_application(context, key, record):
    for store in ("applications", "app_messages"):
        records = context.bot_data.get(store, {})
        records.pop(key, None)
        legacy = records.get(record["applicant_id"])
        if isinstance(legacy, dict) and _same_application(
            legacy, record["applicant_id"], record["group_chat_id"], record["kb_msg_id"]
        ):
            records.pop(record["applicant_id"], None)
    context.bot_data.get("progress_state", {}).pop(key, None)
    from bot.background_ocr import cancel_if_removed
    cancel_if_removed(getattr(context, 'application', None), record.get('code'), record['applicant_id'])
    for store in ("pending_comments", "pending_user_replies", "operator_message_links"):
        entries = context.bot_data.get(store, {})
        for entry_key, target in list(entries.items()):
            if target == key:
                entries.pop(entry_key, None)
                if store == "pending_comments":
                    context.bot_data.get("pending_prompts", {}).pop(entry_key, None)


async def _delete_operator_application(context, key, record) -> bool:
    """Delete only recorded source messages; keep controls until photos are gone."""
    completed = record.setdefault("deleted_source_ids", [])
    ids = list(dict.fromkeys(record.get("photo_msg_ids", [])))
    ids = [mid for mid in ids if mid != record["kb_msg_id"]]
    ids.extend(mid for mid in record.get('zip_message_ids', []) if isinstance(mid, int))
    ids.append(record["kb_msg_id"])
    ids = list(dict.fromkeys(ids))
    for message_id in ids:
        if message_id in completed:
            continue
        try:
            await context.bot.delete_message(
                chat_id=record["group_chat_id"], message_id=message_id,
            )
        except BadRequest as error:
            if "message to delete not found" not in str(error).lower():
                logger.warning("Application source deletion failed for %s", key)
                return False
        except Exception:
            logger.warning("Application source deletion failed for %s", key)
            return False
        completed.append(message_id)
    _forget_application(context, key, record)
    return True


def _application_key(group_chat_id: int, kb_msg_id: int) -> tuple[int, int]:
    return int(group_chat_id), int(kb_msg_id)


def register_application(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    applicant_id: int,
    region: str,
    kind: str,
    group_chat_id: int,
    photo_msg_ids: list[int],
    kb_msg_id: int,
    applicant_name: str = "",
    username: str = "",
    media_albums: list[dict] | None = None,
    code: str = "",
):
    """Register a form application and return its stable record key.

    Form workers should call this immediately after sending their keyboard
    message.  ``app_messages[user_id]`` remains supported for older workers,
    but new records live under this chat/message identity.
    """
    key = _application_key(group_chat_id, kb_msg_id)
    record = {
        "application_key": key,
        "code": code,
        "applicant_id": int(applicant_id),
        "region": region,
        "kind": kind,
        "group_chat_id": int(group_chat_id),
        "photo_msg_ids": [int(mid) for mid in (photo_msg_ids or [])],
        "kb_msg_id": int(kb_msg_id),
        "applicant_name": applicant_name or "",
        "username": username or "",
        "archive_state": {"photos": {}, "kb": False},
    }
    if media_albums is not None:
        # File ids are deliberately persisted at submission time.  Telegram
        # message ids cannot be converted back into reusable file ids later.
        record["media_albums"] = media_albums
    context.bot_data.setdefault("applications", {})[key] = record
    # This alias is useful while the old driver worker is being migrated.  It
    # is still resolved only after source chat and keyboard id are checked.
    context.bot_data.setdefault("app_messages", {})[key] = record
    return key


def _legacy_record(applicant_id: int, value: dict) -> dict:
    """Normalize the old driver's app_messages[user_id] shape."""
    record = dict(value)
    record.setdefault("applicant_id", int(applicant_id))
    record.setdefault("region", "andijon")
    record.setdefault("kind", "driver")
    record.setdefault("applicant_name", "")
    record.setdefault("username", "")
    record.setdefault("archive_state", {"photos": {}, "kb": False})
    if "application_key" not in record:
        record["application_key"] = _application_key(
            record["group_chat_id"], record["kb_msg_id"]
        )
    return record


def _same_application(record: dict, applicant_id: int, chat_id: int, kb_id: int) -> bool:
    try:
        return (
            int(record.get("applicant_id")) == int(applicant_id)
            and int(record.get("group_chat_id")) == int(chat_id)
            and int(record.get("kb_msg_id")) == int(kb_id)
        )
    except (TypeError, ValueError):
        return False


def _find_application(
    context: ContextTypes.DEFAULT_TYPE,
    applicant_id: int,
    chat_id: int,
    kb_id: int,
) -> tuple[tuple[int, int], dict] | None:
    """Resolve only an exact callback message, never a user's latest record."""
    key = _application_key(chat_id, kb_id)
    candidates = []
    applications = context.bot_data.get("applications", {})
    if key in applications:
        candidates.append((key, applications[key]))

    # New records are also aliased in app_messages; old records are keyed by
    # applicant.  In either case exact chat/message checks are mandatory.
    app_messages = context.bot_data.get("app_messages", {})
    if key in app_messages:
        candidates.append((key, app_messages[key]))
    legacy = app_messages.get(applicant_id)
    if isinstance(legacy, dict):
        normalized = _legacy_record(applicant_id, legacy)
        candidates.append((normalized["application_key"], normalized))

    for candidate_key, record in candidates:
        if isinstance(record, dict) and _same_application(
            record, applicant_id, chat_id, kb_id
        ):
            # Keep the normalized legacy state in the store so retries are
            # idempotent even before the old worker is migrated.
            if candidate_key not in applications:
                applications[candidate_key] = record
            return candidate_key, record
    return None


def _record_for_key(context, key):
    if key is None:
        return None
    record = context.bot_data.get("applications", {}).get(key)
    if isinstance(record, dict):
        return record
    record = context.bot_data.get("app_messages", {}).get(key)
    return record if isinstance(record, dict) else None


def _branch(record: dict) -> str:
    try:
        return region_name(record["region"])
    except (KeyError, TypeError, ValueError):
        return str(record.get("region") or "Noma'lum hudud")


def _reply_keyboard(record: dict) -> InlineKeyboardMarkup:
    """Button carries both source ids, so a user's reply keeps its branch."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(
            "💬 Javob yozish",
            callback_data=(
                f"user:reply:{record['group_chat_id']}:{record['kb_msg_id']}"
            ),
        )]]
    )


def _old_reply_keyboard(group_chat_id: int) -> InlineKeyboardMarkup:
    # Kept solely for old comments which predate application registration.
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("💬 Javob yozish", callback_data=f"user:reply:{group_chat_id}")]]
    )


async def _archive_application(bot, record: dict, destination: str) -> bool:
    """Serialize archive attempts for one source application in this process."""
    if record.get("kind") in ("brand", "spectre"):
        logger.warning("archive: refusing brand/Spectre application")
        return False
    key = _application_key(record["group_chat_id"], record["kb_msg_id"])
    if key in _ARCHIVES_IN_FLIGHT:
        logger.warning("archive: copy already in progress for %s", key)
        return False

    _ARCHIVES_IN_FLIGHT.add(key)
    try:
        return await _archive_application_locked(bot, record, destination)
    finally:
        _ARCHIVES_IN_FLIGHT.discard(key)


async def _archive_application_locked(bot, record: dict, destination: str) -> bool:
    """Copy application media as one batch so Telegram preserves its album.

    Telegram may silently skip messages in ``copy_messages``.  A short result
    is therefore an indeterminate partial copy: it is recorded and never
    retried automatically (which would duplicate the messages that did copy).
    The source application remains untouched in that case.
    """
    state = record.setdefault("archive_state", {"photos": {}, "kb": False})
    # Remove the old persistent guard used by earlier versions.  It may be
    # stale after a process interruption and is not used for synchronization.
    state.pop("copying", None)

    if state.get("destination") != str(destination):
        state.clear()
        state.update(
            {
                "destination": str(destination),
                "photos": {},
                "photo_batch": {},
                "kb": False,
            }
        )

    copied_photos = state.setdefault("photos", {})
    # Bot API copyMessages requires strictly increasing message identifiers.
    photo_ids = sorted({int(mid) for mid in record.get("photo_msg_ids", [])})
    expected = {str(mid) for mid in photo_ids}
    source = record["group_chat_id"]
    failed = False

    try:
        media_albums = record.get("media_albums")
        if media_albums is not None:
            return await _archive_reconstructed_albums(
                bot, record, destination, state, media_albums
            )

        pending_ids = [mid for mid in photo_ids if not copied_photos.get(str(mid))]
        batch = state.setdefault("photo_batch", {})
        batch_ids = [int(mid) for mid in batch.get("message_ids", [])]

        if batch.get("status") == "partial" and batch_ids == pending_ids:
            # The API does not identify which source ids were skipped.  Trying
            # this batch again could duplicate an already archived album.
            failed = True
            logger.warning(
                "archive: refusing to retry partial photo batch %s (%s/%s copied)",
                pending_ids,
                batch.get("copied_count", 0),
                len(pending_ids),
            )
        elif pending_ids:
            if len(pending_ids) > 100:
                failed = True
                logger.warning(
                    "archive: photo batch has %s messages; Telegram limit is 100",
                    len(pending_ids),
                )
            else:
                try:
                    result = await bot.copy_messages(
                        chat_id=destination,
                        from_chat_id=source,
                        message_ids=pending_ids,
                    )
                    copied_count = len(result) if result is not None else 0
                    batch.clear()
                    batch.update(
                        {
                            "message_ids": pending_ids,
                            "copied_count": copied_count,
                            "status": (
                                "complete"
                                if copied_count == len(pending_ids)
                                else "partial"
                            ),
                        }
                    )
                    if copied_count != len(pending_ids):
                        failed = True
                        logger.warning(
                            "archive: Telegram copied only %s/%s photos; "
                            "automatic retry disabled",
                            copied_count,
                            len(pending_ids),
                        )
                    else:
                        state['destination_photo_ids'] = [
                            getattr(item, 'message_id', None) for item in result
                        ]
                        for message_id in pending_ids:
                            copied_photos[str(message_id)] = True
                except Exception as error:
                    # No successful result was confirmed, so this batch remains
                    # retryable on the next click.
                    failed = True
                    logger.warning("archive: photo album copy failed: %s", error)

        if not state.get("kb"):
            try:
                copied = await with_keyboard_fallback(bot.copy_message,
                    chat_id=destination,
                    from_chat_id=source,
                    message_id=record["kb_msg_id"],
                    reply_markup=record.get("_copy_markup", InlineKeyboardMarkup([])),
                )
                if copied is None:
                    raise RuntimeError("Telegram did not confirm the keyboard copy")
                state["kb"] = True
                state["destination_kb_id"] = getattr(copied, "message_id", None)
            except Exception as error:
                failed = True
                logger.warning("archive: keyboard message failed: %s", error)

        return not failed and expected.issubset(
            {marker for marker, copied in copied_photos.items() if copied}
        ) and bool(state.get("kb"))
    finally:
        # Also clean records checkpointed by the pre-transient-lock version.
        state.pop("copying", None)


def _album_chunks(items: list[dict]) -> list[list[dict]]:
    """Split at Telegram's limit without creating an avoidable singleton."""
    chunks = []
    remaining = list(items)
    while remaining:
        size = min(10, len(remaining))
        if len(remaining) > 10 and len(remaining) - size == 1:
            size -= 1
        chunks.append(remaining[:size])
        remaining = remaining[size:]
    return chunks


async def _archive_reconstructed_albums(
    bot, record: dict, destination: str, state: dict, media_albums
) -> bool:
    """Re-send new records from reusable file ids, preserving album boundaries."""
    album_state = state.setdefault("albums", {})
    failed = False
    expected_chunks = []

    if not isinstance(media_albums, list):
        return False

    # Never delete a source photo that is absent from the reconstruction plan.
    try:
        source_ids = []
        for album in media_albums:
            if not isinstance(album, dict) or not isinstance(album.get("photos"), list):
                return False
            ids = album.get("source_message_ids", [])
            if len(ids) != len(album["photos"]):
                return False
            source_ids.extend(int(mid) for mid in ids)
        if (
            len(source_ids) != len(set(source_ids))
            or set(source_ids) != {int(mid) for mid in record.get("photo_msg_ids", [])}
        ):
            logger.warning("archive: album metadata does not cover every source photo")
            return False
    except (TypeError, ValueError):
        return False

    for album_index, album in enumerate(media_albums):
        if not isinstance(album, dict) or not isinstance(album.get("photos"), list):
            failed = True
            continue
        photos = album["photos"]
        if not photos:
            failed = True
            continue
        for chunk_index, chunk in enumerate(_album_chunks(photos)):
            marker = f"{album_index}:{chunk_index}"
            expected_chunks.append(marker)
            checkpoint = album_state.setdefault(marker, {})
            if checkpoint.get("status") == "complete":
                continue
            if checkpoint.get("status") == "indeterminate":
                # Telegram may already have accepted this send.  Retrying could
                # duplicate it, so require manual intervention.
                failed = True
                continue

            if any(
                not isinstance(item, dict) or not item.get("file_id")
                for item in chunk
            ):
                checkpoint.update(status="indeterminate", reason="missing_file_id")
                failed = True
                continue

            try:
                if len(chunk) == 1:
                    item = chunk[0]
                    sent = await bot.send_photo(
                        chat_id=destination,
                        photo=item["file_id"],
                        caption=item.get("caption"),
                        parse_mode=item.get("parse_mode"),
                    )
                    if sent is None:
                        raise RuntimeError("Telegram did not confirm photo send")
                else:
                    media = [
                        InputMediaPhoto(
                            media=item["file_id"],
                            caption=item.get("caption"),
                            parse_mode=item.get("parse_mode"),
                        )
                        for item in chunk
                    ]
                    sent = await bot.send_media_group(
                        chat_id=destination, media=media
                    )
                    sent = list(sent or [])
                    group_ids = {
                        getattr(message, "media_group_id", None) for message in sent
                    }
                    if (
                        len(sent) != len(chunk)
                        or len(group_ids) != 1
                        or not next(iter(group_ids), None)
                    ):
                        checkpoint.update(
                            status="indeterminate",
                            confirmed_count=len(sent),
                            expected_count=len(chunk),
                        )
                        failed = True
                        continue
                message_ids = ([getattr(sent, 'message_id', None)] if len(chunk) == 1
                               else [getattr(item, 'message_id', None) for item in sent])
                checkpoint.clear()
                checkpoint["status"] = "complete"
                checkpoint["message_ids"] = message_ids
            except Exception as error:
                # Exceptions have no successful Bot API response and remain
                # retryable.  A malformed successful response above does not.
                failed = True
                logger.warning(
                    "archive: reconstructed album %s failed: %s", marker, error
                )

    if not state.get("kb"):
        try:
            copied = await with_keyboard_fallback(bot.copy_message,
                chat_id=destination,
                from_chat_id=record["group_chat_id"],
                message_id=record["kb_msg_id"],
                reply_markup=record.get("_copy_markup", InlineKeyboardMarkup([])),
            )
            if copied is None:
                raise RuntimeError("Telegram did not confirm the keyboard copy")
            state["kb"] = True
            state["destination_kb_id"] = getattr(copied, "message_id", None)
        except Exception as error:
            failed = True
            logger.warning("archive: keyboard message failed: %s", error)

    return (
        not failed
        and bool(expected_chunks)
        and all(
            album_state.get(marker, {}).get("status") == "complete"
            for marker in expected_chunks
        )
        and bool(state.get("kb"))
    )


async def on_operator_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot.background_ocr import control_lock
    from bot.background_download import active
    query = update.callback_query
    if query and query.message and update.effective_chat:
        key = (update.effective_chat.id, query.message.message_id)
        if active(context, key):
            await query.answer('ZIP tayyorlanmoqda. Shu ariza bo‘yicha amalni birozdan keyin bosing.', show_alert=True)
            return
        async with control_lock(*key):
            return await _on_operator_button(update, context)
    return await _on_operator_button(update, context)


async def _on_operator_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data or not query.message:
        return
    await query.answer()
    parts = query.data.split(":", 2)
    if len(parts) != 3 or parts[0] != "op":
        return
    action, applicant_id_str = parts[1], parts[2]
    if action == "other":  # Accept buttons left on older applications.
        action = "otherpark"
    try:
        applicant_id = int(applicant_id_str)
    except ValueError:
        return

    chat = update.effective_chat
    operator = update.effective_user
    if not chat or not operator:
        return
    op_name = operator.full_name or "Operator"

    # Old form records may have been lost on the external server. The original
    # bot-authored Spectre/Brand message is enough to disable its stale controls.
    if is_plain_application_message(query.message, getattr(context.bot, "id", None)):
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            logger.warning("Could not remove untracked plain-application keyboard.")
        await query.message.reply_text(
            "Spectre va Brend arizalarida operator tugmalari o‘chirilgan; ariza arxivlanmadi."
        )
        return

    # A reply's comment-only button is mapped to the original record by the
    # exact message that was sent to this group.
    link_key = context.bot_data.get("operator_message_links", {}).get(
        (chat.id, query.message.message_id)
    )
    resolved = _find_application(
        context, applicant_id, chat.id, query.message.message_id
    )
    if resolved is None and link_key is not None and action == "comment":
        record = _record_for_key(context, link_key)
        if record and _same_application(
            record, applicant_id, chat.id, record.get("kb_msg_id")
        ):
            resolved = (link_key, record)

    if resolved is not None and resolved[1].get("kind") in ("brand", "spectre"):
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception as error:
            logger.warning("plain application: could not remove old keyboard: %s", error)
        await query.message.reply_text(
            "Brend va Spectre arizalarida operator tugmalari va arxivlash o‘chirilgan."
        )
        return

    if action in ("ready", "progress", "incomplete", "otherpark", "delete", "download") and resolved is None:
        await query.message.reply_text(
            "⚠️ Bu tugma boshqa yoki eski arizaga tegishli. Ariza o‘zgartirilmadi."
        )
        return

    try:
        membership = await context.bot.get_chat_member(chat.id, operator.id)
    except Exception:
        await query.message.reply_text("Operator huquqini tekshirib bo‘lmadi. Qayta urinib ko‘ring.")
        return
    if membership.status not in ("administrator", "creator"):
        await query.message.reply_text("Bu tugmalar faqat guruh administratorlari uchun.")
        return

    if resolved is not None:
        key, record = resolved
        from bot.background_download import UNRESOLVED, recovery_text
        if record.get('zip_delivery_state') in UNRESOLVED and action in ('ready', 'delete', 'incomplete', 'otherpark', 'download'):
            await query.message.reply_text(recovery_text())
            return
        if record.get('transfer_source_key'):
            from bot.queue_moves import finish_source_cleanup
            if not await finish_source_cleanup(context, record):
                await query.message.reply_text("Asl guruhdagi arizani tozalash tugamadi. Botning o‘chirish huquqini tekshiring va qayta bosing.")
                return
        transfer = record.get('_transfer')
        if transfer and action != transfer['action']:
            await query.message.reply_text("Ariza boshqa guruhga ko‘chirilmoqda. Ko‘chirish tugmasini qayta bosing.")
            return

    if action in ("incomplete", "otherpark"):
        key, record = resolved
        if is_review_record(record):
            await query.message.reply_text("Bu guruhda Tayyor, O‘chirish va Jarayonda amallari mavjud.")
            return
        from bot.queue_moves import move_to_review
        result, detail = await move_to_review(context, key, record, action)
        if not result:
            await query.message.reply_text(detail)
        return

    if action == 'download':
        from bot.background_download import schedule
        if not schedule(context, resolved[0], resolved[1]):
            await query.message.reply_text('ZIP navbati band. Birozdan keyin qayta bosing.')
        return

    if action == "delete":
        key, record = resolved
        if not await _delete_operator_application(context, key, record):
            await query.message.reply_text(
                "⚠️ Ayrim xabarlarni o‘chirib bo‘lmadi. Bot ruxsatlarini tekshiring. "
                "Qolgan xabarlar uchun «O‘chirish»ni qayta bosishingiz mumkin."
            )
        return

    if action == "ready":
        key, record = resolved
        if (
            not record.get("group_chat_id")
            or not record.get("kb_msg_id")
            or record.get("applicant_id") is None
        ):
            await query.message.reply_text(
                "⚠️ Arizaning saqlangan xabar ma’lumotlari noto‘g‘ri. "
                "Ariza o‘chirilmadi."
            )
            return
        destination = archive_group(record.get("region", ""))
        if not destination:
            await query.message.reply_text(
                f"⚠️ {_branch(record)} uchun arxiv guruhi sozlanmagan. "
                "Ariza o‘chirilmadi."
            )
            return
        if not await _archive_application(context.bot, record, destination):
            archive_state = record.get("archive_state", {})
            photo_batch = archive_state.get("photo_batch", {})
            uncertain_album = any(
                checkpoint.get("status") == "indeterminate"
                for checkpoint in archive_state.get("albums", {}).values()
            )
            if photo_batch.get("status") == "partial" or uncertain_album:
                await query.message.reply_text(
                    f"⚠️ {_branch(record)} arxiviga barcha rasmlar to‘liq "
                    "o‘tgani tasdiqlanmadi. Muvaffaqiyatli nusxalarni takrorlamaslik "
                    "uchun albom avtomatik qayta yuborilmadi; asl ariza "
                    "o‘chirilmadi. Administrator arxiv va bot jurnalini "
                    "tekshirishi kerak."
                )
            else:
                await query.message.reply_text(
                    f"⚠️ {_branch(record)} arxivi to‘liq yuborilmadi, asl ariza "
                    "o‘chirilmadi. Sozlamani tekshirib, «Tayyor»ni qayta bosing."
                )
            return

        from bot.queue_moves import checkpoint
        if not await checkpoint(context):
            await query.message.reply_text("Arxiv nusxasi yaratildi, lekin holatni saqlab bo‘lmadi. Asl ariza o‘chirilmadi.")
            return
        if not await _delete_operator_application(context, key, record):
            await query.message.reply_text(
                "✅ Arxiv nusxasi tayyor. Ayrim asl xabarlarni o‘chirib bo‘lmadi. "
                "Bot ruxsatlarini tekshirib «Tayyor»ni qayta bosing."
            )
            return
        await checkpoint(context)
        try:
            await context.bot.send_message(
                chat_id=applicant_id,
                text=READY_TEXT + f"\n📍 <b>{h(_branch(record))}</b>\nAriza: <code>{h(record.get('code') or 'Eski ariza')}</code>",
                parse_mode="HTML",
            )
        except Exception:
            logger.warning("ready: applicant notification could not be delivered")
        return

    if action == "progress":
        key, record = resolved
        progress_state = context.bot_data.setdefault("progress_state", {})
        new_state = not progress_state.get(key, False)
        try:
            await with_keyboard_fallback(query.edit_message_reply_markup,
                reply_markup=build_operator_keyboard(applicant_id, in_progress=new_state, review=is_review_record(record))
            )
        except Exception:
            logger.warning("progress: could not update keyboard for %s", key)
            await query.message.reply_text("Holatni yangilab bo‘lmadi. Qayta urinib ko‘ring.")
            return
        progress_state[key] = new_state
        return


    if action == "comment":
        await query.message.reply_text(
            "«Izoh berish» o‘rniga endi «Chala rasm» va «Boshqa park» tugmalaridan foydalaning."
        )
        if resolved:
            key, record = resolved
            markup = (build_operator_keyboard(
                applicant_id, context.bot_data.get("progress_state", {}).get(key, False), review=is_review_record(record)
            ) if query.message.message_id == record["kb_msg_id"] else None)
            await query.edit_message_reply_markup(reply_markup=markup)
        return


async def on_operator_text_in_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Catch operator text, including /bekor, while a comment is pending."""
    msg = update.message
    chat = update.effective_chat
    operator = update.effective_user
    if not msg or not msg.text or not chat or not operator:
        return
    pending_key = (chat.id, operator.id)
    record_key = context.bot_data.get("pending_comments", {}).get(pending_key)
    record = _record_for_key(context, record_key)
    if record is None or int(record.get("group_chat_id", 0)) != int(chat.id):
        return
    if record.get("kind") in ("brand", "spectre"):
        context.bot_data.get("pending_comments", {}).pop(pending_key, None)
        context.bot_data.get("pending_prompts", {}).pop(pending_key, None)
        await msg.reply_text("Bu ariza turi uchun operator izohlari o‘chirilgan.")
        return
    text = msg.text.strip()
    if text.lower() in ("/bekor", "bekor"):
        context.bot_data.get("pending_comments", {}).pop(pending_key, None)
        await msg.reply_text("❌ Izoh bekor qilindi.")
        return

    applicant_id = record["applicant_id"]
    try:
        sent = await context.bot.send_message(
            chat_id=applicant_id,
            text=(
                f"💬 <b>WB LEGENDA — operator izohi</b>\n"
                f"📍 <b>{h(_branch(record))}</b>\n\n{h(text)}"
            ),
            parse_mode="HTML",
            reply_markup=_reply_keyboard(record),
        )
        context.bot_data.setdefault("operator_message_links", {})[
            (chat.id, sent.message_id)
        ] = record_key
        await msg.reply_text(
            f"✅ Izoh arizachiga yuborildi.\n📍 {_branch(record)}\n"
            f"👤 Operator: {operator.full_name or 'Operator'}"
        )
    except Exception as error:
        logger.exception("comment: failed to send to %s", applicant_id)
        await msg.reply_text(
            f"⚠️ Izoh yuborilmadi: {error}\n"
            "Sabab: foydalanuvchi botni bloklagan yoki /start bosmagan."
        )
    finally:
        context.bot_data.get("pending_comments", {}).pop(pending_key, None)


async def on_user_reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User selects a reply target using the exact comment callback."""
    query = update.callback_query
    if not query or not query.data or not query.message:
        return
    await query.answer()
    parts = query.data.split(":")
    if len(parts) not in (3, 4) or parts[:2] != ["user", "reply"]:
        return
    user = update.effective_user
    if not user:
        return

    record = None
    key = None
    if len(parts) == 4:
        try:
            key = _application_key(int(parts[2]), int(parts[3]))
        except ValueError:
            return
        record = _record_for_key(context, key)
        if (
            record is None
            or int(record.get("applicant_id", -1)) != int(user.id)
            or int(record.get("group_chat_id", 0)) != int(parts[2])
            or int(record.get("kb_msg_id", 0)) != int(parts[3])
        ):
            await query.message.reply_text("⚠️ Bu javob arizaga tegishli emas.")
            return
    else:
        # Legacy callback has no keyboard id.  It is safe only when exactly
        # one application for this user exists in that source group.
        try:
            group_id = int(parts[2])
        except ValueError:
            return
        matches = []
        for candidate_key, candidate in context.bot_data.get("applications", {}).items():
            if (
                isinstance(candidate, dict)
                and str(candidate.get("applicant_id")) == str(user.id)
                and str(candidate.get("group_chat_id")) == str(group_id)
            ):
                matches.append((candidate_key, candidate))
        legacy = context.bot_data.get("app_messages", {}).get(user.id)
        if isinstance(legacy, dict):
            candidate = _legacy_record(user.id, legacy)
            if int(candidate.get("group_chat_id", 0)) == group_id:
                if all(
                    candidate_key != candidate["application_key"]
                    for candidate_key, _ in matches
                ):
                    matches.append((candidate["application_key"], candidate))
                context.bot_data.setdefault("applications", {})[
                    candidate["application_key"]
                ] = candidate
        if len(matches) != 1:
            await query.message.reply_text("⚠️ Javob arizasi aniqlanmadi.")
            return
        key, record = matches[0]

    if record.get("kind") in ("brand", "spectre"):
        context.bot_data.get("pending_user_replies", {}).pop(user.id, None)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            logger.warning("Could not remove obsolete reply keyboard.")
        await query.message.reply_text("Bu ariza bo‘yicha bot orqali yozishma o‘chirilgan.")
        return
    context.bot_data.setdefault("pending_user_replies", {})[user.id] = key
    await query.message.reply_text(
        f"✏️ Javobingizni yozing ({h(_branch(record))}):\n\nBekor qilish uchun: /bekor"
    )


async def on_user_reply_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Forward a user's reply to the group belonging to that application."""
    msg = update.message
    user = update.effective_user
    if not msg or not user:
        return
    pending = context.bot_data.get("pending_user_replies", {})
    record_key = pending.get(user.id)
    if record_key is None:
        return
    record = _record_for_key(context, record_key)
    if (
        record is None
        or int(record.get("applicant_id", -1)) != int(user.id)
        or record.get("kind") in ("brand", "spectre")
    ):
        pending.pop(user.id, None)
        await msg.reply_text(
            "Bu ariza bo‘yicha yozishma yopilgan. Davom etish uchun menyudan bo‘lim tanlang."
        )
        return
    text = (msg.text or "").strip()
    if not text:
        await msg.reply_text("❗ Iltimos, matn yozing.")
        return
    if text.lower() in ("/bekor", "bekor"):
        pending.pop(user.id, None)
        await msg.reply_text("❌ Javob bekor qilindi.")
        return

    user_link = (
        f'<a href="https://t.me/{user.username}">{h(user.full_name)}</a>'
        if user.username
        else f'<a href="tg://user?id={user.id}">{h(user.full_name or "Foydalanuvchi")}</a>'
    )
    try:
        sent = await context.bot.send_message(
            chat_id=record["group_chat_id"],
            text=(
                f"📩 <b>Arizachi javobi:</b>\n"
                f"📍 <b>{h(_branch(record))}</b>\n"
                f"👤 {user_link}\n\n{h(text)}"
            ),
            parse_mode="HTML",
            reply_markup=_comment_only_keyboard(user.id),
        )
        context.bot_data.setdefault("operator_message_links", {})[
            (record["group_chat_id"], sent.message_id)
        ] = record_key
        await msg.reply_text(
            f"✅ Javobingiz operatorlarga yuborildi ({_branch(record)})."
        )
    except Exception as error:
        logger.exception("user_reply: failed to send to group %s", record["group_chat_id"])
        await msg.reply_text(f"⚠️ Javob yuborilmadi: {error}")
    finally:
        pending.pop(user.id, None)


def register_operator_handlers(app):
    from bot.background_download import register_handlers as register_download_handlers
    register_download_handlers(app)
    app.add_handler(CommandHandler(
        "tugmasiz", remove_buttons_command, filters=filters.ChatType.GROUPS,
    ))
    app.add_handler(CallbackQueryHandler(on_operator_button, pattern=r"^op:"))
    app.add_handler(CallbackQueryHandler(on_user_reply_button, pattern=r"^user:reply:"))
    # Do not exclude commands: /bekor must be consumed while a relay is
    # pending.  The functions return immediately when no relay is pending.
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.TEXT, on_operator_text_in_group),
        group=1,
    )
