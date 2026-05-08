"""Модуль параллельной оценки вакансий с помощью AI.

Использует семафор для ограничения количества параллельных запросов
к AI-модели и обрабатывает ошибки по каждой вакансии отдельно.
"""

import asyncio
import logging
import os
from typing import Optional

import ai_client

logger = logging.getLogger(__name__)

MAX_PARALLEL = int(os.getenv("MAX_PARALLEL_AI_REQUESTS", "5"))


async def score_batch(vacancies: list[dict]) -> list[dict]:
    """Оценить.batch вакансий параллельно с ограничением конкурентности.

    Каждая вакансия оценивается независимо — ошибка при оценке одной
    вакансии не прерывает оценку остальных. Ошибка возвращает оценку D.
    """
    semaphore = asyncio.Semaphore(MAX_PARALLEL)
    total = len(vacancies)
    scored = 0

    async def score_one(vacancy: dict) -> dict:
        """Оценить одну вакансию с обработкой ошибок."""
        nonlocal scored
        async with semaphore:
            try:
                result = await ai_client.score_vacancy(vacancy)
                scored += 1
                logger.info(
                    "Оценено %d/%d: '%s' → %s (%d)",
                    scored,
                    total,
                    vacancy.get("title"),
                    result["grade"],
                    result["score"],
                )
                return {**vacancy, **result}
            except Exception as e:
                scored += 1
                logger.error(
                    "Ошибка оценки '%s': %s. Присваиваю.grade D.",
                    vacancy.get("title"),
                    e,
                )
                return {
                    **vacancy,
                    "score": 0,
                    "grade": "D",
                    "match_reasons": [],
                    "risk_reasons": [f"Ошибка оценки: {e}"],
                    "summary": "Не удалось оценить вакансию.",
                }

    tasks = [score_one(v) for v in vacancies]
    return await asyncio.gather(*tasks)
