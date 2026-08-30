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
# 1. НАСТРОЙКИ
# =========================================================

load_dotenv()

VK_TOKEN = os.getenv("VK_TOKEN")
KIE_API_KEY = os.getenv("KIE_API_KEY")

if not VK_TOKEN:
    raise ValueError("Не найден VK_TOKEN")

if not KIE_API_KEY:
    raise ValueError("Не найден KIE_API_KEY")


# =========================================================
# 2. КАТАЛОГ
# =========================================================

with open("materials.json", "r", encoding="utf-8") as file:
    MATERIALS = json.load(file)


# =========================================================
# 3. VK
# =========================================================

vk_session = vk_api.VkApi(token=VK_TOKEN)
vk = vk_session.get_api()

group = vk.groups.getById()[0]
GROUP_ID = group["id"]


def create_longpoll():
    return VkBotLongPoll(vk_session, GROUP_ID)


# =========================================================
# 4. KIE AI
# =========================================================

KIE_URL = (
    "https://api.kie.ai/"
    "gemini-3-7-flash-openai/v1/chat/completions"
)


# =========================================================
# 5. СОСТОЯНИЕ
# =========================================================

# Здесь находятся пользователи,
# которые уже написали точное BUNNY
# и сейчас могут отправить ОДИН поисковый запрос.
active_users = set()


# =========================================================
# 6. КНОПКИ
# =========================================================

def result_keyboard():

    keyboard = VkKeyboard(
        one_time=True,
        inline=False
    )

    keyboard.add_button(
        "🔎 Новый поиск",
        color=VkKeyboardColor.PRIMARY,
        payload={
            "action": "new_search"
        }
    )

    keyboard.add_line()

    keyboard.add_button(
        "💬 Написать преподавателю",
        color=VkKeyboardColor.SECONDARY,
        payload={
            "action": "teacher"
        }
    )

    return keyboard.get_keyboard()


def empty_keyboard():
    return VkKeyboard.get_empty_keyboard()


# =========================================================
# 7. ОТПРАВКА СООБЩЕНИЯ
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
# 8. PAYLOAD КНОПОК
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
# 9. ПОИСК КАНДИДАТОВ
# =========================================================

def normalize(text):

    text = str(text).lower()

    text = re.sub(
        r"[^\w\s]",
        " ",
        text
    )

    return " ".join(text.split())


def find_candidates(user_text):

    query = normalize(user_text)

    query_words = set(
        word
        for word in query.split()
        if len(word) >= 2
    )

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

        similarity = SequenceMatcher(
            None,
            query,
            " ".join([
                course,
                unit,
                topic,
                title
            ])
        ).ratio()

        score = (
            overlap * 3
            + similarity
        )

        if overlap > 0 or similarity >= 0.22:
            scored.append(
                (score, material)
            )

    scored.sort(
        key=lambda item: item[0],
        reverse=True
    )

    return [
        material
        for score, material in scored[:10]
    ]


# =========================================================
# 10. ОТВЕТ AI
# =========================================================

def get_ai_answer(user_text):

    candidates = find_candidates(
        user_text
    )

    # Если вообще ничего даже приблизительно
    # не нашлось — не заставляем ИИ придумывать.
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

Запрос пользователя:
{user_text}

Вот материалы-кандидаты из реального каталога:
{catalog_text}

Твоя задача — помочь найти подходящий материал.

ВАЖНЫЕ ПРАВИЛА:

1. Используй ТОЛЬКО материалы из списка.
2. Никогда ничего не придумывай.
3. Никогда не придумывай ссылки.
4. Максимум 3 материала.
5. Пиши по-русски.
6. Пиши коротко, понятно и дружелюбно.
7. НЕ используй Markdown.
8. НЕ используй звёздочки ** вообще.
9. Не пиши длинные вступления.
10. Используй максимум 1–2 уместных эмодзи.

Если есть хорошие совпадения, начни примерно так:

🔎 Вот что нашлось по запросу ...

Дальше для каждого материала:
номер
название
одно короткое предложение
ссылка

Если ТОЧНОГО совпадения нет,
но есть реально близкие материалы, напиши:

🐰 Точного совпадения по ... не нашлось.
Но вот несколько близких материалов:

