import json
import os
from typing import List
import httpx

import database
from config.defaults import (
    DEFAULT_CANDIDATE_PROFILE,
    DEFAULT_SCORE_PROMPT,
    DEFAULT_COVER_LETTER_PROMPT,
    DEFAULT_QUERY_PROMPT,
)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"


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
    profile = (await database.get_setting("candidate_profile")) or DEFAULT_CANDIDATE_PROFILE
    prompt_tpl = (await database.get_setting("prompt_score")) or DEFAULT_SCORE_PROMPT
    salary = _salary_str(vacancy)
    schedule = _schedule_str(vacancy.get("schedule", ""))
    prompt = prompt_tpl.format(
        profile=profile,
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
    profile = (await database.get_setting("candidate_profile")) or DEFAULT_CANDIDATE_PROFILE
    prompt_tpl = (await database.get_setting("prompt_cover_letter")) or DEFAULT_COVER_LETTER_PROMPT
    prompt = prompt_tpl.format(
        profile=profile,
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
    profile = (await database.get_setting("candidate_profile")) or DEFAULT_CANDIDATE_PROFILE
    prompt_tpl = (await database.get_setting("prompt_queries")) or DEFAULT_QUERY_PROMPT
    used_str = "\n".join(f"- {q}" for q in used_queries) if used_queries else "— (нет)"
    titles_str = "\n".join(f"- {t}" for t in top_titles) if top_titles else "— (нет)"
    prompt = prompt_tpl.format(
        profile=profile,
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
