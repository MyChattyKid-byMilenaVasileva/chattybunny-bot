import os
import json
import random
import time

import requests
import vk_api

from dotenv import load_dotenv
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType


# =========================================================
# 1. НАСТРОЙКИ
# =========================================================

load_dotenv()

VK_TOKEN = os.getenv("VK_TOKEN")
KIE_API_KEY = os.getenv("KIE_API_KEY")

if not VK_TOKEN:
    raise ValueError("Не найден VK_TOKEN в файле .env")

if not KIE_API_KEY:
    raise ValueError("Не найден KIE_API_KEY в файле .env")


# =========================================================
# 2. ЗАГРУЖАЕМ КАТАЛОГ МАТЕРИАЛОВ
# =========================================================

with open("materials.json", "r", encoding="utf-8") as file:
    MATERIALS = json.load(file)


# =========================================================
# 3. ПОДКЛЮЧЕНИЕ К VK
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
# 5. ПРЕДВАРИТЕЛЬНЫЙ ПОИСК ПО КАТАЛОГУ
# =========================================================

def find_candidates(user_text):
    """
    Сначала Python сам находит наиболее похожие позиции.
    ИИ получает уже небольшой список, поэтому отвечает быстрее.
    """

    words = (
        user_text
        .lower()
        .replace(",", " ")
        .replace(".", " ")
        .replace("-", " ")
        .split()
    )

    scored = []

    for material in MATERIALS:

        searchable_text = " ".join([
            str(material.get("title", "")),
            str(material.get("topic", "")),
            str(material.get("course", "")),
            str(material.get("unit", "")),
            str(material.get("age", "")),
            str(material.get("type", "")),
            str(material.get("description", ""))
        ]).lower()

        score = 0

        for word in words:
            if len(word) >= 3 and word in searchable_text:
                score += 1

        scored.append((score, material))

    scored.sort(
        key=lambda item: item[0],
        reverse=True
    )

    # Если нашли совпадения — передаём ИИ лучшие.
    matches = [
        material
        for score, material in scored
        if score > 0
    ]

    if matches:
        return matches[:5]

    # Если запрос слишком общий — небольшой кусок каталога.
    return MATERIALS[:7]


# =========================================================
# 6. ЗАПРОС К ИИ
# =========================================================

def get_ai_answer(user_text):

    candidates = find_candidates(user_text)

    catalog_text = json.dumps(
        candidates,
        ensure_ascii=False
    )

    prompt = f"""
Ты — помощник по поиску учебных материалов
сообщества ChattyBunny.

Запрос пользователя:
{user_text}

Вот подходящие позиции из каталога:
{catalog_text}

Сформируй короткий ответ на русском языке.

Правила:
- используй ТОЛЬКО материалы из этого списка;
- ничего не придумывай;
- не придумывай ссылки;
- выбери максимум 3 материала;
- укажи название, коротко зачем подходит и ссылку;
- если ничего подходящего нет, честно скажи об этом;
- не пиши длинное вступление.
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

    print("KIE status:", response.status_code)

    if response.status_code != 200:
        print("KIE error:", response.text)

        raise Exception(
            f"KIE API вернул код {response.status_code}"
        )

    result = response.json()

    # OpenAI-compatible ответ
    choices = result.get("choices")

    if choices:
        message = choices[0].get("message", {})
        content = message.get("content")

        if content:
            return content.strip()

    # Запасной вариант, если KIE вернёт Gemini-формат
    candidates_response = result.get("candidates")

    if candidates_response:
        content = candidates_response[0].get(
            "content",
            {}
        )

        parts = content.get("parts", [])

        texts = []

        for part in parts:
            text = part.get("text")

            if text:
                texts.append(text)

        if texts:
            return "\n".join(texts).strip()

    print("Неожиданный ответ KIE:", result)

    raise Exception(
        "ИИ вернул ответ без текста"
    )


# =========================================================
# 7. ОТПРАВКА СООБЩЕНИЯ В VK
# =========================================================

def send_message(peer_id, text):

    vk.messages.send(
        peer_id=peer_id,
        random_id=random.randint(
            1,
            2_000_000_000
        ),
        message=text
    )


# =========================================================
# 8. ОСНОВНОЙ ЦИКЛ БОТА
# =========================================================

print("🐰 ChattyBunny Materials Finder запущен")


while True:

    try:

        longpoll = create_longpoll()

        for event in longpoll.listen():

            if event.type != VkBotEventType.MESSAGE_NEW:
                continue

            message = event.object.message

            # Не реагируем на собственные исходящие сообщения
            if message.get("out") == 1:
                continue

            peer_id = message["peer_id"]
            text = message["text"].strip()

            if not text:
                continue

            print()
            print("Запрос:", text)

            try:

                answer = get_ai_answer(text)

                print("AI успешно ответил")
                print("Ответ:", answer)

            except requests.exceptions.ReadTimeout:

                print(
                    "KIE не успел ответить за 60 секунд"
                )

                answer = (
                    "Поиск занял слишком много времени. "
                    "Попробуйте повторить запрос."
                )

            except requests.exceptions.RequestException as error:

                print(
                    "Ошибка соединения с KIE:",
                    repr(error)
                )

                answer = (
                    "Сейчас сервис поиска временно недоступен. "
                    "Попробуйте ещё раз чуть позже."
                )

            except Exception as error:

                print(
                    "Ошибка AI:",
                    repr(error)
                )

                answer = (
                    "Сейчас не получилось выполнить поиск. "
                    "Попробуйте ещё раз чуть позже."
                )

            try:

                send_message(
                    peer_id,
                    answer
                )

            except Exception as error:

                print(
                    "Ошибка отправки в VK:",
                    repr(error)
                )


    except requests.exceptions.ReadTimeout:

        # VK Long Poll иногда сам обрывает соединение.
        # Бот просто подключится заново.
        print(
            "VK Long Poll timeout. Переподключаюсь..."
        )

        time.sleep(2)


    except requests.exceptions.RequestException as error:

        print(
            "Ошибка соединения с VK:",
            repr(error)
        )

        print(
            "Повторное подключение через 5 секунд..."
        )

        time.sleep(5)


    except Exception as error:

        print(
            "Ошибка VK Long Poll:",
            repr(error)
        )

        print(
            "Повторное подключение через 5 секунд..."
        )

        time.sleep(5)