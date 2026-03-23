"""
One-time script to obtain a Telethon StringSession.
Run once, copy the output into TG_SESSION= in your .env file.
"""
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

API_ID   = int(input("Enter api_id: "))
API_HASH = input("Enter api_hash: ").strip()

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    client.start()
    print("\n=== Your StringSession (paste into TG_SESSION in .env) ===")
    print(client.session.save())
    print("=" * 60)
