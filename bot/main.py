import logging

from telegram import Update
from telegram.error import Conflict, InvalidToken, NetworkError, TimedOut
from telegram.ext import (
    Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler,
    ContextTypes, ConversationHandler, MessageHandler, PicklePersistence, filters,
)
from telegram.request import HTTPXRequest

from bot.config import BOT_TOKEN
from bot.brand_cleanup import remove_legacy_brand_keyboards
from bot.handlers.brand import build_brand_conversation
from bot.handlers.driver import build_driver_conversation
from bot.handlers.operator import on_user_reply_message, register_operator_handlers
from bot.handlers.start import (
    MENU_BRAND, MENU_CONTACT, MENU_DRIVER, MENU_OFFICE, MENU_REGION,
    build_contact_handler, build_office_handler, build_region_handler, cancel,
    show_menu, start,
)
from bot.warmup import warmup_templates

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

MENU_ACTIONS = {MENU_DRIVER, MENU_BRAND, MENU_CONTACT, MENU_OFFICE, MENU_REGION}


async def intercept_pending_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A reply to an operator must not also become an application field."""
    if not update.effective_user or not update.message:
        return
    pending = context.bot_data.get("pending_user_replies", {})
    user_id = update.effective_user.id
    if user_id not in pending:
        return
    text = update.message.text or ""
    command = text.split(maxsplit=1)[0].split("@")[0] if text else ""
    if text in MENU_ACTIONS or command in ("/start", "/cancel"):
        pending.pop(user_id, None)
        return
    await on_user_reply_message(update, context)
    raise ApplicationHandlerStop


def build_application_conversation() -> ConversationHandler:
    """One state machine prevents driver and brand forms running concurrently."""
    driver = build_driver_conversation()
    brand = build_brand_conversation()
    if set(driver.states) & set(brand.states):
        raise ValueError("Driver and brand conversation states must not overlap")
    navigation = [
        CommandHandler("start", start, filters.ChatType.PRIVATE),
        CommandHandler("cancel", cancel, filters.ChatType.PRIVATE),
        MessageHandler(filters.ChatType.PRIVATE & filters.Regex(f"^{MENU_REGION}$"), start),
        build_contact_handler(),
        build_office_handler(),
    ]
    return ConversationHandler(
        entry_points=navigation + driver.entry_points + brand.entry_points,
        states={**driver.states, **brand.states},
        fallbacks=navigation,
        allow_reentry=True,
    )


async def stale_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer(
        "Bu tugma eskirgan. Menyudan ariza turini qayta tanlang.", show_alert=True,
    )


def register_handlers(app) -> None:
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE, intercept_pending_reply), group=-1)
    app.add_handler(build_region_handler())
    app.add_handler(build_application_conversation())
    register_operator_handlers(app)
    app.add_handler(CallbackQueryHandler(
        stale_subscription, pattern=r"^(?:driver|brand|spectre):check_membership$",
    ))
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, show_menu,
    ))


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler.

    - Conflict (another bot instance polling): warn once, do not spam tracebacks.
    - Network/Timeout: warn briefly, the polling loop will recover automatically.
    - Other errors: full traceback + a friendly message to the user if possible.
    """
    err = context.error

    if isinstance(err, Conflict):
        logger.warning(
            "Conflict: another instance is polling with the same bot token. "
            "Check Render: stop duplicate services or wait for the old container "
            "to drain."
        )
        return

    if isinstance(err, (NetworkError, TimedOut)):
        logger.warning("Network/Timeout while polling: %s", err)
        return

    logger.error("Unhandled error: %s", err, exc_info=err)
    try:
        if isinstance(update, Update) and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(
                    "⚠️ Texnik xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring "
                    "yoki /start bosing."
                ),
            )
    except Exception:
        pass


async def post_init(application) -> None:
    await remove_legacy_brand_keyboards(application)
    await warmup_templates(application)


def main() -> None:
    request = HTTPXRequest(
        connection_pool_size=20,
        connect_timeout=30.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=10.0,
    )
    get_updates_request = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=30.0,
        read_timeout=40.0,
        write_timeout=40.0,
    )

    import os
    persist_path = os.path.join(os.environ.get("PERSIST_DIR", "."), "bot_persistence.pkl")
    persistence = PicklePersistence(filepath=persist_path)

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .persistence(persistence)
        .request(request)
        .get_updates_request(get_updates_request)
        .post_init(post_init)
        .build()
    )

    register_handlers(app)
    app.add_error_handler(on_error)

    logger.info("🚖 WB TAXI HUMO bot ishga tushdi...")
    try:
        app.run_polling(allowed_updates=["message", "callback_query"])
    except InvalidToken:
        # PTB's exception text includes the credential: never print its traceback.
        logger.error(
            "Telegram tokenni qabul qilmadi. TELEGRAM_BOT_TOKEN ni xavfsiz "
            "sozlamada yangilang; tokenni chatga yoki logga yozmang."
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
