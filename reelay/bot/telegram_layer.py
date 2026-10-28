"""The only file that imports python-telegram-bot. Kept thin on purpose.

Run with `run_bot(bot)`; push reports with `send_reply(...)`.
"""

import logging
import tempfile
from pathlib import Path

from reelay.bot.config import get_bot_token
from reelay.bot.core import Incoming, ReelayBot, Reply, download_failed_reply, too_big_reply


def _markup(reply: Reply):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    if not reply.buttons:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=data) for label, data in reply.buttons]]
    )


async def send_reply(application, user_id: str, reply: Reply) -> None:
    """Send a Reply (text + optional buttons) to a user."""
    await application.bot.send_message(int(user_id), reply.text, reply_markup=_markup(reply))


MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # Telegram's limit for bot downloads


async def handle_update(bot: ReelayBot, update) -> Reply | None:
    """Turn one Telegram message into a reply. Strangers never trigger a download."""
    message = update.effective_message
    user_id = str(update.effective_user.id)
    text = message.text or message.caption or ""
    command = text.split(maxsplit=1)[0].split("@")[0].lower() if text.strip() else ""
    if not bot.pairing.is_paired(user_id) and command not in ("/start", "/pair"):
        return None  # check pairing BEFORE touching any file
    file_path = None
    attachment = message.video or message.video_note or message.document
    if attachment is not None and bot.pairing.is_paired(user_id):
        if (attachment.file_size or 0) > MAX_DOWNLOAD_BYTES:
            return too_big_reply()
        try:
            tg_file = await attachment.get_file()
            # Never use the sender's file name: it could contain "../".
            file_path = Path(tempfile.mkdtemp(prefix="reelay-")) / f"{attachment.file_unique_id}.mp4"
            await tg_file.download_to_drive(file_path)
        except Exception:
            return download_failed_reply()
    forwarded = getattr(message, "forward_origin", None) is not None
    return bot.handle_message(Incoming(user_id, text, file_path, forwarded))


def build_application(bot: ReelayBot, token: str | None = None):
    """Wire Telegram updates to the ReelayBot logic."""
    from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters

    # httpx logs full URLs at INFO, and those contain the bot token. Keep it quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    app = Application.builder().token(token or get_bot_token()).build()

    async def on_message(update, context):
        reply = await handle_update(bot, update)
        if reply:
            await update.effective_message.reply_text(reply.text, reply_markup=_markup(reply))

    async def on_button(update, context):
        query = update.callback_query
        await query.answer()
        reply = bot.handle_button(str(query.from_user.id), query.data or "")
        if reply:
            await query.edit_message_reply_markup(None)  # buttons used up
            await query.message.reply_text(reply.text)

    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT | filters.VIDEO | filters.VIDEO_NOTE
                                   | filters.Document.VIDEO, on_message))
    return app


def run_bot(bot: ReelayBot, token: str | None = None) -> None:
    """Start polling. Blocks until stopped."""
    build_application(bot, token).run_polling()
