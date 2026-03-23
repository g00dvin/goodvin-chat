"""
Lists all groups / supergroups the account participates in,
along with their CHAT_ID and, for forum supergroups, all TOPIC_IDs.

Usage:
    python get_chats.py
"""
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import Channel

# GetForumTopicsRequest was added in a specific Telethon build;
# fall back gracefully if it is not available.
try:
    from telethon.tl.functions.channels import GetForumTopicsRequest
    _FORUM_REQUEST_AVAILABLE = True
except ImportError:
    _FORUM_REQUEST_AVAILABLE = False


def _get_topics_via_request(client: TelegramClient, entity: Channel) -> list:
    result = client(GetForumTopicsRequest(
        channel=entity,
        offset_date=0,
        offset_id=0,
        offset_topic=0,
        limit=100,
    ))
    return result.topics


def _get_topics_via_method(client: TelegramClient, entity: Channel) -> list:
    """Fallback: use the high-level helper added in newer Telethon builds."""
    return list(client.get_forum_topics(entity))


def _fetch_topics(client: TelegramClient, entity: Channel) -> list | None:
    """Return list of topic objects or None if unavailable."""
    if _FORUM_REQUEST_AVAILABLE:
        try:
            return _get_topics_via_request(client, entity)
        except Exception:
            pass
    # Try high-level method
    try:
        return _get_topics_via_method(client, entity)
    except Exception:
        return None


API_ID   = int(input("api_id  : "))
API_HASH = input("api_hash: ").strip()
SESSION  = input("TG_SESSION (StringSession string or file name): ").strip()

session = StringSession(SESSION) if len(SESSION) > 50 else SESSION

with TelegramClient(session, API_ID, API_HASH) as client:
    client.start()

    print("\n" + "=" * 65)
    print("  Groups & Supergroups")
    print("=" * 65)

    found = 0
    for dialog in client.iter_dialogs():
        entity = dialog.entity

        # Keep only groups and supergroups (skip channels / private chats)
        if not isinstance(entity, Channel):
            continue
        if entity.broadcast:
            continue

        found += 1
        chat_id = int(f"-100{entity.id}")

        print(f"\n  {'─' * 61}")
        print(f"  Name   : {dialog.name}")
        print(f"  CHAT_ID: {chat_id}")

        is_forum = getattr(entity, "forum", False)
        if not is_forum:
            print("  Topics : не forum-группа — TOPIC_ID не нужен")
            continue

        topics = _fetch_topics(client, entity)
        if topics is None:
            print("  Topics : FORUM группа, но получить список не удалось")
            print(f"           Откройте в браузере: https://web.telegram.org/k/#{chat_id}")
            print("           В адресной строке будет: #<CHAT_ID>_<TOPIC_ID>")
        elif topics:
            print("  Topics :")
            for t in topics:
                print(f"    TOPIC_ID={t.id:<10} {t.title}")
        else:
            print("  Topics : топики не найдены")

    print(f"\n{'=' * 65}")
    print(f"  Итого групп: {found}")
    print(f"{'=' * 65}\n")
