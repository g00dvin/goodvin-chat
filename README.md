# goodvin-chat

Telegram userbot, который отслеживает сообщения в публичном чате и отвечает на них через **GigaChat API** в заданном стиле.

---

## Содержание

- [Архитектура](#архитектура)
- [Шаг 1 — Получить Telegram API credentials](#шаг-1--получить-telegram-api-credentials)
- [Шаг 2 — Получить StringSession](#шаг-2--получить-stringsession)
- [Шаг 3 — Получить CHAT\_ID и TOPIC\_ID](#шаг-3--получить-chat_id-и-topic_id)
- [Шаг 4 — Получить GigaChat credentials](#шаг-4--получить-gigachat-credentials)
- [Шаг 5 — Настройка и запуск](#шаг-5--настройка-и-запуск)
- [Переменные окружения](#переменные-окружения)
- [Структура проекта](#структура-проекта)

---

## Архитектура

```
Telegram chat
      │  новое сообщение
      ▼
message_handler.py
  ├─ фильтр по chat_id / topic_id
  ├─ триггер: ключевые слова / @mention / reply на нас
  ├─ rate limit
  └─ сборка контекста (последние N сообщений)
      │
      ▼
prompt_builder.py  →  gigachat_client.py  →  GigaChat API
                                                  │ ответ
      ◄───────────────────────────────────────────┘
      │
      ▼
  send_message() → Telegram chat
```

---

## Шаг 1 — Получить Telegram API credentials

Userbot работает **от имени вашего аккаунта** (не бота), поэтому нужны личные `api_id` и `api_hash`.

1. Откройте [https://my.telegram.org/apps](https://my.telegram.org/apps) в браузере.
2. Войдите под своим номером телефона.
3. Нажмите **"Create new application"**.
4. Заполните поля:
   - **App title** — любое название, например `my-userbot`
   - **Short name** — латиница без пробелов, например `myuserbot`
   - Platform — `Other`
   - Description — можно оставить пустым
5. Нажмите **"Create application"**.
6. На открывшейся странице скопируйте:
   - **App api_id** → это ваш `TG_API_ID`
   - **App api_hash** → это ваш `TG_API_HASH`

> **Важно.** `api_id` и `api_hash` привязаны к вашему аккаунту. Никому не передавайте эти данные.

---

## Шаг 2 — Получить StringSession

Telethon сохраняет сессию авторизации в файл или в строку (`StringSession`). Строковый вариант удобен для Docker — не нужно монтировать файл.

### Способ А — одноразовый скрипт (рекомендуется)

Выполните **один раз** на любой машине с Python:

```bash
pip install telethon
```

```python
# get_session.py
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

API_ID   = 12345678          # ← вставьте свой api_id
API_HASH = "abcdef1234..."   # ← вставьте свой api_hash

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    print("Ваш StringSession:")
    print(client.session.save())
```

```bash
python get_session.py
```

Скрипт попросит:
1. **Номер телефона** (в международном формате, например `+79161234567`)
2. **Код подтверждения** из Telegram
3. **Пароль двухфакторной аутентификации** (если включён)

После успешного входа в терминале появится длинная строка (~350 символов).
Скопируйте её целиком в `TG_SESSION=` в файле `.env`.

### Способ Б — файловая сессия (локальный запуск без Docker)

Укажите короткое имя (без пробелов):

```env
TG_SESSION=userbot
```

При первом запуске `main.py` Telethon создаст файл `userbot.session` рядом со скриптом и запросит авторизацию в терминале.

---

## Шаг 3 — Получить CHAT\_ID и TOPIC\_ID

### CHAT\_ID

`CHAT_ID` для групп и супергрупп — это **отрицательное число**, например `-1001234567890`.

#### Способ 1 — через мини-скрипт (самый надёжный)

```python
# get_chat_id.py
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

API_ID   = 12345678
API_HASH = "abcdef1234..."
SESSION  = "ВАШ_SESSION_STRING"   # или имя файла

USERNAME = "username_публичной_группы"  # без @

with TelegramClient(StringSession(SESSION), API_ID, API_HASH) as client:
    entity = client.get_entity(USERNAME)
    print("CHAT_ID =", -entity.id if entity.id > 0 else entity.id)
```

> Для супергрупп Telethon возвращает положительный id, а в реальности он отрицательный и начинается с `-100`.
> Скрипт выводит уже готовое значение.

#### Способ 2 — через веб-версию Telegram

1. Откройте [https://web.telegram.org/k/](https://web.telegram.org/k/).
2. Перейдите в нужную группу.
3. В адресной строке браузера будет URL вида:
   ```
   https://web.telegram.org/k/#-1001234567890
   ```
4. Число после `#` (вместе с минусом) — и есть `CHAT_ID`.

#### Способ 3 — через @userinfobot

1. Перешлите **любое сообщение из группы** боту [@userinfobot](https://t.me/userinfobot).
2. Бот ответит объектом, в котором будет поле `Forwarded from chat` с id группы.

---

### TOPIC\_ID (только для Forum-супергрупп)

`TOPIC_ID` — это **id первого сообщения в топике** (оно же служит заголовком топика).

#### Способ 1 — через мини-скрипт

```python
# get_topics.py
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import GetForumTopicsRequest

API_ID   = 12345678
API_HASH = "abcdef1234..."
SESSION  = "ВАШ_SESSION_STRING"
CHAT_ID  = -1001234567890   # ← ваш chat_id

with TelegramClient(StringSession(SESSION), API_ID, API_HASH) as client:
    result = client(GetForumTopicsRequest(
        channel=CHAT_ID,
        offset_date=0,
        offset_id=0,
        offset_topic=0,
        limit=100,
    ))
    for topic in result.topics:
        print(f"id={topic.id:>10}  title={topic.title}")
```

Вывод будет примерно таким:
```
id=         1  title=General
id=      4521  title=Флудилка
id=      8803  title=Вопросы и ответы
```

Значение в столбце `id` и есть `TOPIC_ID`.

#### Способ 2 — через веб-версию Telegram

1. Откройте группу в [web.telegram.org](https://web.telegram.org/k/).
2. Перейдите в нужный топик.
3. URL станет вида:
   ```
   https://web.telegram.org/k/#-1001234567890_4521
   ```
4. Число после `_` — это `TOPIC_ID` (в примере `4521`).

> **General** (первый топик) имеет `TOPIC_ID=1`.
> Если оставить `TOPIC_ID` пустым, бот будет отвечать во **всех** топиках чата.

---

## Шаг 4 — Получить GigaChat credentials

1. Зарегистрируйтесь на [developers.sber.ru/studio](https://developers.sber.ru/studio).
2. Создайте проект и добавьте сервис **GigaChat API**.
3. В разделе **Доступы** скопируйте:
   - `Client ID` → `GIGACHAT_CLIENT_ID`
   - `Client Secret` → `GIGACHAT_CLIENT_SECRET`
4. Выберите тип доступа (`scope`):
   - `GIGACHAT_API_PERS` — физическое лицо
   - `GIGACHAT_API_CORP` — юридическое лицо

---

## Шаг 5 — Настройка и запуск

### 1. Скопировать `.env`

```bash
cp .env.example .env
```

Заполнить все обязательные поля:

```env
TG_API_ID=12345678
TG_API_HASH=abcdef1234567890abcdef1234567890
TG_SESSION=<строка из get_session.py>

CHAT_ID=-1001234567890
TOPIC_ID=4521

TRIGGER_WORDS=привет,помоги,бот
SYSTEM_PROMPT=Ты дерзкий, саркастичный собеседник. Отвечай кратко, с юмором, не более 3 предложений.
MAX_CONTEXT_MESSAGES=10
RATE_LIMIT_SECONDS=5

GIGACHAT_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
GIGACHAT_CLIENT_SECRET=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_MODEL=GigaChat
```

### 2. Запустить через Docker Compose

```bash
docker compose up --build
```

Или в фоне:

```bash
docker compose up --build -d
docker compose logs -f
```

### 3. Запустить локально (без Docker)

```bash
pip install -r requirements.txt
python -m app.main
```

---

## Переменные окружения

| Переменная | Обязательная | Описание |
|---|:---:|---|
| `TG_API_ID` | ✅ | App api_id с my.telegram.org |
| `TG_API_HASH` | ✅ | App api_hash с my.telegram.org |
| `TG_SESSION` | ✅ | StringSession или имя файла сессии |
| `CHAT_ID` | ✅ | Числовой id чата (отрицательный для групп) |
| `TOPIC_ID` | — | Id топика; пусто = все топики |
| `TRIGGER_WORDS` | — | Слова через запятую, запускающие ответ |
| `SYSTEM_PROMPT` | — | Инструкция стиля для GigaChat |
| `MAX_CONTEXT_MESSAGES` | — | Кол-во сообщений контекста (по умолч. 10) |
| `RATE_LIMIT_SECONDS` | — | Минимальная пауза между ответами (по умолч. 5) |
| `GIGACHAT_CLIENT_ID` | ✅ | Client ID из кабинета Sber |
| `GIGACHAT_CLIENT_SECRET` | ✅ | Client Secret из кабинета Sber |
| `GIGACHAT_SCOPE` | — | `GIGACHAT_API_PERS` или `GIGACHAT_API_CORP` |
| `GIGACHAT_MODEL` | — | `GigaChat`, `GigaChat-Plus`, `GigaChat-Pro` |

---

## Структура проекта

```
goodvin-chat/
├── app/
│   ├── __init__.py
│   ├── main.py             # точка входа
│   ├── config.py           # загрузка ENV
│   ├── telegram_client.py  # Telethon клиент + регистрация хендлеров
│   ├── message_handler.py  # логика триггеров, контекста, rate limit
│   ├── gigachat_client.py  # GigaChat API (auth, chat, retry)
│   ├── prompt_builder.py   # сборка prompt с историей диалога
│   └── utils.py            # логирование, обработка сигналов
├── .env.example
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```
