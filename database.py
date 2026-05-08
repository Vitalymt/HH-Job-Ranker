"""Модуль работы с базой данных SQLite.

Обеспечивает CRUD-операции для вакансий, поисковых запросов,
запусков агента и настроек. Поддерживает пагинацию и экспорт данных.
"""

import json
import logging
import math
import os
from datetime import date, datetime
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "./data/jobs.db")


async def init_db() -> None:
    """Инициализировать базу данных: создать таблицы и засеять настройки по умолчанию."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS vacancies (
                id TEXT PRIMARY KEY,
                title TEXT,
                company TEXT,
                salary_from INTEGER,
                salary_to INTEGER,
                currency TEXT,
                url TEXT,
                schedule TEXT,
                area TEXT,
                description TEXT,
                score INTEGER DEFAULT 0,
                grade TEXT DEFAULT 'D',
                match_reasons TEXT DEFAULT '[]',
                risk_reasons TEXT DEFAULT '[]',
                summary TEXT,
                cover_letter TEXT,
                status TEXT DEFAULT 'new',
                found_by_query TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS search_queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT UNIQUE,
                used_count INTEGER DEFAULT 0,
                good_results INTEGER DEFAULT 0,
                last_used_at TEXT,
                created_at TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS agent_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT,
                finished_at TEXT,
                queries_used INTEGER DEFAULT 0,
                vacancies_found INTEGER DEFAULT 0,
                vacancies_new INTEGER DEFAULT 0,
                vacancies_scored INTEGER DEFAULT 0,
                status TEXT DEFAULT 'running',
                error_text TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )
        """)

        # Засеять настройки по умолчанию из переменных окружения и встроенных значений
        from config.defaults import (
            DEFAULT_CANDIDATE_PROFILE,
            DEFAULT_COVER_LETTER_PROMPT,
            DEFAULT_QUERY_PROMPT,
            DEFAULT_SEED_QUERIES,
            DEFAULT_SCORE_PROMPT,
        )

        defaults = {
            "ai_provider": os.getenv("AI_PROVIDER", "openrouter"),
            "openrouter_api_key": os.getenv("OPENROUTER_API_KEY", ""),
            "openrouter_model": os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat"),
            "deepseek_api_key": os.getenv("DEEPSEEK_API_KEY", ""),
            "deepseek_model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            "candidate_profile": DEFAULT_CANDIDATE_PROFILE,
            "seed_queries": "\n".join(DEFAULT_SEED_QUERIES),
            "prompt_score": DEFAULT_SCORE_PROMPT,
            "prompt_cover_letter": DEFAULT_COVER_LETTER_PROMPT,
            "prompt_queries": DEFAULT_QUERY_PROMPT,
        }
        now = datetime.utcnow().isoformat()
        for key, value in defaults.items():
            await db.execute(
                "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, now),
            )

        await db.commit()


def _parse_row(row: aiosqlite.Row) -> dict:
    """Преобразовать строку базы данных в словарь, десериализуя JSON-поля."""
    item = dict(row)
    item["match_reasons"] = json.loads(item.get("match_reasons") or "[]")
    item["risk_reasons"] = json.loads(item.get("risk_reasons") or "[]")
    return item


