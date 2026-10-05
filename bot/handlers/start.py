import os
from html import escape
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from bot.regions import (
    ANDIJON, TASHKENT, REGION_NAMES, clear_application, get_region, region_name,
)
from bot.subscription import (
    ANDIJON_REQUIRED_CHATS,
    are_subscribed,
    subscription_keyboard,
)

MENU_DRIVER = "📝 Ulanish uchun Ariza"
MENU_BRAND = "🎨 Brend Ariza"
MENU_CONTACT = "📞 Bog'lanish uchun"
MENU_OFFICE = "📍 Ofis manzili"
MENU_REGION = "🔄 Hududni almashtirish"
OFFICE_PHOTO = Path(__file__).resolve().parents[1] / "templates" / "office.png"
TASHKENT_OFFICE_PHOTO = OFFICE_PHOTO.with_name("office_tashkent.png")
OFFICE_CAPTION = os.environ.get("ANDIJON_OFFICE_TEXT", "Andijon ofis manzili hozircha kiritilmagan.")
OFFICE_MAP_URL = os.environ.get("ANDIJON_OFFICE_MAP_URL", "")
OFFICE_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("📍 Xaritada ochish", url=OFFICE_MAP_URL)],
]) if OFFICE_MAP_URL else None
TASHKENT_OFFICE_TEXT = (
    "📍 <b>Toshkent shahri — ofis manzili</b>\n\n"
    "Manzil — Toshkent shahri, Mirzo Ulug‘bek tumani, "
    "Traktorsozlar shaharchasi massivi, 1-mavze, 39-uy\n\n"
    "Mo‘ljal: TTZ diadora\n\n"
    "👇 Manzilni ko‘rish uchun «Xaritada ochish» tugmasini bosing."
)
TASHKENT_OFFICE_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("📍 Xaritada ochish", url="https://yandex.uz/maps/-/CTxxiJ5~")],
])

def main_keyboard(region: str) -> ReplyKeyboardMarkup:
    rows = [[MENU_DRIVER], [MENU_BRAND],
            [MENU_CONTACT], [MENU_OFFICE], [MENU_REGION]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


# Compatibility for imports outside the regional flows.
MAIN_KEYBOARD = main_keyboard(ANDIJON)

CONTACT_TEXT = os.environ.get("ANDIJON_CONTACT_TEXT", "Andijon aloqa ma’lumotlari hozircha kiritilmagan.")
TASHKENT_CONTACT_TEXT = (
    "📞 Aloqa: +998 78 113-80-81\n"
    "✈️ Telegram: @wb_taxi_Humo\n"
    "📢 Telegram kanal: @WB_HUMO_TAXI\n"
    '📸 Instagram: <a href="https://www.instagram.com/humo_wb_taxi/">@humo_wb_taxi</a>'
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_chat.type != "private":
        return ConversationHandler.END
    context.user_data.clear()
    if update.effective_user:
        context.bot_data.get("pending_user_replies", {}).pop(update.effective_user.id, None)
    nonce = uuid4().hex[:10]
    context.user_data["region_choice_nonce"] = nonce
    await update.effective_message.reply_text(
        "• WB HUMO TAXI • xush kelibsiz\n\n"
        "“WB HUMO TAXI” ga ulanish va avtomobilni brendlash uchun shu botga ariza qoldiring!\n\n"
        "Avval ishlamoqchi bo’lgan shahringizni tanlang!",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    await update.effective_message.reply_text(
        "Qaysi hududda ishlamoqchisiz?",
        reply_markup=_region_choices(nonce),
    )
    return ConversationHandler.END


def _region_choices(nonce: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(name, callback_data=f"region:pick:{region}:{nonce}")]
        for region, name in REGION_NAMES.items()
    ])


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    region = get_region(context)
    if not region:
        return await start(update, context)
    details = (
        "📝 <b>Ulanish uchun ariza</b> — haydovchi sifatida ro‘yxatdan o‘tish.\n"
        "🎨 <b>Brend ariza</b> — avtomobilni brendlash uchun murojaat.\n"
        "📞 Aloqa ma’lumotlari va 📍 ofis manzili ham quyidagi menyuda."
    )
    await update.effective_message.reply_text(
        f"📍 <b>{region_name(region)}</b>\n\n"
        "Arizangiz tanlangan shahardagi operatorlarga yuboriladi.\n\n"
        f"{details}\n\nKerakli bo‘limni tanlang:",
        parse_mode="HTML", reply_markup=main_keyboard(region),
    )
    return ConversationHandler.END


async def on_region_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or update.effective_chat.type != "private":
        return ConversationHandler.END
    parts = (query.data or "").split(":")
    if len(parts) == 4 and parts[:3] == ["region", "andijon", "subscription"]:
        nonce = parts[3]
        if (
            get_region(context) != ANDIJON
            or context.user_data.get("andijon_subscription_nonce") != nonce
        ):
            await query.answer(
                "Bu obuna tekshiruvi eskirgan. /start orqali qayta boshlang.",
                show_alert=True,
            )
            return ConversationHandler.END
        joined = await are_subscribed(
            context.bot, update.effective_user.id, ANDIJON_REQUIRED_CHATS
        )
        if joined is None:
            await query.answer(
                "Obunani tekshirib bo‘lmadi. Birozdan keyin qayta urinib ko‘ring.",
                show_alert=True,
            )
            return ConversationHandler.END
        if not joined:
            await query.answer(
                "Avval ko‘rsatilgan kanal va guruhlarga obuna bo‘ling.",
                show_alert=True,
            )
            return ConversationHandler.END
        context.user_data.pop("andijon_subscription_nonce", None)
        await query.answer()
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await show_menu(update, context)
        return ConversationHandler.END
    if (len(parts) != 4 or parts[2] not in REGION_NAMES
            or parts[3] != context.user_data.get("region_choice_nonce")):
        await query.answer("Bu tanlov eskirgan. Hududni almashtirish uchun /start bosing.", show_alert=True)
        return ConversationHandler.END
    _, action, region, nonce = parts
    if action == "pick":
        context.user_data["pending_region"] = region
        await query.answer()
        await query.edit_message_text(
            f"📍 <b>{region_name(region)}</b>\n\n"
            f"Siz <b>{region_name(region)}</b> uchun ariza yubormoqchisiz.\n"
            "Arizangiz faqat shu shahardagi operatorlarga boradi.\n\n"
            "Tanlovingizni tasdiqlaysizmi?",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"region:confirm:{region}:{nonce}")],
                [InlineKeyboardButton("⬅️ Ortga", callback_data=f"region:back:{region}:{nonce}")],
            ]),
        )
    elif action == "back":
        context.user_data.pop("pending_region", None)
        await query.answer()
        await query.edit_message_text(
            "Qaysi hududda ishlamoqchisiz?", reply_markup=_region_choices(nonce),
        )
    elif action == "confirm" and context.user_data.get("pending_region") == region:
        context.user_data.clear()
        context.user_data["region"] = region
        await query.answer()
        if region == ANDIJON:
            context.user_data["andijon_subscription_nonce"] = nonce
            await query.edit_message_text(
                f"✅ {region_name(region)} tanlandi.\n\n"
                "Andijon bo‘yicha ariza yuborish uchun avval quyidagi "
                "obuna manbalariga qo‘shiling:",
                parse_mode="HTML",
                reply_markup=subscription_keyboard(
                    f"region:andijon:subscription:{nonce}",
                    ANDIJON_REQUIRED_CHATS,
                ),
            )
            return ConversationHandler.END
        await query.edit_message_text(f"✅ Tanlandi: {region_name(region)}")
        await show_menu(update, context)
    else:
        await query.answer("Avval hududni qayta tanlang.", show_alert=True)
    return ConversationHandler.END


