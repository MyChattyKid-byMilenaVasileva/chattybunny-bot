import os
import json
import random
import time
import re
from difflib import SequenceMatcher

import requests
import vk_api

from dotenv import load_dotenv
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor


# =========================================================
# НАСТРОЙКИ
# =========================================================

load_dotenv()

VK_TOKEN = os.getenv("VK_TOKEN")
KIE_API_KEY = os.getenv("KIE_API_KEY")

if not VK_TOKEN:
    raise ValueError("Не найден VK_TOKEN")

if not KIE_API_KEY:
    raise ValueError("Не найден KIE_API_KEY")


GROUP_ID = 225157002

KIE_URL = (
    "https://api.kie.ai/"
    "gemini-3-7-flash-openai/v1/chat/completions"
)


# =========================================================
# КАТАЛОГ
# =========================================================

with open("materials.json", "r", encoding="utf-8") as file:
    MATERIALS = json.load(file)


# =========================================================
# VK
# =========================================================

vk_session = vk_api.VkApi(token=VK_TOKEN)
vk = vk_session.get_api()


def create_longpoll():
    return VkBotLongPoll(
        vk_session,
        GROUP_ID
    )


# =========================================================
# СОСТОЯНИЕ
# =========================================================

active_users = set()


# =========================================================
# КНОПКИ
# =========================================================

def result_keyboard():

    keyboard = VkKeyboard(
        one_time=True
    )

    keyboard.add_button(
        "🔎 Новый поиск",
        color=VkKeyboardColor.PRIMARY,
        payload={"action": "new_search"}
    )

    keyboard.add_line()

    keyboard.add_button(
        "💬 Написать преподавателю",
        color=VkKeyboardColor.SECONDARY,
        payload={"action": "teacher"}
    )

    return keyboard.get_keyboard()


def empty_keyboard():
    return VkKeyboard.get_empty_keyboard()


# =========================================================
# ОТПРАВКА
# =========================================================

def send_message(peer_id, text, keyboard=None):

    params = {
        "peer_id": peer_id,
        "random_id": random.randint(
            1,
            2_000_000_000
        ),
        "message": text
    }

    if keyboard is not None:
        params["keyboard"] = keyboard

    vk.messages.send(**params)


# =========================================================
# PAYLOAD
# =========================================================

def get_payload(message):

    payload = message.get("payload")

    if not payload:
        return {}

    if isinstance(payload, dict):
        return payload

    try:
        return json.loads(payload)

    except Exception:
        return {}


# =========================================================
# НОРМАЛИЗАЦИЯ
# =========================================================

def normalize(text):

    text = str(text).lower()

    text = re.sub(
        r"[^\w\s]",
        " ",
        text
    )

    return " ".join(
        text.split()
    )


# =========================================================
# ПОИСК КАНДИДАТОВ
# =========================================================

def find_candidates(user_text):

    query = normalize(user_text)

    query_words = {
        word
        for word in query.split()
        if len(word) >= 2
    }

    scored = []

    for material in MATERIALS:

        title = normalize(
            material.get("title", "")
        )

        course = normalize(
            material.get("course", "")
        )

        unit = normalize(
            material.get("unit", "")
        )

        topic = normalize(
            material.get("topic", "")
        )

        material_type = normalize(
            material.get("type", "")
        )

        description = normalize(
            material.get("description", "")
        )

        searchable = " ".join([
            title,
            course,
            unit,
            topic,
            material_type,
            description
        ])

        material_words = set(
            searchable.split()
        )

        overlap = len(
            query_words & material_words
        )

        important_text = " ".join([
            course,
            unit,
            topic,
            title
        ])

        similarity = SequenceMatcher(
            None,
            query,
            important_text
        ).ratio()

        score = (
            overlap * 4
            + similarity
        )

        if overlap > 0 or similarity >= 0.25:
            scored.append(
                (score, material)
            )

    scored.sort(
        key=lambda x: x[0],
        reverse=True
    )

    return [
        material
        for score, material in scored[:10]
    ]


# =========================================================
# FALLBACK БЕЗ AI
# =========================================================