def _build_filter_conditions(
    grade: Optional[str],
    schedule: Optional[str],
    status: Optional[str],
    q: Optional[str],
) -> tuple[list[str], list]:
    """Построить условия WHERE и параметры для фильтрации вакансий."""
    conditions: list[str] = []
    params: list = []

    if grade and grade != "all":
        conditions.append("grade = ?")
        params.append(grade.upper())
    else:
        # По умолчанию скрываем D
        if not grade:
            conditions.append("grade != 'D'")

    if schedule and schedule != "all":
        schedule_map = {
            "remote": "remote",
            "hybrid": "flyInFlyOut",
            "office": "fullDay",
        }
        conditions.append("schedule = ?")
        params.append(schedule_map.get(schedule, schedule))

    if status and status != "all":
        if status == "active":
            conditions.append("status NOT IN ('applied', 'rejected')")
        else:
            conditions.append("status = ?")
            params.append(status)
    else:
        conditions.append("status != 'rejected'")

    if q:
        conditions.append("(title LIKE ? OR company LIKE ? OR summary LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])

    return conditions, params


def _build_order_clause(sort: str) -> str:
    """Построить выражение ORDER BY из параметра сортировки."""
    sort_map = {
        "score": "score DESC, created_at DESC",
        "date": "created_at DESC",
        "salary": "COALESCE(salary_from, 0) DESC",
    }
    return sort_map.get(sort, "score DESC, created_at DESC")


async def save_vacancy(data: dict) -> None:
    """Сохранить вакансию в базу данных (вставка или обновление при конфликте ID)."""
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO vacancies (
                id, title, company, salary_from, salary_to, currency,
                url, schedule, area, description, score, grade,
                match_reasons, risk_reasons, summary, cover_letter,
                status, found_by_query, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                score = excluded.score,
                grade = excluded.grade,
                match_reasons = excluded.match_reasons,
                risk_reasons = excluded.risk_reasons,
                summary = excluded.summary,
                updated_at = excluded.updated_at
        """, (
            data.get("id"),
            data.get("title"),
            data.get("company"),
            data.get("salary_from"),
            data.get("salary_to"),
            data.get("currency"),
            data.get("url"),
            data.get("schedule"),
            data.get("area"),
            data.get("description"),
            data.get("score", 0),
            data.get("grade", "D"),
            json.dumps(data.get("match_reasons", []), ensure_ascii=False),
            json.dumps(data.get("risk_reasons", []), ensure_ascii=False),
            data.get("summary"),
            data.get("cover_letter"),
            data.get("status", "new"),
            data.get("found_by_query"),
            data.get("created_at", now),
            now,
        ))
        await db.commit()


async def get_vacancy_by_title_company(title: str, company: str) -> Optional[dict]:
    """Найти вакансию по комбинации названия и компании (для дедупликации)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM vacancies WHERE title = ? AND company = ?",
            (title, company),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return _parse_row(row)


async def update_vacancy_score(vacancy_id: str, score: int, grade: str,
                                match_reasons: list, risk_reasons: list,
                                summary: str) -> None:
    """Обновить оценку существующей вакансии (при дедупликации с более высоким баллом)."""
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE vacancies SET
                score = ?, grade = ?,
                match_reasons = ?, risk_reasons = ?,
                summary = ?, updated_at = ?
            WHERE id = ?
        """, (
            score,
            grade,
            json.dumps(match_reasons, ensure_ascii=False),
            json.dumps(risk_reasons, ensure_ascii=False),
            summary,
            now,
            vacancy_id,
        ))
        await db.commit()


async def get_vacancies(
    grade: Optional[str] = None,
    schedule: Optional[str] = None,
    status: Optional[str] = None,
    sort: str = "score",
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """Получить список вакансий с фильтрацией, сортировкой и пагинацией.

    Возвращает словарь: {"items": [...], "total": N, "page": N, "pages": N}.
    """
    conditions, params = _build_filter_conditions(grade, schedule, status, q)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    order = _build_order_clause(sort)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Подсчитать общее количество
        count_cursor = await db.execute(
            f"SELECT COUNT(*) FROM vacancies {where}",
            params,
        )
        total = (await count_cursor.fetchone())[0]
        pages = max(1, math.ceil(total / page_size))

        # Ограничить страницу допустимыми значениями
        page = max(1, min(page, pages))

        offset = (page - 1) * page_size
        cursor = await db.execute(
            f"SELECT * FROM vacancies {where} ORDER BY {order} LIMIT ? OFFSET ?",
            params + [page_size, offset],
        )
        rows = await cursor.fetchall()
        items = [_parse_row(row) for row in rows]

        return {
            "items": items,
            "total": total,
            "page": page,
            "pages": pages,
        }


async def get_vacancies_export(
    grade: Optional[str] = None,
    schedule: Optional[str] = None,
    status: Optional[str] = None,
    sort: str = "score",
    q: Optional[str] = None,
) -> list[dict]:
    """Получить все вакансии по фильтрам без пагинации (для экспорта CSV/JSON)."""
    conditions, params = _build_filter_conditions(grade, schedule, status, q)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    order = _build_order_clause(sort)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"SELECT * FROM vacancies {where} ORDER BY {order}",
            params,
        )
        rows = await cursor.fetchall()
        return [_parse_row(row) for row in rows]


async def get_vacancy(vacancy_id: str) -> Optional[dict]:
    """Получить одну вакансию по её ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM vacancies WHERE id = ?", (vacancy_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        return _parse_row(row)


async def update_vacancy_status(vacancy_id: str, status: str) -> None:
    """Обновить статус вакансии."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE vacancies SET status = ?, updated_at = ? WHERE id = ?",
            (status, datetime.utcnow().isoformat(), vacancy_id),
        )
        await db.commit()


async def update_cover_letter(vacancy_id: str, text: str) -> None:
    """Сохранить сопроводительное письмо для вакансии."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE vacancies SET cover_letter = ?, updated_at = ? WHERE id = ?",
            (text, datetime.utcnow().isoformat(), vacancy_id),
        )
        await db.commit()


async def get_used_queries() -> list[str]:
    """Получить список уже использованных поисковых запросов."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT query FROM search_queries ORDER BY last_used_at DESC")
        rows = await cursor.fetchall()
        return [row[0] for row in rows]


async def save_query(query: str) -> None:
    """Сохранить поисковый запрос (если ещё не существует)."""
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO search_queries (query, created_at, last_used_at)
            VALUES (?, ?, ?)
            ON CONFLICT(query) DO NOTHING
        """, (query, now, now))
        await db.commit()


