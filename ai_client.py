import json
import os
from typing import List
import httpx

import database
from config.profile import CANDIDATE_PROFILE

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

SCORE_PROMPT = """
Ты — AI-рекрутер. Оцени вакансию для кандидата.

ПРОФИЛЬ КАНДИДАТА:
{profile}

ВАКАНСИЯ:
Название: {title}
Компания: {company}
Локация: {area}
Формат: {schedule}
Зарплата: {salary}
Описание:
{description}

Верни ТОЛЬКО JSON, без markdown, без пояснений:
{{
  "score": 85,
  "grade": "A",
  "match_reasons": ["причина 1", "причина 2", "причина 3"],
  "risk_reasons": ["риск 1", "риск 2"],
  "summary": "Краткий вывод 1-2 предложения"
}}

Шкала оценки:
A (80-100) — отличное совпадение, рекомендую
B (60-79)  — хорошее, стоит рассмотреть
C (40-59)  — частичное совпадение
D (0-39)   — плохое совпадение, не подходит
"""

COVER_LETTER_PROMPT = """
Напиши уникальное сопроводительное письмо для отклика на вакансию.

ПРОФИЛЬ КАНДИДАТА:
{profile}

ВАКАНСИЯ:
Компания: {company}
Роль: {title}
Описание: {description}

ТРЕБОВАНИЯ К ПИСЬМУ:
- Длина: 150-200 слов
- Тон: живой, профессиональный, без клише и шаблонов
- Структура: зацепка (почему эта компания/роль) → что конкретно принесу →
  пример из опыта с цифрой → призыв к действию
- НЕ начинать с "Я хочу", "Меня заинтересовала", "Добрый день"
- НЕ перечислять резюме по пунктам
- Обязательно упомянуть: название роли и название компании
- Обращение нейтральное (без имени HR)
- Язык: русский

Верни только текст письма, без заголовков и пояснений.
"""

QUERY_PROMPT = """
Ты ищешь вакансии на HH.ru для конкретного кандидата.

ПРОФИЛЬ КАНДИДАТА:
{profile}

УЖЕ ИСПОЛЬЗОВАННЫЕ ЗАПРОСЫ (не повторять):
{used_queries}

ЛУЧШИЕ НАЙДЕННЫЕ ВАКАНСИИ (grade A/B — для понимания что работает):
{top_titles}

Сгенерируй 6 новых поисковых запросов для HH.ru.
Правила:
- Короткие (2-4 слова), как реальный человек ищет работу на HH
- Разнообразные: часть на русском, часть на английском
- Учитывай что уже искали — ищи с других углов и синонимов
- Фокус на целевых ролях кандидата

Верни ТОЛЬКО JSON, без markdown:
{{"queries": ["запрос 1", "запрос 2", "запрос 3", "запрос 4", "запрос 5", "запрос 6"]}}
"""


def _salary_str(vacancy: dict) -> str:
    sal_from = vacancy.get("salary_from")
    sal_to = vacancy.get("salary_to")
    currency = vacancy.get("currency", "RUR")
    if not sal_from and not sal_to:
        return "не указана"
    parts = []
    if sal_from:
        parts.append(f"от {sal_from:,}")
    if sal_to:
        parts.append(f"до {sal_to:,}")
    return " ".join(parts) + f" {currency}"


def _schedule_str(schedule: str) -> str:
    mapping = {
        "remote": "Удалённая работа",
        "fullDay": "Полный день (офис)",
        "flyInFlyOut": "Вахтовый метод",
        "flexible": "Гибкий график",
        "shift": "Сменный график",
    }
    return mapping.get(schedule, schedule or "не указан")


async def _call_ai(prompt: str, max_tokens: int = 1000) -> str:
    provider = (await database.get_setting("ai_provider")) or "openrouter"
    if provider == "deepseek":
        url = DEEPSEEK_URL
        model = (await database.get_setting("deepseek_model")) or "deepseek-chat"
        key = (await database.get_setting("deepseek_api_key")) or ""
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
    else:
        url = OPENROUTER_URL
        model = (await database.get_setting("openrouter_model")) or "deepseek/deepseek-chat"
        key = (await database.get_setting("openrouter_api_key")) or ""
        headers = {
            "Authorization": f"Bearer {key}",
            "HTTP-Referer": "https://hh-job-ranker",
            "X-Title": "HH Job Ranker",
            "Content-Type": "application/json",
        }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


def _extract_json(text: str) -> dict:
    # Убрать markdown ```json ... ``` если есть
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[-1] if text.count("```") >= 2 else text
        # Remove language specifier
        lines = text.split("\n")
        if lines[0].lower() in ("json", ""):
            text = "\n".join(lines[1:])
        text = text.rstrip("`").strip()
    return json.loads(text)


async def score_vacancy(vacancy: dict) -> dict:
    salary = _salary_str(vacancy)
    schedule = _schedule_str(vacancy.get("schedule", ""))
    prompt = SCORE_PROMPT.format(
        profile=CANDIDATE_PROFILE,
        title=vacancy.get("title", ""),
        company=vacancy.get("company", ""),
        area=vacancy.get("area", ""),
        schedule=schedule,
        salary=salary,
        description=vacancy.get("description", "")[:3000],
    )

    for attempt in range(2):
        try:
            raw = await _call_ai(prompt, max_tokens=600)
            result = _extract_json(raw)
            score = int(result.get("score", 0))
            grade = result.get("grade", "D").upper()
            # Нормализация grade по score если не совпадает
            if score >= 80:
                grade = "A"
            elif score >= 60:
                grade = "B"
            elif score >= 40:
                grade = "C"
            else:
                grade = "D"
            return {
                "score": score,
                "grade": grade,
                "match_reasons": result.get("match_reasons", []),
                "risk_reasons": result.get("risk_reasons", []),
                "summary": result.get("summary", ""),
            }
        except Exception as e:
            if attempt == 0:
                print(f"[AI] Retry score для '{vacancy.get('title')}': {e}")
            else:
                print(f"[AI] Ошибка score для '{vacancy.get('title')}': {e}")

    return {
        "score": 0,
        "grade": "D",
        "match_reasons": [],
        "risk_reasons": ["Ошибка оценки AI"],
        "summary": "Не удалось оценить вакансию.",
    }


async def generate_cover_letter(vacancy: dict) -> str:
    prompt = COVER_LETTER_PROMPT.format(
        profile=CANDIDATE_PROFILE,
        company=vacancy.get("company", ""),
        title=vacancy.get("title", ""),
        description=vacancy.get("description", "")[:2000],
    )
    try:
        return await _call_ai(prompt, max_tokens=800)
    except Exception as e:
        print(f"[AI] Ошибка генерации письма: {e}")
        return "Не удалось сгенерировать письмо. Попробуйте ещё раз."


async def generate_queries(used_queries: List[str], top_titles: List[str]) -> List[str]:
    used_str = "\n".join(f"- {q}" for q in used_queries) if used_queries else "— (нет)"
    titles_str = "\n".join(f"- {t}" for t in top_titles) if top_titles else "— (нет)"
    prompt = QUERY_PROMPT.format(
        profile=CANDIDATE_PROFILE,
        used_queries=used_str,
        top_titles=titles_str,
    )
    try:
        raw = await _call_ai(prompt, max_tokens=300)
        result = _extract_json(raw)
        queries = result.get("queries", [])
        return [q.strip() for q in queries if q.strip()]
    except Exception as e:
        print(f"[AI] Ошибка генерации запросов: {e}")
        return []