И покажи только действительно близкие варианты.

Если предложенные кандидаты на самом деле
не имеют отношения к запросу, честно напиши:

🐰 Точного совпадения пока нет,
и близких материалов тоже не нашлось.
Попробуйте уточнить учебник, unit
или написать тему немного иначе.

НЕ добавляй фразу про BUNNY в конце.
Она будет добавлена программой автоматически.
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
        response.status_code
    )

    if response.status_code != 200:

        print(
            "KIE error:",
            response.text
        )

        raise Exception(
            f"KIE API вернул код "
            f"{response.status_code}"
        )

    result = response.json()

    choices = result.get(
        "choices",
        []
    )

    if not choices:
        raise Exception(
            "ИИ вернул ответ без choices"
        )

    answer = (
        choices[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )

    if not answer:
        raise Exception(
            "ИИ вернул пустой ответ"
        )

    # На всякий случай убираем markdown-звёздочки,
    # даже если модель вдруг их добавила.
    answer = answer.replace("**", "")

    return answer


# =========================================================
# 11. АКТИВАЦИЯ ПОИСКА
# =========================================================

def activate_search(peer_id):

    active_users.add(peer_id)

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
# 12. ОСНОВНОЙ ЦИКЛ
# =========================================================

print(
    "🐰 ChattyBunny Materials Finder запущен"
)


while True:

    try:

        longpoll = create_longpoll()

        for event in longpoll.listen():

            if (
                event.type
                != VkBotEventType.MESSAGE_NEW
            ):
                continue

            message = event.object.message

            # Игнорируем собственные сообщения сообщества
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


            # =============================================
            # КНОПКА: НОВЫЙ ПОИСК
            # =============================================

            if action == "new_search":

                activate_search(
                    peer_id
                )

                print(
                    f"{peer_id}: новый поиск через кнопку"
                )

                continue


            # =============================================
            # КНОПКА: НАПИСАТЬ ПРЕПОДАВАТЕЛЮ
            # =============================================

            if action == "teacher":

                active_users.discard(
                    peer_id
                )

                send_message(
                    peer_id,
                    (
                        "💬 Просто напишите сообщение сюда — "
                        "преподаватель его увидит и ответит."
                    ),
                    keyboard=empty_keyboard()
                )

                print(
                    f"{peer_id}: переход к преподавателю"
                )

                continue


            # =============================================
            # ТОЛЬКО ТОЧНОЕ BUNNY
            # =============================================

            if text == "BUNNY":

                activate_search(
                    peer_id
                )

                print(
                    f"{peer_id}: активировал BUNNY"
                )

                continue


            # =============================================
            # ВСЕ ОСТАЛЬНЫЕ СООБЩЕНИЯ БОТ ИГНОРИРУЕТ,
            # ЕСЛИ ПОИСК НЕ БЫЛ АКТИВИРОВАН
            # =============================================

            if peer_id not in active_users:
                continue


            # =============================================
            # ОДИН ПОИСКОВЫЙ ЗАПРОС
            # =============================================

            active_users.discard(
                peer_id
            )

            if not text:
                continue

            print()
            print(
                "Поисковый запрос:",
                text
            )


            try:

                answer = get_ai_answer(
                    text
                )

                print(
                    "AI успешно ответил"
                )


            except requests.exceptions.ReadTimeout:

                print(
                    "KIE timeout"
                )

                answer = (
                    "🐰 Поиск занял слишком много времени.\n\n"
                    "Попробуйте запустить новый поиск."
                )


            except Exception as error:

                print(
                    "Ошибка AI:",
                    repr(error)
                )

                answer = (
                    "🐰 Сейчас не получилось выполнить поиск.\n\n"
                    "Попробуйте ещё раз чуть позже."
                )


            final_answer = (
                answer
                + "\n\n"
                + "Для нового поиска снова отправьте BUNNY."
            )


            send_message(
                peer_id,
                final_answer,
                keyboard=result_keyboard()
            )


    except Exception as error:

        print(
            "Ошибка соединения с VK:",
            repr(error)
        )

        print(
            "Переподключаюсь через 5 секунд..."
        )

        time.sleep(5)