def fallback_answer(user_text, candidates):

    if not candidates:

        return (
            "🐰 Точного совпадения пока нет, "
            "и близких материалов тоже не нашлось.\n\n"
            "Попробуйте уточнить учебник, unit "
            "или написать тему немного иначе."
        )

    top = candidates[:3]

    lines = [
        f"🐰 Точного совпадения по {user_text} не нашлось.",
        "Но вот несколько близких материалов:",
        ""
    ]

    for index, material in enumerate(top, start=1):

        title = material.get(
            "title",
            "Материал"
        )

        description = material.get(
            "description",
            ""
        )

        url = (
            material.get("url")
            or material.get("link")
            or ""
        )

        lines.append(
            f"{index}. {title}"
        )

        if description:
            lines.append(
                description
            )

        if url:
            lines.append(
                f"Ссылка: {url}"
            )

        lines.append("")

    return "\n".join(lines).strip()


# =========================================================
# РАЗБОР KIE
# =========================================================

def parse_kie_answer(result):

    choices = result.get("choices")

    if choices:

        content = (
            choices[0]
            .get("message", {})
            .get("content", "")
        )

        if isinstance(content, str) and content.strip():
            return content.strip()

        if isinstance(content, list):

            texts = []

            for part in content:

                if isinstance(part, dict):

                    text = part.get("text")

                    if text:
                        texts.append(
                            str(text)
                        )

            if texts:
                return "\n".join(
                    texts
                ).strip()


    candidates = result.get("candidates")

    if candidates:

        parts = (
            candidates[0]
            .get("content", {})
            .get("parts", [])
        )

        texts = []

        for part in parts:

            if isinstance(part, dict):

                text = part.get("text")

                if text:
                    texts.append(
                        str(text)
                    )

        if texts:
            return "\n".join(
                texts
            ).strip()


    response_text = result.get(
        "response"
    )

    if isinstance(
        response_text,
        str
    ) and response_text.strip():

        return response_text.strip()


    output_text = result.get(
        "output_text"
    )

    if isinstance(
        output_text,
        str
    ) and output_text.strip():

        return output_text.strip()


    print(
        "Неожиданный ответ KIE:",
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2
        ),
        flush=True
    )

    raise Exception(
        "Не удалось прочитать текст ответа KIE"
    )


# =========================================================
# ОДНА ПОПЫТКА KIE
# =========================================================

def request_kie(prompt):

    headers = {
        "Authorization": f"Bearer {KIE_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "stream": False,
        "reasoning_effort": "low",
        "include_thoughts": False
    }

    response = requests.post(
        KIE_URL,
        headers=headers,
        json=data,
        timeout=45
    )

    print(
        "KIE status:",
        response.status_code,
        flush=True
    )

    if response.status_code != 200:

        raise Exception(
            f"KIE HTTP {response.status_code}"
        )

    result = response.json()

    # У KIE иногда HTTP 200,
    # но внутри приходит ошибка code 524
    if result.get("code"):

        raise Exception(
            f"KIE code {result.get('code')}: "
            f"{result.get('msg', '')}"
        )

    return parse_kie_answer(
        result
    )


# =========================================================
# AI-ПОИСК С RETRY
# =========================================================

def get_ai_answer(user_text):

    candidates = find_candidates(
        user_text
    )

    if not candidates:

        return fallback_answer(
            user_text,
            candidates
        )

    catalog_text = json.dumps(
        candidates,
        ensure_ascii=False
    )

    prompt = f"""
Ты — поисковый помощник ChattyBunny.

Пользователь ищет:
{user_text}

Вот реальные материалы-кандидаты:
{catalog_text}

Правила:

- используй только материалы из списка;
- ничего не придумывай;
- не придумывай ссылки;
- максимум 3 результата;
- отвечай по-русски;
- не используй Markdown;
- не используй звёздочки;
- стиль короткий и дружелюбный;
- максимум 1–2 эмодзи;
- если точного совпадения нет,
  предложи только действительно близкие материалы;
- не предлагай случайные материалы.

Если есть хорошие совпадения:

🔎 Вот что нашлось по запросу [запрос]:

1. Название
Короткое описание.
Ссылка: ...

Если точного совпадения нет,
но есть близкие материалы:

🐰 Точного совпадения по [запрос] не нашлось.
Но вот несколько близких материалов:

1. Название
Короткое описание.
Ссылка: ...

Если ничего подходящего нет:

🐰 Точного совпадения пока нет,
и близких материалов тоже не нашлось.

Попробуйте уточнить учебник,
unit или тему.

Не добавляй фразу про BUNNY.
"""

    # Максимум 3 попытки
    for attempt in range(1, 4):

        try:

            print(
                f"KIE попытка {attempt}/3",
                flush=True
            )

            answer = request_kie(
                prompt
            )

            answer = answer.replace(
                "**",
                ""
            )

            return answer


        except Exception as error:

            print(
                f"KIE ошибка на попытке {attempt}:",
                repr(error),
                flush=True
            )

            if attempt < 3:

                wait_seconds = attempt * 2

                print(
                    f"Повтор через {wait_seconds} сек.",
                    flush=True
                )

                time.sleep(
                    wait_seconds
                )


    # Все три попытки KIE провалились.
    # Выдаём локальный результат.
    print(
        "KIE недоступен. Использую локальный fallback.",
        flush=True
    )

    return fallback_answer(
        user_text,
        candidates
    )


