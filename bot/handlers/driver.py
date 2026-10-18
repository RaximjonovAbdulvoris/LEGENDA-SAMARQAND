import asyncio
import logging
import re
from html import escape as h

from telegram import (
    InputMediaPhoto,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.config import template_path
from bot.document_ocr import prepare_document_summary, SOURCES, enabled
from bot.background_ocr import PENDING_TEXT, schedule_ocr
from bot.driver_card import details as driver_details
from bot.application_ids import application_code
from bot.vehicle_numbers import normalize_plate
from bot.presentation import contact_button, menu_button, premium_markup, send_brand_sticker, send_warning_voice, with_keyboard_fallback
from bot.counter import next_index
from bot.handlers.operator import build_operator_keyboard, register_application
from bot.handlers.start import MENU_DRIVER, main_keyboard, require_region, cancel
from bot.regions import (
    clear_application,
    driver_groups,
    get_region,
    region_name,
)
from bot.subscription import require_subscription, subscription_keyboard

logger = logging.getLogger(__name__)

# Module-level dict to track delayed progress check tasks per user.
# Not stored in user_data so they don't end up in pickle persistence.
_pending_car_progress_tasks: dict[int, asyncio.Task] = {}

(
    NAME,
    PHONE,
    WARN_DOCS,
    PASSPORT_FRONT,
    PASSPORT_BACK,
    LICENSE_FRONT,
    LICENSE_BACK,
    TECH_FRONT,
    TECH_BACK,
    SELFIE,
    LITSENZIYA,
    CAR_PHOTOS,
    CAR_PLATE,
) = range(13)

JOIN_GROUP = 13
REQUIRED_GROUP = "@WBLEGENDA_KANAL"
JOIN_KEYBOARD = subscription_keyboard("driver:check_membership")

CONTINUE_BTN = "✅ Davom etish"
CONTINUE_KB = ReplyKeyboardMarkup(
    [[menu_button(CONTINUE_BTN, icon="READY")]], resize_keyboard=True, one_time_keyboard=True
)


def _regional_keyboard(context):
    return main_keyboard(get_region(context))


def _region_required(context) -> str:
    region = get_region(context)
    if not region:
        raise RuntimeError("Tasdiqlangan hudud topilmadi")
    return region


async def _require_region(update, context) -> bool:
    return await require_region(update, context)


async def _destination_error(update, context, text: str) -> None:
    await update.effective_message.reply_text(
        f"⚠️ {text}\nIltimos, keyinroq qayta urinib ko‘ring.",
        reply_markup=_regional_keyboard(context),
    )


# -------------------- prompt sender --------------------
async def _send_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       text: str, template_name: str | None = None,
                       reply_markup=None):
    """Send a prompt, attaching the cached template image if available.
    Falls back to text-only on any error so the flow never gets stuck."""
    if not template_name:
        await update.message.reply_text(
            text, reply_markup=reply_markup, parse_mode="Markdown"
        )
        return

    cache = context.bot_data.setdefault("template_file_ids", {})
    file_id = cache.get(template_name)

    try:
        if file_id:
            await update.message.reply_photo(
                photo=file_id, caption=text,
                parse_mode="Markdown", reply_markup=reply_markup,
            )
            return

        path = template_path(template_name)
        if not path:
            await update.message.reply_text(
                text, reply_markup=reply_markup, parse_mode="Markdown"
            )
            return

        with open(path, "rb") as f:
            sent = await update.message.reply_photo(
                photo=f, caption=text,
                parse_mode="Markdown", reply_markup=reply_markup,
            )
        if sent and sent.photo:
            cache[template_name] = sent.photo[-1].file_id
    except Exception as e:
        logger.warning("template '%s' yuborilmadi: %s", template_name, e)
        await update.message.reply_text(
            text, reply_markup=reply_markup, parse_mode="Markdown"
        )


# -------------------- entry --------------------
async def start_driver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await _require_region(update, context):
        return ConversationHandler.END
    region = get_region(context)
    # A new form always starts with only the selected branch retained.
    clear_application(context)
    context.user_data["_application_kind"] = "driver"
    if not driver_groups(region):
        await _destination_error(
            update, context,
            f"{region_name(region)} uchun haydovchilar guruhi sozlanmagan.",
        )
        clear_application(context)
        return ConversationHandler.END
    return await check_membership(update, context)


