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


# ID сообщества ChattyBunny
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

# Пользователь попадает сюда только после ТОЧНОГО сообщения BUNNY.
# После одного поискового запроса он автоматически удаляется.
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
# ОТПРАВКА СООБЩЕНИЯ
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
# PAYLOAD КНОПОК
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
# РАЗБОР ОТВЕТА KIE
# =========================================================

def parse_kie_answer(result):

    # -----------------------------------------------------
    # Вариант 1:
    # OpenAI-совместимый ответ
    # choices[0].message.content
    # -----------------------------------------------------

    choices = result.get("choices")

    if choices:

        content = (
            choices[0]
            .get("message", {})
            .get("content", "")
        )

        if isinstance(content, str) and content.strip():
            return content.strip()

        # Иногда content может быть массивом частей
        if isinstance(content, list):

            texts = []

            for part in content:

                if isinstance(part, dict):

                    text = part.get("text")

                    if text:
                        texts.append(str(text))

            if texts:
                return "\n".join(texts).strip()


    # -----------------------------------------------------
    # Вариант 2:
    # Gemini-формат
    # candidates[0].content.parts[].text
    # -----------------------------------------------------

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
                    texts.append(str(text))

        if texts:
            return "\n".join(texts).strip()


    # -----------------------------------------------------
    # Вариант 3:
    # Некоторые API кладут текст прямо в поле response
    # -----------------------------------------------------

    response_text = result.get("response")

    if isinstance(response_text, str) and response_text.strip():
        return response_text.strip()


    # -----------------------------------------------------
    # Вариант 4:
    # Иногда встречается output_text
    # -----------------------------------------------------

    output_text = result.get("output_text")

    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()


    # -----------------------------------------------------
    # Если формат снова окажется новым —
    # выводим реальный JSON в терминал.
    # -----------------------------------------------------

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
# AI-ПОИСК
# =========================================================

def get_ai_answer(user_text):

    candidates = find_candidates(
        user_text
    )

    if not candidates:

        return (
            "🐰 Точного совпадения пока нет, "
            "и близких материалов тоже не нашлось.\n\n"
            "Попробуйте уточнить учебник, unit "
            "или написать тему немного иначе."
        )

    catalog_text = json.dumps(
        candidates,
        ensure_ascii=False
    )

    prompt = f"""
Ты — поисковый помощник ChattyBunny.

Пользователь ищет:
{user_text}

Вот реальные материалы-кандидаты из каталога:
{catalog_text}

ТВОЯ ЗАДАЧА:
найти самые подходящие материалы для запроса пользователя.

ПРАВИЛА:

1. Используй только материалы из списка выше.
2. Ничего не придумывай.
3. Не придумывай ссылки.
4. Максимум 3 результата.
5. Отвечай по-русски.
6. Не используй Markdown.
7. Не используй звёздочки.
8. Стиль короткий, понятный и дружелюбный.
9. Максимум 1–2 эмодзи.
10. Не используй канцелярские фразы.
11. Не пиши длинное вступление.
12. Если точного совпадения нет, попробуй найти действительно близкие материалы.
13. Не предлагай случайные материалы только ради того, чтобы что-то показать.

ЕСЛИ ЕСТЬ ХОРОШИЕ СОВПАДЕНИЯ:

🔎 Вот что нашлось по запросу [запрос]:

1. Название
Короткое описание.
Ссылка: ...

2. Название
Короткое описание.
Ссылка: ...

ЕСЛИ ТОЧНОГО СОВПАДЕНИЯ НЕТ,
НО ЕСТЬ ДЕЙСТВИТЕЛЬНО БЛИЗКИЕ МАТЕРИАЛЫ:

🐰 Точного совпадения по [запрос] не нашлось.
Но вот несколько близких материалов:

1. Название
Короткое описание.
Ссылка: ...

2. Название
Короткое описание.
Ссылка: ...

ЕСЛИ НИЧЕГО ПОДХОДЯЩЕГО НЕТ:

🐰 Точного совпадения пока нет, и близких материалов тоже не нашлось.

Попробуйте уточнить учебник, unit или написать тему немного иначе.

НЕ добавляй в конце предложение про BUNNY.
Программа добавит его автоматически.
"""

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
        timeout=60
    )

    print(
        "KIE status:",
        response.status_code,
        flush=True
    )

    if response.status_code != 200:

        print(
            "KIE error:",
            response.text,
            flush=True
        )

        raise Exception(
            f"KIE API: {response.status_code}"
        )

    result = response.json()

    answer = parse_kie_answer(
        result
    )

    if not answer:
        raise Exception(
            "KIE вернул пустой текст"
        )

    # VK не рендерит Markdown-звёздочки нормально.
    answer = answer.replace(
        "**",
        ""
    )

    return answer


# =========================================================
# АКТИВАЦИЯ ПОИСКА
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

            # Бот не реагирует на собственные сообщения сообщества.
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
            # КНОПКА: НОВЫЙ ПОИСК
            # =================================================

            if action == "new_search":

                print(
                    f"{peer_id}: новый поиск",
                    flush=True
                )

                activate_search(
                    peer_id
                )

                continue


            # =================================================
            # КНОПКА: НАПИСАТЬ ПРЕПОДАВАТЕЛЮ
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

                print(
                    f"{peer_id}: переход к преподавателю",
                    flush=True
                )

                continue


            # =================================================
            # АКТИВАТОР
            #
            # Работает исключительно точное сообщение:
            #
            # BUNNY
            #
            # bunny
            # Bunny
            # BUNNY!
            # Привет BUNNY
            #
            # НЕ активируют поиск.
            # =================================================

            if text == "BUNNY":

                print(
                    f"{peer_id}: BUNNY",
                    flush=True
                )

                activate_search(
                    peer_id
                )

                continue


            # =================================================
            # ЕСЛИ ПОИСК НЕ АКТИВИРОВАН —
            # БОТ ПОЛНОСТЬЮ МОЛЧИТ
            # =================================================

            if peer_id not in active_users:
                continue


            # =================================================
            # ОДИН ПОИСКОВЫЙ ЗАПРОС
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


            except requests.exceptions.ReadTimeout:

                print(
                    "KIE timeout",
                    flush=True
                )

                answer = (
                    "🐰 Поиск занял слишком много времени.\n\n"
                    "Попробуйте ещё раз чуть позже."
                )


            except Exception as error:

                print(
                    "Ошибка AI:",
                    repr(error),
                    flush=True
                )

                answer = (
                    "🐰 Сейчас не получилось выполнить поиск.\n\n"
                    "Попробуйте ещё раз чуть позже."
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