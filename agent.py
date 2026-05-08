"""Модуль агента автоматического поиска и оценки вакансий.

Управляет планировщиком (APScheduler) и циклом поиска: генерация запросов,
поиск вакансий на HH.ru, получение деталей, AI-оценка, сохранение в БД
с дедупликацией и отправка уведомлений в Telegram.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database
import hh_client
import query_generator
import ranker

logger = logging.getLogger(__name__)

SEARCH_INTERVAL_HOURS = float(os.getenv("SEARCH_INTERVAL_HOURS", "2"))

_scheduler: Optional[AsyncIOScheduler] = None
_is_running: bool = False

# Настройки Telegram-уведомлений
_telegram_token: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
_telegram_chat_id: Optional[str] = os.getenv("TELEGRAM_CHAT_ID")


async def _send_telegram_notification(vacancy: dict) -> None:
    """Отправить уведомление о вакансии класса A в Telegram.

    Формирует сообщение с названием, компанией, зарплатой, оценкой и ссылкой.
    Отправка выполняется асинхронно, не блокируя основной цикл.
    Если переменные окружения не заданы — молча пропускает.
    """
    if not _telegram_token or not _telegram_chat_id:
        return

    salary_parts = []
    if vacancy.get("salary_from"):
        salary_parts.append(f"от {vacancy['salary_from']:,}")
    if vacancy.get("salary_to"):
        salary_parts.append(f"до {vacancy['salary_to']:,}")
    salary_str = " ".join(salary_parts) if salary_parts else "не указана"

    message = (
        "🎯 *Найдена вакансия класса A!*\n\n"
        f"📋 *{vacancy.get('title', 'Без названия')}*\n"
        f"🏢 {vacancy.get('company', 'Не указана')}\n"
        f"💰 {salary_str}\n"
        f"⭐ Оценка: {vacancy.get('score', 0)}/100\n"
        f"📍 {vacancy.get('area', '')}\n"
        f"🔗 {vacancy.get('url', '')}"
    )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{_telegram_token}/sendMessage",
                json={
                    "chat_id": _telegram_chat_id,
                    "text": message,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
            )
            logger.info("Telegram-уведомление отправлено: %s", vacancy.get("title"))
    except Exception as e:
        logger.warning("Ошибка отправки Telegram-уведомления: %s", e)


async def run_cycle() -> None:
    """Выполнить один цикл поиска и оценки вакансий.

    Генерирует поисковые запросы, ищет вакансии на HH.ru, получает описания,
    оценивает через AI, сохраняет в БД с дедупликацией и отправляет
    уведомления о вакансиях класса A.
    """
    global _is_running
    if _is_running:
        logger.info("Цикл уже выполняется, пропускаю")
        return

    _is_running = True
    run_id = await database.start_agent_run()
    logger.info("Запуск цикла #%d в %s", run_id, datetime.now(timezone.utc).isoformat())

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
        logger.info("Запросов для поиска: %d", len(queries))

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
                logger.info("Запрос '%s': найдено %d, новых %d", query, len(results), new_count)
            except Exception as e:
                logger.error("Ошибка поиска по '%s': %s", query, e)

        stats["vacancies_new"] = len(all_new_vacancies)
        logger.info("Всего новых вакансий для обработки: %d", len(all_new_vacancies))

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
                logger.info("Загружено описаний: %d/%d", i, total_new)
            await asyncio.sleep(0.3)

        logger.info("Получены описания для %d вакансий", len(detailed))

        # 4. Batch-оценка
        scored = await ranker.score_batch(detailed)
        stats["vacancies_scored"] = len(scored)

        # 5. Сохранить в БД + подсчёт статистики по запросам + дедупликация
        query_good: dict[str, int] = {}
        query_found: dict[str, int] = {}

        for v in scored:
            title = v.get("title", "")
            company = v.get("company", "")

            # Дедупликация: проверяем по комбинации title + company
            existing = await database.get_vacancy_by_title_company(title, company)
            if existing and existing["id"] != v.get("id"):
                # Вакансия уже есть (другой ID из другого запроса)
                if existing.get("score", 0) >= v.get("score", 0):
                    # Существующая оценка выше — пропускаем
                    logger.debug(
                        "Дедупликация: '%s' (%s) уже есть с оценкой %d >= %d, пропускаю",
                        title, company, existing["score"], v.get("score", 0),
                    )
                else:
                    # Новая оценка выше — обновляем существующую
                    logger.info(
                        "Дедупликация: обновляю '%s' (%s): %d → %d",
                        title, company, existing["score"], v.get("score", 0),
                    )
                    await database.update_vacancy_score(
                        existing["id"],
                        v.get("score", 0),
                        v.get("grade", "D"),
                        v.get("match_reasons", []),
                        v.get("risk_reasons", []),
                        v.get("summary", ""),
                    )
                continue

            await database.save_vacancy(v)
            q = v.get("found_by_query", "")
            if q:
                query_found[q] = query_found.get(q, 0) + 1
                if v.get("grade") in ("A", "B"):
                    query_good[q] = query_good.get(q, 0) + 1

            # Отправить Telegram-уведомление для вакансий класса A
            if v.get("grade") == "A":
                asyncio.create_task(_send_telegram_notification(v))

        # 6. Обновить статистику запросов
        for q in queries:
            found_count = query_found.get(q, 0)
            good_count = query_good.get(q, 0)
            await database.update_query_stats(q, found_count, good_count)

        # 7. Завершить
        await database.finish_agent_run(run_id, stats)
        logger.info(
            "Цикл #%d завершён. Найдено: %d, новых: %d, оценено: %d",
            run_id,
            stats["vacancies_found"],
            stats["vacancies_new"],
            stats["vacancies_scored"],
        )

    except Exception as e:
        stats["status"] = "error"
        stats["error_text"] = str(e)
        logger.error("Ошибка в цикле #%d: %s", run_id, e)
        await database.finish_agent_run(run_id, stats)
    finally:
        _is_running = False


def start_scheduler() -> None:
    """Запустить планировщик задач для периодических циклов поиска."""
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
    logger.info("Планировщик запущен, интервал: %sч", SEARCH_INTERVAL_HOURS)


def get_next_run_time() -> Optional[str]:
    """Получить время следующего запуска планировщика (ISO-формат)."""
    global _scheduler
    if not _scheduler:
        return None
    job = _scheduler.get_job("periodic_run")
    if job and job.next_run_time:
        return job.next_run_time.isoformat()
    return None


def is_running() -> bool:
    """Проверить, выполняется ли сейчас цикл агента."""
    return _is_running
