import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock


def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return

    telegram_mod = MagicMock()
    telegram_mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    telegram_mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    telegram_mod.constants.ChatType.GROUP = "group"
    telegram_mod.constants.ChatType.SUPERGROUP = "supergroup"
    telegram_mod.constants.ChatType.CHANNEL = "channel"
    telegram_mod.constants.ChatType.PRIVATE = "private"

    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, telegram_mod)


_ensure_telegram_mock()

from gateway.config import PlatformConfig  # noqa: E402
from gateway.platforms.base import MessageEvent  # noqa: E402
from gateway.platforms.telegram import TelegramAdapter  # noqa: E402


def test_flush_text_batch_does_not_prepend_directive():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))
    adapter.handle_message = AsyncMock()
    adapter._media_batch_delay_seconds = 0

    key = "session:batch"
    adapter._pending_text_batches[key] = MessageEvent(text="review the production rollout checklist")

    asyncio.run(adapter._flush_text_batch(key))

    event = adapter.handle_message.call_args[0][0]
    assert event.text == "review the production rollout checklist"
