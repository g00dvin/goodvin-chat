import logging

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from .config import Config
from .message_handler import MessageHandler

logger = logging.getLogger(__name__)

# StringSession strings produced by Telethon are longer than 50 characters
_SESSION_STRING_MIN_LEN = 50


def create_client(config: Config) -> TelegramClient:
    """Create a Telethon client using either a StringSession or a file session."""
    session_value = config.tg_session
    if len(session_value) > _SESSION_STRING_MIN_LEN:
        session = StringSession(session_value)
        logger.info("Using in-memory StringSession")
    else:
        session = session_value  # file-based session
        logger.info("Using file session: %s", session_value)

    return TelegramClient(session, config.tg_api_id, config.tg_api_hash)


async def register_handlers(
    client: TelegramClient,
    handler: MessageHandler,
    config: Config,
) -> None:
    """Attach event handlers to the client."""

    @client.on(events.NewMessage(chats=config.chat_id))
    async def _on_new_message(event: events.NewMessage.Event) -> None:
        try:
            await handler.handle(event.message)
        except Exception as exc:
            logger.error("Unhandled error in message handler: %s", exc, exc_info=True)

    logger.info("Listening for new messages in chat %s", config.chat_id)
