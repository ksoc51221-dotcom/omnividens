"""Bot token lookup, using the shared config (~/.reelay/config.toml)."""

from reelay.core.config import config_path, load_config


def get_bot_token() -> str:
    """The Telegram bot token from the shared config, or a friendly error."""
    token = load_config().get("telegram_bot_token", "")
    if not isinstance(token, str) or not token:
        raise RuntimeError(
            f'No bot token found. Add a line telegram_bot_token = "<your token>" '
            f"to {config_path()} (get a token from @BotFather)."
        )
    return token
