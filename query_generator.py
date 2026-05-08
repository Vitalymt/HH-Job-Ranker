"""Модуль генерации поисковых запросов для поиска вакансий.

Использует seed-запросы при первом запуске и генерирует новые запросы
с помощью AI на основе уже использованных запросов и топ-вакансий.
"""

import logging
from typing import Optional

import ai_client
import database
from config.defaults import DEFAULT_SEED_QUERIES

logger = logging.getLogger(__name__)


async def _get_seed_queries() -> list[str]:
    """Получить seed-запросы из настроек или значений по умолчанию."""
    seed_val = await database.get_setting("seed_queries")
    if seed_val:
        return [q.strip() for q in seed_val.splitlines() if q.strip()]
    return list(DEFAULT_SEED_QUERIES)


async def generate() -> list[str]:
    """Сгенерировать список поисковых запросов для текущего цикла.

    При первом запуске возвращает seed-запросы из настроек.
    При последующих — генерирует новые запросы через AI на основе
    истории использованных запросов и титулов найденных вакансий.
    При ошибке генерации — fallback на топ-запросы из БД.
    """
    used_queries = await database.get_used_queries()

    if not used_queries:
        # Первый запуск — использовать seed-запросы
        logger.info("Первый запуск, используем seed-запросы из настроек")
        seed_list = await _get_seed_queries()
        for q in seed_list:
            await database.save_query(q)
        return seed_list

    top_titles = await database.get_top_vacancies_titles(limit=10)

    logger.info("Генерирую новые запросы (уже использовано: %d)", len(used_queries))
    new_queries = await ai_client.generate_queries(used_queries, top_titles)

    if not new_queries:
        # Fallback: повторно использовать топ запросы
        top = await database.get_top_queries(limit=6)
        logger.info("Fallback — используем топ запросы из БД")
        return [q["query"] for q in top] if top else await _get_seed_queries()

    # Сохранить новые запросы в БД
    for q in new_queries:
        await database.save_query(q)

    logger.info("Новых запросов: %d: %s", len(new_queries), new_queries)
    return new_queries
