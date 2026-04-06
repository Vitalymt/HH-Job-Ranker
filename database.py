import aiosqlite
import json
import os
from datetime import datetime, date
from typing import List, Optional, Dict, Any

DB_PATH = os.getenv("DB_PATH", "./data/jobs.db")


async def init_db():
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

        await db.commit()


async def save_vacancy(data: dict):
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


async def get_vacancies(
    grade: Optional[str] = None,
    schedule: Optional[str] = None,
    status: Optional[str] = None,
    sort: str = "score",
    q: Optional[str] = None,
) -> List[dict]:
    conditions = []
    params = []

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

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    sort_map = {
        "score": "score DESC, created_at DESC",
        "date": "created_at DESC",
        "salary": "COALESCE(salary_from, 0) DESC",
    }
    order = sort_map.get(sort, "score DESC, created_at DESC")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"SELECT * FROM vacancies {where} ORDER BY {order}",
            params,
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["match_reasons"] = json.loads(item.get("match_reasons") or "[]")
            item["risk_reasons"] = json.loads(item.get("risk_reasons") or "[]")
            result.append(item)
        return result


async def get_vacancy(vacancy_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM vacancies WHERE id = ?", (vacancy_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        item = dict(row)
        item["match_reasons"] = json.loads(item.get("match_reasons") or "[]")
        item["risk_reasons"] = json.loads(item.get("risk_reasons") or "[]")
        return item


async def update_vacancy_status(vacancy_id: str, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE vacancies SET status = ?, updated_at = ? WHERE id = ?",
            (status, datetime.utcnow().isoformat(), vacancy_id),
        )
        await db.commit()


async def update_cover_letter(vacancy_id: str, text: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE vacancies SET cover_letter = ?, updated_at = ? WHERE id = ?",
            (text, datetime.utcnow().isoformat(), vacancy_id),
        )
        await db.commit()


async def get_used_queries() -> List[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT query FROM search_queries ORDER BY last_used_at DESC")
        rows = await cursor.fetchall()
        return [row[0] for row in rows]


async def save_query(query: str):
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO search_queries (query, created_at, last_used_at)
            VALUES (?, ?, ?)
            ON CONFLICT(query) DO NOTHING
        """, (query, now, now))
        await db.commit()


async def update_query_stats(query: str, found: int, good: int):
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


async def get_top_queries(limit: int = 10) -> List[dict]:
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


async def get_all_queries() -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT query, used_count, good_results, last_used_at, created_at
            FROM search_queries
            ORDER BY created_at DESC
        """)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_top_vacancies_titles(limit: int = 10) -> List[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT title FROM vacancies
            WHERE grade IN ('A', 'B')
            ORDER BY score DESC
            LIMIT ?
        """, (limit,))
        rows = await cursor.fetchall()
        return [row[0] for row in rows]


async def get_existing_ids() -> set:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id FROM vacancies")
        rows = await cursor.fetchall()
        return {row[0] for row in rows}


async def start_agent_run() -> int:
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO agent_runs (started_at, status) VALUES (?, 'running')",
            (now,),
        )
        await db.commit()
        return cursor.lastrowid


async def finish_agent_run(run_id: int, stats: dict):
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


async def get_last_runs(limit: int = 20) -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM agent_runs
            ORDER BY started_at DESC
            LIMIT ?
        """, (limit,))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_stats() -> dict:
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
