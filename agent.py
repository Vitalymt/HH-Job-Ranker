import asyncio
import os
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database
import hh_client
import query_generator
import ranker

SEARCH_INTERVAL_HOURS = float(os.getenv("SEARCH_INTERVAL_HOURS", "2"))

_scheduler = None
_is_running = False


async def run_cycle():
    global _is_running
    if _is_running:
        print("[Agent] Цикл уже выполняется, пропускаю")
        return

    _is_running = True
    run_id = await database.start_agent_run()
    print(f"[Agent] Запуск цикла #{run_id} в {datetime.utcnow().isoformat()}")

    stats = {
        "queries_used": 0,
        "vacancies_found": 0,
        "vacancies_new": 0,
        "vacancies_scored": 0,
        "status": "done",
        "error_text": None,
    }

    try:
        # 1. Генерировать запросы
        queries = await query_generator.generate()
        stats["queries_used"] = len(queries)
        print(f"[Agent] Запросов для поиска: {len(queries)}")

        # 2. Поиск по каждому запросу
        existing_ids = await database.get_existing_ids()
        all_new_vacancies: dict[str, dict] = {}

        for query in queries:
            try:
                results = await hh_client.search_vacancies(query)
                stats["vacancies_found"] += len(results)
                new_count = 0
                for item in results:
                    vid = str(item.get("id", ""))
                    if vid and vid not in existing_ids and vid not in all_new_vacancies:
                        all_new_vacancies[vid] = {"_query": query, "_raw": item}
                        new_count += 1
                print(f"[Agent] Запрос '{query}': найдено {len(results)}, новых {new_count}")
            except Exception as e:
                print(f"[Agent] Ошибка поиска по '{query}': {e}")

        stats["vacancies_new"] = len(all_new_vacancies)
        print(f"[Agent] Всего новых вакансий для обработки: {len(all_new_vacancies)}")

        if not all_new_vacancies:
            await database.finish_agent_run(run_id, stats)
            _is_running = False
            return

        # 3. Получить полные описания
        detailed: list[dict] = []
        total_new = len(all_new_vacancies)
        for i, (vid, meta) in enumerate(all_new_vacancies.items(), 1):
            detail = await hh_client.get_vacancy_detail(vid)
            if detail:
                detail["found_by_query"] = meta["_query"]
                detailed.append(detail)
            if i % 20 == 0 or i == total_new:
                print(f"[Agent] Загружено описаний: {i}/{total_new}")
            await asyncio.sleep(0.3)

        print(f"[Agent] Получены описания для {len(detailed)} вакансий")

        # 4. Batch-оценка
        scored = await ranker.score_batch(detailed)
        stats["vacancies_scored"] = len(scored)

        # 5. Сохранить в БД + подсчёт статистики по запросам
        query_good: dict[str, int] = {}
        query_found: dict[str, int] = {}

        for v in scored:
            await database.save_vacancy(v)
            q = v.get("found_by_query", "")
            if q:
                query_found[q] = query_found.get(q, 0) + 1
                if v.get("grade") in ("A", "B"):
                    query_good[q] = query_good.get(q, 0) + 1

        # 6. Обновить статистику запросов
        all_queries_set = set(query_found.keys()) | set(query_good.keys()) | set(queries)
        for q in queries:
            found_count = query_found.get(q, 0)
            good_count = query_good.get(q, 0)
            await database.update_query_stats(q, found_count, good_count)

        # 7. Завершить
        await database.finish_agent_run(run_id, stats)
        print(f"[Agent] Цикл #{run_id} завершён. Найдено: {stats['vacancies_found']}, "
              f"новых: {stats['vacancies_new']}, оценено: {stats['vacancies_scored']}")

    except Exception as e:
        stats["status"] = "error"
        stats["error_text"] = str(e)
        print(f"[Agent] Ошибка в цикле #{run_id}: {e}")
        await database.finish_agent_run(run_id, stats)
    finally:
        _is_running = False


def start_scheduler():
    global _scheduler
    _scheduler = AsyncIOScheduler()

    # Запустить немедленно при старте
    _scheduler.add_job(run_cycle, "date", id="initial_run")

    # Запускать каждые N часов
    _scheduler.add_job(
        run_cycle,
        "interval",
        hours=SEARCH_INTERVAL_HOURS,
        id="periodic_run",
        misfire_grace_time=60,
    )

    _scheduler.start()
    print(f"[Agent] Планировщик запущен, интервал: {SEARCH_INTERVAL_HOURS}ч")


def get_next_run_time() -> str | None:
    global _scheduler
    if not _scheduler:
        return None
    job = _scheduler.get_job("periodic_run")
    if job and job.next_run_time:
        return job.next_run_time.isoformat()
    return None


def is_running() -> bool:
    return _is_running