async def update_query_stats(query: str, found: int, good: int) -> None:
    """Обновить статистику поискового запроса."""
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE search_queries
            SET used_count = used_count + 1,
                good_results = good_results + ?,
                last_used_at = ?
            WHERE query = ?
        """, (good, now, query))
        await db.commit()


async def get_top_queries(limit: int = 10) -> list[dict]:
    """Получить топ поисковых запросов по количеству хороших результатов."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT query, used_count, good_results, last_used_at
            FROM search_queries
            ORDER BY good_results DESC, used_count DESC
            LIMIT ?
        """, (limit,))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_all_queries() -> list[dict]:
    """Получить все поисковые запросы."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT query, used_count, good_results, last_used_at, created_at
            FROM search_queries
            ORDER BY created_at DESC
        """)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_top_vacancies_titles(limit: int = 10) -> list[str]:
    """Получить топ-названий вакансий с высокими оценками (A, B)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT title FROM vacancies
            WHERE grade IN ('A', 'B')
            ORDER BY score DESC
            LIMIT ?
        """, (limit,))
        rows = await cursor.fetchall()
        return [row[0] for row in rows]


async def get_existing_ids() -> set[str]:
    """Получить множество всех ID вакансий в базе."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id FROM vacancies")
        rows = await cursor.fetchall()
        return {row[0] for row in rows}


async def start_agent_run() -> int:
    """Создать запись о запуске агента и вернуть её ID."""
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO agent_runs (started_at, status) VALUES (?, 'running')",
            (now,),
        )
        await db.commit()
        return cursor.lastrowid


async def finish_agent_run(run_id: int, stats: dict) -> None:
    """Завершить запись о запуске агента с итоговой статистикой."""
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE agent_runs
            SET finished_at = ?,
                queries_used = ?,
                vacancies_found = ?,
                vacancies_new = ?,
                vacancies_scored = ?,
                status = ?,
                error_text = ?
            WHERE id = ?
        """, (
            now,
            stats.get("queries_used", 0),
            stats.get("vacancies_found", 0),
            stats.get("vacancies_new", 0),
            stats.get("vacancies_scored", 0),
            stats.get("status", "done"),
            stats.get("error_text"),
            run_id,
        ))
        await db.commit()


async def get_last_runs(limit: int = 20) -> list[dict]:
    """Получить последние запуски агента."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM agent_runs
            ORDER BY started_at DESC
            LIMIT ?
        """, (limit,))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_setting(key: str) -> Optional[str]:
    """Получить значение настройки по ключу."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row[0] if row else None


async def set_setting(key: str, value: str) -> None:
    """Установить значение настройки (создать или обновить)."""
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, now),
        )
        await db.commit()


async def get_all_settings() -> dict[str, str]:
    """Получить все настройки в виде словаря."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT key, value FROM settings")
        rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}


async def get_stats() -> dict:
    """Получить общую статистику: количество вакансий, сегодняшние добавления и статус агента."""
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM vacancies")
        total = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM vacancies WHERE grade IN ('A', 'B')")
        shown = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT COUNT(*) FROM vacancies WHERE created_at LIKE ?",
            (f"{today}%",),
        )
        today_new = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT started_at, finished_at, status FROM agent_runs ORDER BY started_at DESC LIMIT 1"
        )
        last_run = await cursor.fetchone()

        return {
            "total": total,
            "shown": shown,
            "today_new": today_new,
            "last_run_at": last_run[0] if last_run else None,
            "last_run_finished_at": last_run[1] if last_run else None,
            "agent_status": "running" if last_run and last_run[2] == "running" else "idle",
        }
