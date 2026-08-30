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
    raise ValueError("Не найден VK_TOKEN")

if not KIE_API_KEY:
    raise ValueError("Не найден KIE_API_KEY")


# =========================================================
# 2. КАТАЛОГ МАТЕРИАЛОВ
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
# 4. KIE
# =========================================================

KIE_URL = (
    "https://api.kie.ai/"
    "gemini-3-7-flash-openai/v1/chat/completions"
)


# =========================================================
# 5. АКТИВИРОВАННЫЕ ПОЛЬЗОВАТЕЛИ
# =========================================================

# Здесь хранятся пользователи,
# которые отправили точное слово BUNNY
active_users = set()


# =========================================================
# 6. ПРЕДВАРИТЕЛЬНЫЙ ПОИСК
# =========================================================

def find_candidates(user_text):

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

    matches = [
        material
        for score, material in scored
        if score > 0
    ]

    if matches:
        return matches[:5]

    return MATERIALS[:10]


# =========================================================
# 7. AI-ПОИСК
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

Материалы из каталога:
{catalog_text}

Сформируй короткий ответ на русском языке.

Правила:
- используй ТОЛЬКО материалы из предоставленного списка;
- ничего не придумывай;
- не придумывай ссылки;
- выбери максимум 3 самых подходящих материала;
- укажи название, кратко что это и ссылку;
- если подходящего материала нет, честно скажи об этом;
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

    choices = result.get("choices")

    if choices:

        message = choices[0].get("message", {})
        content = message.get("content")

        if content:
            return content.strip()

    raise Exception("ИИ вернул ответ без текста")


# =========================================================
# 8. ОТПРАВКА В VK
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
# 9. ОСНОВНОЙ ЦИКЛ
# =========================================================

print("🐰 ChattyBunny Materials Finder запущен")


while True:

    try:

        longpoll = create_longpoll()

        for event in longpoll.listen():

            if event.type != VkBotEventType.MESSAGE_NEW:
                continue

            message = event.object.message

            # Не реагируем на собственные сообщения
            if message.get("out") == 1:
                continue

            peer_id = message["peer_id"]
            text = message["text"].strip()

            if not text:
                continue


            # =================================================
            # ТОЧНАЯ АКТИВАЦИЯ
            # =================================================

            if text == "BUNNY":

                active_users.add(peer_id)

                send_message(
                    peer_id,
                    (
                        "🐰 Поиск материалов ChattyBunny включён!\n\n"
                        "Напишите, что вы ищете.\n"
                        "Например:\n"
                        "• Academy Stars 1 Unit 2\n"
                        "• Go Getter 2 Unit 4\n"
                        "• phonics ch\n"
                        "• natural disasters"
                    )
                )

                print(
                    f"Пользователь {peer_id} активировал BUNNY"
                )

                continue


            # =================================================
            # ЕСЛИ BUNNY НЕ АКТИВИРОВАН — МОЛЧИМ
            # =================================================

            if peer_id not in active_users:
                continue


            # =================================================
            # ПОЛУЧИЛИ ОДИН ПОИСКОВЫЙ ЗАПРОС
            # =================================================

            # Сразу выключаем режим поиска.
            # Поэтому любые дальнейшие сообщения бот игнорирует,
            # пока человек снова не напишет BUNNY.

            active_users.discard(peer_id)

            print()
            print("Поисковый запрос:", text)


            try:

                answer = get_ai_answer(text)

                print("AI успешно ответил")


            except requests.exceptions.ReadTimeout:

                print(
                    "KIE не успел ответить за 60 секунд"
                )

                answer = (
                    "Поиск занял слишком много времени. "
                    "Попробуйте ещё раз: сначала отправьте BUNNY."
                )


            except Exception as error:

                print(
                    "Ошибка AI:",
                    repr(error)
                )

                answer = (
                    "Сейчас не получилось выполнить поиск. "
                    "Чтобы попробовать ещё раз, отправьте BUNNY."
                )


            send_message(
                peer_id,
                answer
            )


    except Exception as error:

        print(
            "Ошибка VK:",
            repr(error)
        )

        print(
            "Переподключаюсь через 5 секунд..."
        )

        time.sleep(5)