# =========================================================
# АКТИВАЦИЯ
# =========================================================

def activate_search(peer_id):

    active_users.add(
        peer_id
    )

    send_message(
        peer_id,
        (
            "🐰 Заглядываю в архив ChattyBunny.\n\n"
            "Напишите учебник, unit или тему.\n\n"
            "Например:\n"
            "• Go Getter 2 Unit 4\n"
            "• Academy Stars 1 Unit 10\n"
            "• phonics ch\n"
            "• geographical features"
        ),
        keyboard=empty_keyboard()
    )


# =========================================================
# ЗАПУСК
# =========================================================

print(
    "🐰 ChattyBunny Materials Finder запущен",
    flush=True
)


while True:

    try:

        longpoll = create_longpoll()

        print(
            "VK подключён. Жду сообщения...",
            flush=True
        )

        for event in longpoll.listen():

            if event.type != VkBotEventType.MESSAGE_NEW:
                continue

            message = event.object.message

            if message.get("out") == 1:
                continue

            peer_id = message["peer_id"]

            text = message.get(
                "text",
                ""
            ).strip()

            payload = get_payload(
                message
            )

            action = payload.get(
                "action"
            )


            # =================================================
            # НОВЫЙ ПОИСК
            # =================================================

            if action == "new_search":

                activate_search(
                    peer_id
                )

                continue


            # =================================================
            # ПРЕПОДАВАТЕЛЬ
            # =================================================

            if action == "teacher":

                active_users.discard(
                    peer_id
                )

                send_message(
                    peer_id,
                    (
                        "💬 Просто напишите сообщение сюда — "
                        "преподаватель его увидит и ответит.\n\n"
                        "Хотите поискать ещё? "
                        "Отправьте BUNNY 🐰"
                    ),
                    keyboard=empty_keyboard()
                )

                continue


            # =================================================
            # BUNNY
            # =================================================

            if text == "BUNNY":

                activate_search(
                    peer_id
                )

                continue


            # =================================================
            # ОБЫЧНОЕ СООБЩЕНИЕ
            # =================================================

            if peer_id not in active_users:
                continue


            # =================================================
            # ПОИСК
            # =================================================

            active_users.discard(
                peer_id
            )

            if not text:
                continue

            print(
                f"Поиск: {text}",
                flush=True
            )

            try:

                answer = get_ai_answer(
                    text
                )

            except Exception as error:

                print(
                    "Ошибка поиска:",
                    repr(error),
                    flush=True
                )

                candidates = find_candidates(
                    text
                )

                answer = fallback_answer(
                    text,
                    candidates
                )


            final_answer = (
                answer
                + "\n\n"
                + "Хотите поискать ещё? "
                + "Отправьте BUNNY 🐰"
            )


            send_message(
                peer_id,
                final_answer,
                keyboard=result_keyboard()
            )


    except KeyboardInterrupt:

        print(
            "Бот остановлен.",
            flush=True
        )

        break


    except Exception as error:

        print(
            "Ошибка соединения с VK:",
            type(error).__name__,
            str(error),
            repr(error),
            flush=True
        )

        print(
            "Переподключаюсь через 5 секунд...",
            flush=True
        )

        time.sleep(5)