def build_region_handler() -> CallbackQueryHandler:
    return CallbackQueryHandler(on_region_choice, pattern=r"^region:")


async def require_region(update: Update, context: ContextTypes.DEFAULT_TYPE, allowed_regions=None) -> bool:
    if not update.effective_chat or update.effective_chat.type != "private":
        return False
    region = get_region(context)
    if not region:
        if update.callback_query:
            await update.callback_query.answer()
        await start(update, context)
        return False
    if allowed_regions is not None and region not in allowed_regions:
        await update.effective_message.reply_text(
            "Bu bo‘lim tanlangan shaharda mavjud emas. Quyidagi menyudan tanlang.",
            reply_markup=main_keyboard(region),
        )
        return False
    return True
    return ConversationHandler.END


async def show_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_region(update, context):
        return ConversationHandler.END
    clear_application(context)
    region = get_region(context)
    text = TASHKENT_CONTACT_TEXT if region == TASHKENT else escape(CONTACT_TEXT)
    await update.message.reply_text(
        f"<b>{region_name(region)} — bog‘lanish</b>\n\n" + text,
        parse_mode="HTML",
        reply_markup=main_keyboard(region),
        disable_web_page_preview=True,
    )
    return ConversationHandler.END


def build_contact_handler() -> MessageHandler:
    return MessageHandler(filters.ChatType.PRIVATE & filters.Regex(r"^(?:📞 )?Bog'lanish uchun$"), show_contact)


async def show_office(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_region(update, context):
        return ConversationHandler.END
    clear_application(context)
    if get_region(context) == TASHKENT:
        photo_path, caption, keyboard = (
            TASHKENT_OFFICE_PHOTO, TASHKENT_OFFICE_TEXT, TASHKENT_OFFICE_KEYBOARD,
        )
        cache_key = "tashkent_office_photo"
    else:
        await update.message.reply_text(
            f"📍 <b>Andijon shahri — ofis manzili</b>\n\n{escape(OFFICE_CAPTION)}",
            parse_mode="HTML", reply_markup=OFFICE_KEYBOARD,
        )
        return ConversationHandler.END
    photo_hash = sha256(photo_path.read_bytes()).hexdigest()
    cached_photo = context.bot_data.get(f"{cache_key}_file_id")
    if cached_photo and context.bot_data.get(f"{cache_key}_hash") == photo_hash:
        await update.message.reply_photo(
            photo=cached_photo, caption=caption,
            parse_mode="HTML", reply_markup=keyboard,
        )
    else:
        with photo_path.open("rb") as photo:
            sent = await update.message.reply_photo(
                photo=photo, caption=caption,
                parse_mode="HTML", reply_markup=keyboard,
            )
        if sent.photo:
            context.bot_data[f"{cache_key}_file_id"] = sent.photo[-1].file_id
            context.bot_data[f"{cache_key}_hash"] = photo_hash
    return ConversationHandler.END


def build_office_handler() -> MessageHandler:
    return MessageHandler(filters.ChatType.PRIVATE & filters.Regex(f"^{MENU_OFFICE}$"), show_office)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    clear_application(context)
    if update.effective_user:
        context.bot_data.get("pending_user_replies", {}).pop(update.effective_user.id, None)
    await update.effective_message.reply_text("Ariza to‘ldirish bekor qilindi.")
    return await show_menu(update, context)
