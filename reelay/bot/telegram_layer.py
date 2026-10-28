"""The only file that imports python-telegram-bot. Kept thin on purpose.

Run with `run_bot(bot)`; push reports with `send_reply(...)`.
"""

import tempfile
from pathlib import Path

from reelay.bot.config import get_bot_token
from reelay.bot.core import Incoming, Reply, ReelayBot


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


def build_application(bot: ReelayBot, token: str | None = None):
    """Wire Telegram updates to the ReelayBot logic."""
    from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters

    app = Application.builder().token(token or get_bot_token()).build()

    async def on_message(update, context):
        message = update.effective_message
        file_path = None
        attachment = message.video or message.video_note or message.document
        if attachment is not None:
            tg_file = await attachment.get_file()
            file_path = Path(tempfile.mkdtemp(prefix="reelay-")) / (
                getattr(attachment, "file_name", None) or f"{attachment.file_unique_id}.mp4"
            )
            await tg_file.download_to_drive(file_path)
        incoming = Incoming(
            str(update.effective_user.id), message.text or message.caption or "", file_path
        )
        reply = bot.handle_message(incoming)
        if reply:
            await message.reply_text(reply.text, reply_markup=_markup(reply))

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