async def check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Membership may be checked long after the menu was opened.  Revalidate
    # the branch and destination before accepting the first form answer.
    if not await _require_region(update, context):
        return ConversationHandler.END
    region = _region_required(context)
    if not driver_groups(region):
        if update.callback_query:
            await update.callback_query.answer()
        await _destination_error(
            update, context,
            f"{region_name(region)} uchun haydovchilar guruhi sozlanmagan.",
        )
        clear_application(context)
        return ConversationHandler.END
    if not await require_subscription(
        update, context, callback_data="driver:check_membership"
    ):
        return JOIN_GROUP

    # Keep the confirmed region while discarding any stale form data.
    clear_application(context)
    context.user_data["_application_kind"] = "driver"
    context.user_data["_application_region"] = region
    await update.effective_message.reply_text(
        f"{premium_markup('APPLICATION', '📝')} <b>{region_name(region)} — haydovchilik arizasi</b>\n\n"
        "Ism va familiyangiz pravangizdan avtomatik o‘qiladi.\n"
        "Iltimos, <b>telefon raqamingizni</b> yuboring:",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup([[contact_button()]], resize_keyboard=True, one_time_keyboard=True),
    )
    return PHONE


# -------------------- 1. name --------------------
async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.message.text or "").strip()
    if len(name) < 3:
        await update.message.reply_text(
            "❗ Iltimos, to'liq ism va familiyangizni yozing:"
        )
        return NAME
    context.user_data["name"] = name
    kb = ReplyKeyboardMarkup(
        [[contact_button()]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        "📞 Iltimos, telefon raqamingizni jo'nating (knopka orqali):",
        reply_markup=kb,
    )
    return PHONE


async def name_wrong(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "❗ Iltimos, *matn* ko'rinishida ism va familiyangizni yozing:",
        parse_mode="Markdown",
    )
    return NAME


# -------------------- 2. phone --------------------
async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    phone = None
    if update.message.contact:
        phone = update.message.contact.phone_number
    elif update.message.text:
        phone = update.message.text.strip()
    digits = re.sub(r'\D', '', phone or '')
    if len(digits) == 9:
        digits = '998' + digits
    if not re.fullmatch(r'998[0-9]{9}', digits):
        await update.message.reply_text(
            "❗ Iltimos, telefon raqamingizni knopka orqali jo'nating "
            "yoki to'g'ri raqam kiriting (masalan: 998901234567):"
        )
        return PHONE
    context.user_data["phone"] = digits

    await update.message.reply_text(
        "⚠️ *Diqqat!*\n\n"
        "Endi sizdan hujjatlaringizning *originalini rasmga olib* jo'natishingiz so'raladi.\n\n"
        "❌ Soliqdan olingan skrinshot QABUL QILINMAYDI!\n\n"
        "Hujjatlardagi ma’lumotlar arizani ko‘rib chiqish uchun operatorlarga va "
        "avtomatik o‘qish uchun bizning serverimizdagi mahalliy AI orqali qayta ishlanadi. "
        "Davom etish orqali ushbu ishlov berishga rozilik bildirasiz.",
        parse_mode="Markdown",
        reply_markup=CONTINUE_KB,
    )
    await send_warning_voice(update.message)
    return WARN_DOCS


async def phone_wrong(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "❗ Iltimos, *📞 Raqamni jo'natish* knopkasini bosing yoki raqamingizni yozing:",
        parse_mode="Markdown",
    )
    return PHONE


# -------------------- 3. warn --------------------
async def warn_docs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _send_prompt(
        update, context,
        "📄 1/11 — *Passport (old tarafi)* rasmini jo'nating:",
        "passport_front",
        reply_markup=ReplyKeyboardRemove(),
    )
    return PASSPORT_FRONT


async def warn_wrong(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        f"❗ Iltimos, *{CONTINUE_BTN}* knopkasini bosing:",
        parse_mode="Markdown",
        reply_markup=CONTINUE_KB,
    )
    return WARN_DOCS


# -------------------- photo step factory --------------------
def _make_photo_step(field: str, next_text: str,
                     next_template: str | None, current_state: int,
                     next_state: int):
    """Create (handler, wrong-input handler) for a single-photo step."""

    async def get(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        if not update.message.photo:
            return await wrong(update, context)
        context.user_data[field] = update.message.photo[-1].file_id
        await _send_prompt(update, context, next_text, next_template)
        return next_state

    async def wrong(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await update.message.reply_text(
            "❗ Iltimos, *rasm jo'nating*! Matn yoki boshqa tur qabul qilinmaydi.",
            parse_mode="Markdown",
        )
        return current_state

    return get, wrong


get_passport_front, _wrong_pf = _make_photo_step(
    "passport_front",
    "🪪 2/11 — *Haydovchilik guvohnomasi (old tarafi)* rasmini jo'nating:",
    "license_front", PASSPORT_FRONT, LICENSE_FRONT,
)
# Keep the old state so applications already waiting for the back photo can finish.
get_passport_back, _wrong_pb = _make_photo_step(
    "passport_back",
    "🪪 2/11 — *Haydovchilik guvohnomasi (old tarafi)* rasmini jo'nating:",
    "license_front", PASSPORT_BACK, LICENSE_FRONT,
)
get_license_front, _wrong_lf = _make_photo_step(
    "license_front",
    "🪪 3/11 — *Haydovchilik guvohnomasi (orqa tarafi)* rasmini jo'nating:",
    "license_back", LICENSE_FRONT, LICENSE_BACK,
)
get_license_back, _wrong_lb = _make_photo_step(
    "license_back",
    "🚘 4/11 — *Texnik passport (old tarafi)* rasmini jo'nating:",
    "tech_front", LICENSE_BACK, TECH_FRONT,
)
get_tech_front, _wrong_tf = _make_photo_step(
    "tech_front",
    "🚘 5/11 — *Texnik passport (orqa tarafi)* rasmini jo'nating:",
    "tech_back", TECH_FRONT, TECH_BACK,
)
get_tech_back, _wrong_tb = _make_photo_step(
    "tech_back",
    "🤳 6/11 — *Selfie* rasmingizni jo'nating:",
    "selfie", TECH_BACK, SELFIE,
)
get_selfie, _wrong_se = _make_photo_step(
    "selfie",
    "📜 7/11 — *Litsenziya* rasmini jo'nating:",
    "litsenziya", SELFIE, LITSENZIYA,
)


# -------------------- 11. litsenziya -> ask car photos --------------------
async def get_litsenziya(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        return await litsenziya_wrong(update, context)
    context.user_data["litsenziya"] = update.message.photo[-1].file_id
    context.user_data["car_photos"] = []
    await _send_car_photo_prompt(update, context)
    return CAR_PHOTOS


async def _send_car_photo_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send 4 car template images as a media group with instruction caption on first image."""
    car_names = ["car_front", "car_back", "car_left", "car_right"]
    instruction = (
        "🚗 *8-11/11 — Mashina rasmlari*\n\n"
        "Mashinangizning *4 ta tomonidan* rasmga olib jo'nating:\n"
        "1️⃣ Old • 2️⃣ Orqa • 3️⃣ Chap • 4️⃣ O'ng\n\n"
        "Hammasini birin-ketin (4 ta rasm) jo'natishingiz kerak."
    )

    cache = context.bot_data.setdefault("template_file_ids", {})
    paths = [template_path(name) for name in car_names]
    if paths[0] and len(set(paths)) == 1 and 'car_sides.' in paths[0]:
        try:
            if cache.get('car_front'):
                await update.message.reply_photo(photo=cache['car_front'], caption=instruction,
                                                 parse_mode='Markdown')
            else:
                with open(paths[0], 'rb') as original:
                    await update.message.reply_photo(photo=original, caption=instruction,
                                                     parse_mode='Markdown')
        except Exception as error:
            logger.warning('car template yuborilmadi: %s', type(error).__name__)
            await update.message.reply_text(instruction, parse_mode='Markdown')
        return
    media = []
    media_names = []
    opened_files = []

    try:
        for i, name in enumerate(car_names):
            caption = instruction if not media else None
            parse_mode = "Markdown" if not media else None
            file_id = cache.get(name)
            if file_id:
                media.append(
                    InputMediaPhoto(media=file_id, caption=caption, parse_mode=parse_mode)
                )
                media_names.append(name)
            else:
                path = template_path(name)
                if path:
                    f = open(path, "rb")
                    opened_files.append((name, f))
                    media.append(
                        InputMediaPhoto(media=f, caption=caption, parse_mode=parse_mode)
                    )
                    media_names.append(name)

        if media:
            sent_msgs = await update.message.reply_media_group(media=media)
            # Cache returned file_ids by index
            for i, msg in enumerate(sent_msgs):
                if msg.photo and i < len(media_names):
                    cache[media_names[i]] = msg.photo[-1].file_id
        else:
            await update.message.reply_text(instruction, parse_mode="Markdown")
    except Exception as e:
        logger.warning("car template rasmlar yuborilmadi: %s", e)
        await update.message.reply_text(instruction, parse_mode="Markdown")
    finally:
        for _, f in opened_files:
            f.close()


async def litsenziya_wrong(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "❗ Iltimos, *litsenziya rasmini* jo'nating!",
        parse_mode="Markdown",
    )
    return LITSENZIYA


# -------------------- 12. car photos (4) --------------------
async def _delayed_car_progress(chat_id: int, user_id: int,
                                 user_data: dict, bot) -> None:
    """After a short delay, send a progress message if photos are still incomplete.

    Used for album uploads: when an album arrives with <4 photos, we wait briefly
    in case more are coming, then prompt the user for the remaining ones.
    """
    try:
        await asyncio.sleep(2.0)
        photos = user_data.get("car_photos", [])
        count = len(photos)
        if 0 < count < 4:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"✅ {count}/4 ta rasm qabul qilindi.\n"
                    f"Yana *{4 - count} ta* rasm jo'nating "
                    f"(qolgan tomonlari)."
                ),
                parse_mode="Markdown",
            )
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning("car progress check failed: %s", e)
    finally:
        _pending_car_progress_tasks.pop(user_id, None)


async def get_car_photos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Accept car photos one-by-one OR as a media group (album).

    Rules:
    - Photos in an album arrive as separate updates with the same media_group_id.
    - Album photos: collect silently, but schedule a debounced progress check.
      If 2 seconds pass without new photos and total < 4, prompt user for the rest.
    - Individual photos: send a progress message immediately.
    - If user already sent 4+, ignore extras and stay on CAR_PLATE quietly.
    """
    photos = context.user_data.setdefault("car_photos", [])

    if not update.message.photo:
        await update.message.reply_text(
            f"❗ *Mashina rasmlari kerak*! Hozir {len(photos)}/4 ta yuborildi. "
            "Iltimos, *rasm* jo'nating.",
            parse_mode="Markdown",
        )
        return CAR_PHOTOS

    # Already have 4 — ignore extra photos that may arrive late from a big album
    if len(photos) >= 4:
        return CAR_PLATE

    photos.append(update.message.photo[-1].file_id)
    is_album = bool(update.message.media_group_id)
    user_id = update.effective_user.id if update.effective_user else 0

    # Cancel any pending progress check (we just got a new photo)
    prev_task = _pending_car_progress_tasks.pop(user_id, None)
    if prev_task and not prev_task.done():
        prev_task.cancel()

    if len(photos) >= 4:
        # Done — ask for plate number
        await update.message.reply_text(
            "✅ *4 ta rasm qabul qilindi!*\n\n"
            "🔢 Endi mashinangizning *davlat raqamini* yozing (masalan: `01A123BC`):",
            parse_mode="Markdown",
        )
        return CAR_PLATE

    if is_album:
        # Schedule debounced progress check: if no more photos arrive in 2s,
        # tell the user how many they still need to send.
        chat_id = update.effective_chat.id
        task = asyncio.create_task(
            _delayed_car_progress(chat_id, user_id,
                                   context.user_data, context.bot)
        )
        _pending_car_progress_tasks[user_id] = task
        return CAR_PHOTOS

    # Single photo, not an album — show progress immediately
    await update.message.reply_text(
        f"✅ {len(photos)}/4 ta rasm qabul qilindi. "
        f"Yana *{4 - len(photos)} ta* rasm jo'nating.",
        parse_mode="Markdown",
    )
    return CAR_PHOTOS


async def car_photos_wrong(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photos = context.user_data.get("car_photos", [])
    await update.message.reply_text(
        f"❗ Iltimos, *mashina rasmini* jo'nating! ({len(photos)}/4 yuborildi)",
        parse_mode="Markdown",
    )
    return CAR_PHOTOS


# -------------------- 13. car plate -> finish --------------------
async def get_car_plate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if context.user_data.get("_delivery_uncertain"):
        await update.message.reply_text(
            "⚠️ Oldingi yuborish natijasi noma’lum. Takroriy ariza yubormaslik uchun "
            "operator bilan bog‘laning. Ma’lumotlaringiz saqlandi."
        )
        return CAR_PLATE
    plate = normalize_plate(update.message.text)
    if not plate:
        await update.message.reply_text(
            "❗ Iltimos, to'g'ri davlat raqami kiriting (masalan: `01A123BC`):",
            parse_mode="Markdown",
        )
        return CAR_PLATE
    if not await _require_region(update, context):
        return ConversationHandler.END
    region = _region_required(context)
    if context.user_data.get("_application_region") != region:
        clear_application(context)
        await update.message.reply_text(
            "⚠️ Hudud almashtirilgani uchun eski ariza bekor qilindi. "
            "Yangi hudud uchun arizani qayta boshlang.",
            reply_markup=_regional_keyboard(context),
        )
        return ConversationHandler.END
    context.user_data["car_plate"] = plate

    user = update.effective_user
    context.user_data["user_id"] = user.id
    context.user_data["user_username"] = user.username or ""
    context.user_data["user_full_name"] = user.full_name or ""

    await update.message.reply_text(
        "⏳ Arizangiz operatorga yuborilmoqda. AI tekshiruvi keyin davom etadi."
    )
    try:
        await _send_to_driver_group(context)
    except Exception as e:
        logger.exception("driver: send_to_driver_group failed: %s", e)
        if isinstance(e, NetworkError) and not isinstance(e, BadRequest):
            context.user_data["_delivery_uncertain"] = True
            await update.message.reply_text(
                "⚠️ Aloqa uzildi; ariza yetib borgani tasdiqlanmadi. "
                "Ma’lumotlar saqlandi. Qayta ariza yuborishdan oldin operator bilan bog‘laning."
            )
            return CAR_PLATE
        await update.message.reply_text(
            "⚠️ Ariza to‘liq yuborilmadi. Ma’lumotlar saqlandi. "
            "Qayta urinish uchun davlat raqamini yana yuboring yoki /cancel bosing."
        )
        return CAR_PLATE

    await send_brand_sticker(update.message, 'SUCCESS')
    await update.message.reply_text(
        "✅ <b>Arizangiz qabul qilindi</b>\n\n"
        f"Ariza kodi: <code>{h(context.user_data['_application_code'])}</code>\n"
        f"Hudud: {h(region_name(_region_required(context)))}\n\n"
        "Hujjatlaringiz operatorga yuborildi. Natija yoki qo‘shimcha ma’lumot "
        "kerak bo‘lsa, shu bot orqali xabar beramiz.\n\n"
        "Aloqa: +998 78 150-50-50",
        parse_mode="HTML", reply_markup=_regional_keyboard(context),
    )
    clear_application(context)
    return ConversationHandler.END


async def car_plate_wrong(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "❗ Iltimos, davlat raqamini *matn ko'rinishida* yozing (masalan: `01A123BC`):",
        parse_mode="Markdown",
    )
    return CAR_PLATE


# -------------------- group dispatch --------------------
async def _send_to_driver_group(context: ContextTypes.DEFAULT_TYPE):
    """Send the application to ONE driver group, rotating through them."""
    d = context.user_data
    region = _region_required(context)
    flow_region = d.get("_application_region")
    if flow_region != region:
        raise RuntimeError("Application region changed while form was in progress")
    groups = driver_groups(region)
    # Recheck immediately before sending: configuration can change while a
    # user is completing the form, and never fall back to another region.
    if not groups:
        raise RuntimeError(f"{region_name(region)} driver destination is missing")
    dispatch = d.setdefault("_dispatch", {})
    if "chat_id" not in dispatch:
        dispatch["index"] = next_index(f"driver_group_rr:{region}", len(groups))
        dispatch["chat_id"] = groups[dispatch["index"]]
    idx, chat_id = dispatch["index"], dispatch["chat_id"]

    user_id = d.get("user_id")
    username = d.get("user_username", "")
    full_name = d.get("user_full_name", "") or "Foydalanuvchi"
    display = f"@{username}" if username else full_name
    user_link_html = (
        f'<a href="tg://user?id={user_id}">{h(display)}</a>' if user_id else h(display)
    )

    code = application_code(d)
    initial = {'status': 'pending', 'values': {}}

    prefix = (
        f"<b>WB LEGENDA · HAYDOVCHI ARIZASI</b>\n"
        f"Ariza: <code>{code}</code> · {h(region_name(region))}\n\n"
        f"<b>Arizachi</b>\n{user_link_html}\n\n"
    )
    suffix = "\n\n<b>Operator boshqaruvi</b>\nQuyidagi amallardan birini tanlang."
    details = prefix + driver_details(initial, d.get('phone'), d.get('car_plate')) + suffix
    caption_main = f"📎 <b>{code}</b> · Hujjatlar va avtomobil · 1/2"
    caption_selfie = f"📎 <b>{code}</b> · Selfie va litsenziya · 2/2"

    main_album_ids = [
        d.get("passport_front"),
        d.get("passport_back"),
        d.get("tech_front"),
        d.get("tech_back"),
        d.get("license_front"),
        d.get("license_back"),
        *d.get("car_photos", []),
    ]
    main_album_ids = [pid for pid in main_album_ids if pid]
    selfie_album_ids = [d.get("selfie"), d.get("litsenziya")]
    selfie_album_ids = [pid for pid in selfie_album_ids if pid]

    # photo_msg_ids retain source deletion compatibility; media_albums below
    # retain reusable file ids, captions, and the two album boundaries.
    # kb_msg_id is copied separately without its inline keyboard.
    photo_msg_ids = dispatch.setdefault("photo_msg_ids", [])
    media_albums = dispatch.setdefault("media_albums", [])

    if main_album_ids and not any(a["name"] == "main" for a in media_albums):
        main_media = [
            InputMediaPhoto(
                media=fid,
                caption=caption_main if i == 0 else None,
                parse_mode=ParseMode.HTML if i == 0 else None,
            )
            for i, fid in enumerate(main_album_ids)
        ]
        main_sent = await context.bot.send_media_group(chat_id=chat_id, media=main_media)
        main_message_ids = [m.message_id for m in main_sent]
        photo_msg_ids.extend(main_message_ids)
        media_albums.append(
            {
                "name": "main",
                "source_message_ids": main_message_ids,
                "photos": [
                    {
                        "file_id": fid,
                        "caption": caption_main if i == 0 else None,
                        "parse_mode": ParseMode.HTML if i == 0 else None,
                    }
                    for i, fid in enumerate(main_album_ids)
                ],
            }
        )

    if selfie_album_ids and not any(a["name"] == "selfie" for a in media_albums):
        selfie_media = [
            InputMediaPhoto(
                media=fid,
                caption=caption_selfie if i == 0 else None,
                parse_mode=ParseMode.HTML if i == 0 else None,
            )
            for i, fid in enumerate(selfie_album_ids)
        ]
        selfie_sent = await context.bot.send_media_group(chat_id=chat_id, media=selfie_media)
        selfie_message_ids = [m.message_id for m in selfie_sent]
        photo_msg_ids.extend(selfie_message_ids)
        media_albums.append(
            {
                "name": "selfie",
                "source_message_ids": selfie_message_ids,
                "photos": [
                    {
                        "file_id": fid,
                        "caption": caption_selfie if i == 0 else None,
                        "parse_mode": ParseMode.HTML if i == 0 else None,
                    }
                    for i, fid in enumerate(selfie_album_ids)
                ],
            }
        )

    if user_id:
        # Clear old progress state so new application shows fresh 🔴 button
        context.bot_data.get("progress_state", {}).pop(user_id, None)

        # Cache applicant info so operator.py can build a proper mention link
        applicant_cache = context.bot_data.setdefault("applicant_info", {})
        applicant_cache[user_id] = {
            "name": d.get("name") or full_name or "Arizachi",
            "username": username,
        }

        if "kb_msg_id" not in dispatch:
            kb_msg = await with_keyboard_fallback(context.bot.send_message,
                chat_id=chat_id,
                text=details,
                parse_mode=ParseMode.HTML,
                reply_markup=build_operator_keyboard(user_id),
            )
            dispatch["kb_msg_id"] = kb_msg.message_id

        record_key = register_application(
            context,
            applicant_id=user_id,
            region=region,
            kind="driver",
            group_chat_id=chat_id,
            photo_msg_ids=photo_msg_ids,
            kb_msg_id=dispatch["kb_msg_id"],
            applicant_name=d.get("name") or full_name or "Arizachi",
            username=username,
            media_albums=media_albums,
            code=code,
        )

        if enabled():
            record = context.bot_data['applications'][record_key]
            record.update(ocr_status='pending', ocr_prefix=prefix, ocr_suffix=suffix,
                          ocr_card_data={'phone': d.get('phone'), 'plate': d.get('car_plate')},
                          ocr_input={**{source: d[source] for source in SOURCES if d.get(source)},
                                     'car_plate': d.get('car_plate', '')})
            application = getattr(context, 'application', None)
            # Durable inputs belong to this application, never the user's next form.
            if application is not None:
                from bot.queue_moves import checkpoint
                await checkpoint(context)
                schedule_ocr(application, code, user_id)

    logger.info("[driver] sent application to group #%s (%s)", idx + 1, chat_id)


# -------------------- conversation builder --------------------
def _photo_state(get_handler, wrong_handler):
    """Photo states accept ONLY photos; everything else triggers wrong handler."""
    return [
        MessageHandler(filters.PHOTO, get_handler),
        MessageHandler(~filters.COMMAND, wrong_handler),
    ]


async def submission_waiting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Do not allow /start or another form to erase a document batch being read.
    if update.message:
        await update.message.reply_text(
            "⏳ Arizangiz hali tekshirilmoqda. Tayyor bo‘lgach xabar beramiz. "
            "Hujjatlarni qayta yuborishingiz shart emas."
        )
    return ConversationHandler.WAITING


def build_driver_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.ChatType.PRIVATE & filters.Regex(r"^(?:(?:📝|🚕) )?Ulanish uchun Ariza$"),
                start_driver,
            ),
        ],
        states={
            ConversationHandler.WAITING: [MessageHandler(filters.ALL, submission_waiting)],
            JOIN_GROUP: [
                CallbackQueryHandler(
                    check_membership, pattern=r"^driver:check_membership$",
                ),
                MessageHandler(~filters.COMMAND, check_membership),
            ],
            NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_name),
                MessageHandler(~filters.COMMAND, name_wrong),
            ],
            PHONE: [
                MessageHandler(filters.CONTACT, get_phone),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone),
                MessageHandler(~filters.COMMAND, phone_wrong),
            ],
            WARN_DOCS: [
                MessageHandler(filters.Regex(r"^(?:✅ )?Davom etish$"), warn_docs),
                MessageHandler(~filters.COMMAND, warn_wrong),
            ],
            PASSPORT_FRONT: _photo_state(get_passport_front, _wrong_pf),
            PASSPORT_BACK: _photo_state(get_passport_back, _wrong_pb),
            LICENSE_FRONT: _photo_state(get_license_front, _wrong_lf),
            LICENSE_BACK: _photo_state(get_license_back, _wrong_lb),
            TECH_FRONT: _photo_state(get_tech_front, _wrong_tf),
            TECH_BACK: _photo_state(get_tech_back, _wrong_tb),
            SELFIE: _photo_state(get_selfie, _wrong_se),
            LITSENZIYA: _photo_state(get_litsenziya, litsenziya_wrong),
            CAR_PHOTOS: _photo_state(get_car_photos, car_photos_wrong),
            CAR_PLATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_car_plate, block=False),
                MessageHandler(~filters.COMMAND, car_plate_wrong),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", cancel)],
        allow_reentry=True,
    )
