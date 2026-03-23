import logging
import time
from collections import defaultdict, deque
from typing import Optional

from telethon import TelegramClient
from telethon.tl.types import Message, User

from .config import Config
from .gigachat_client import GigaChatClient
from .prompt_builder import ContextMessage, PromptBuilder

logger = logging.getLogger(__name__)


class MessageHandler:
    def __init__(
        self,
        client: TelegramClient,
        gigachat: GigaChatClient,
        config: Config,
    ) -> None:
        self._client = client
        self._gigachat = gigachat
        self._config = config
        self._prompt_builder = PromptBuilder(
            config.system_prompt, config.max_context_messages
        )
        # Dialogue context keyed by "chat_id:topic_id"
        self._context: dict[str, deque[ContextMessage]] = defaultdict(
            lambda: deque(maxlen=config.max_context_messages * 2)
        )
        # Last reply timestamp per key (rate-limiting)
        self._last_reply: dict[str, float] = {}
        self._me: Optional[User] = None

    async def initialize(self) -> None:
        self._me = await self._client.get_me()
        logger.info(
            "Logged in as @%s (id=%s)", self._me.username, self._me.id
        )

    # ------------------------------------------------------------------
    # Topic helpers
    # ------------------------------------------------------------------

    def _get_message_topic_id(self, message: Message) -> Optional[int]:
        """Return the forum topic id the message belongs to, or None."""
        rt = getattr(message, "reply_to", None)
        if rt is None:
            return None
        top_id = getattr(rt, "reply_to_top_id", None)
        if top_id:
            return top_id
        # Topic header message: forum_topic=True, id == reply_to_msg_id
        if getattr(rt, "forum_topic", False):
            return getattr(rt, "reply_to_msg_id", None)
        return None

    def _is_in_target_topic(self, message: Message) -> bool:
        if self._config.topic_id is None:
            return True  # No topic filter configured
        topic_id = self._get_message_topic_id(message)
        # "General" topic (id=1) messages have no reply_to
        if self._config.topic_id == 1 and topic_id is None:
            return True
        return topic_id == self._config.topic_id

    def _context_key(self, chat_id: int, topic_id: Optional[int]) -> str:
        return f"{chat_id}:{topic_id or 0}"

    # ------------------------------------------------------------------
    # Trigger detection
    # ------------------------------------------------------------------

    def _has_trigger_word(self, text: str) -> bool:
        low = text.lower()
        return any(w.lower() in low for w in self._config.trigger_words)

    def _mentions_me(self, text: str) -> bool:
        if not self._me or not self._me.username:
            return False
        return f"@{self._me.username}".lower() in text.lower()

    async def _is_reply_to_me(self, message: Message) -> bool:
        reply_to_id = getattr(message, "reply_to_msg_id", None)
        if not reply_to_id:
            return False
        try:
            replied = await self._client.get_messages(
                message.chat_id, ids=reply_to_id
            )
            return replied is not None and replied.sender_id == self._me.id
        except Exception as exc:
            logger.error("Could not fetch replied-to message: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _can_reply(self, key: str) -> bool:
        elapsed = time.time() - self._last_reply.get(key, 0.0)
        return elapsed >= self._config.rate_limit_seconds

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def handle(self, message: Message) -> None:
        if self._me is None:
            return

        # Ignore our own messages
        if message.sender_id == self._me.id:
            return

        text: str = getattr(message, "text", "") or getattr(message, "message", "") or ""
        if not text.strip():
            return

        topic_id = self._get_message_topic_id(message)

        logger.debug(
            "[MSG] chat=%s topic=%s msg_id=%s sender_id=%s | %.120s",
            message.chat_id, topic_id, message.id, message.sender_id, text,
        )

        # Topic filter
        if not self._is_in_target_topic(message):
            logger.debug("[SKIP] topic %s != target %s", topic_id, self._config.topic_id)
            return

        key = self._context_key(message.chat_id, topic_id)

        # Resolve sender display name
        sender = ""
        raw_sender = getattr(message, "sender", None)
        if raw_sender:
            sender = (
                getattr(raw_sender, "first_name", "")
                or getattr(raw_sender, "username", "")
                or ""
            )

        # Add message to rolling context before deciding to reply
        self._context[key].append(
            ContextMessage(role="user", content=text, sender=sender)
        )

        # Decide whether to respond
        should_reply = False
        trigger_reason = ""

        if self._has_trigger_word(text):
            logger.info("Trigger word matched: %.60s", text)
            should_reply = True
            trigger_reason = "trigger_word"
        elif self._mentions_me(text):
            logger.info("Bot mentioned by username: %.60s", text)
            should_reply = True
            trigger_reason = "mention"
        elif await self._is_reply_to_me(message):
            logger.info("Reply to our message: %.60s", text)
            should_reply = True
            trigger_reason = "reply"

        if not should_reply:
            logger.debug("[SKIP] no trigger: %.80s", text)
            return

        # Rate limit
        if not self._can_reply(key):
            logger.info("Rate-limited, skipping reply for key=%s", key)
            return

        # Build prompt from context (exclude the message we just added,
        # because build() appends it as the current turn)
        context_snapshot = list(self._context[key])[:-1]
        prompt = self._prompt_builder.build(context_snapshot, text, sender)

        logger.debug(
            "[GIGACHAT REQUEST] trigger=%s context_msgs=%d prompt_turns=%d\n%s",
            trigger_reason,
            len(context_snapshot),
            len(prompt),
            "\n".join(
                f"  [{m['role'].upper()}] {m['content'][:200]}"
                for m in prompt
            ),
        )

        # Generate reply
        try:
            reply_text = await self._gigachat.chat(prompt)
        except Exception as exc:
            logger.error("GigaChat generation failed: %s", exc)
            return

        if not reply_text:
            return

        logger.debug("[GIGACHAT RESPONSE] %.300s", reply_text)

        # Send message (reply keeps us in the same topic thread)
        try:
            await self._client.send_message(
                message.chat_id,
                reply_text,
                reply_to=message.id,
            )
            logger.info("Sent reply: %.80s", reply_text)
        except Exception as exc:
            logger.error("Failed to send message: %s", exc)
            return

        # Persist our reply in context and update rate-limit timestamp
        self._context[key].append(
            ContextMessage(role="assistant", content=reply_text)
        )
        self._last_reply[key] = time.time()
