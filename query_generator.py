import asyncio
from typing import List

import ai_client
import database
from config.profile import SEED_QUERIES


async def generate() -> List[str]:
    used_queries = await database.get_used_queries()

    if not used_queries:
        # Первый запуск — использовать seed-запросы
        print("[QueryGen] Первый запуск, используем SEED_QUERIES")
        for q in SEED_QUERIES:
            await database.save_query(q)
        return list(SEED_QUERIES)

    top_titles = await database.get_top_vacancies_titles(limit=10)

    print(f"[QueryGen] Генерирую новые запросы (уже использовано: {len(used_queries)})")
    new_queries = await ai_client.generate_queries(used_queries, top_titles)

    if not new_queries:
        # Fallback: повторно использовать топ запросы
        top = await database.get_top_queries(limit=6)
        print("[QueryGen] Fallback — используем топ запросы из БД")
        return [q["query"] for q in top] if top else list(SEED_QUERIES)

    # Сохранить новые запросы в БД
    for q in new_queries:
        await database.save_query(q)

    print(f"[QueryGen] Новых запросов: {len(new_queries)}: {new_queries}")
    return new_queries
