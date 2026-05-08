"""Главный модуль FastAPI-приложения HH Job Ranker.

Предоставляет REST API для управления агентом поиска вакансий,
просмотра и фильтрации вакансий, генерации сопроводительных писем,
настройки параметров и экспорта данных.
"""

import csv
import io
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import agent
import ai_client
import database
import hh_client
import logging_config

logger = logging.getLogger(__name__)

# Время запуска для подсчёта uptime
_startup_time: float = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Контекстный менеджер жизненного цикла приложения.

    Инициализирует логирование, базу данных и планировщик при старте.
    Закрывает HTTP-клиенты при завершении.
    """
    logging_config.setup()
    await database.init_db()
    agent.start_scheduler()
    yield
    # Закрытие HTTP-клиентов
    await hh_client.close_client()
    await ai_client.close_client()


app = FastAPI(title="HH Job Ranker", lifespan=lifespan)

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/health")
async def health() -> dict:
    """Эндпоинт проверки здоровья приложения.

    Возвращает статус, версию и время работы с момента запуска.
    """
    uptime = int(time.time() - _startup_time)
    return {"status": "ok", "version": "1.0.0", "uptime_seconds": uptime}


@app.get("/")
async def index() -> FileResponse:
    """Отдать главную страницу приложения."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/vacancies")
async def get_vacancies(
    grade: Optional[str] = None,
    schedule: Optional[str] = None,
    status: Optional[str] = None,
    sort: str = "score",
    q: Optional[str] = None,
    page: int = Query(1, ge=1, description="Номер страницы"),
    page_size: int = Query(20, ge=1, le=100, description="Количество на странице"),
) -> dict:
    """Получить список вакансий с фильтрацией, сортировкой и пагинацией."""
    result = await database.get_vacancies(
        grade=grade,
        schedule=schedule,
        status=status,
        sort=sort,
        q=q,
        page=page,
        page_size=page_size,
    )
    return result


@app.get("/api/vacancies/export")
async def export_vacancies(
    grade: Optional[str] = None,
    schedule: Optional[str] = None,
    status: Optional[str] = None,
    sort: str = "score",
    q: Optional[str] = None,
    format: str = Query("json", description="Формат экспорта: json или csv"),
):
    """Экспортировать вакансии по фильтрам в формате JSON или CSV.

    Возвращает все совпадающие записи без пагинации.
    Для CSV — StreamingResponse с Content-Type text/csv.
    """
    vacancies = await database.get_vacancies_export(
        grade=grade,
        schedule=schedule,
        status=status,
        sort=sort,
        q=q,
    )

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        # Заголовок
        writer.writerow([
            "title", "company", "salary_from", "salary_to",
            "area", "schedule", "grade", "score", "status", "url", "summary",
        ])
        for v in vacancies:
            writer.writerow([
                v.get("title", ""),
                v.get("company", ""),
                v.get("salary_from", ""),
                v.get("salary_to", ""),
                v.get("area", ""),
                v.get("schedule", ""),
                v.get("grade", ""),
                v.get("score", 0),
                v.get("status", ""),
                v.get("url", ""),
                v.get("summary", ""),
            ])
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=vacancies.csv"},
        )
    else:
        return vacancies


@app.get("/api/stats")
async def get_stats() -> dict:
    """Получить общую статистику приложения."""
    stats = await database.get_stats()
    stats["next_run_at"] = agent.get_next_run_time()
    stats["agent_running"] = agent.is_running()
    return stats


@app.post("/api/agent/run")
async def trigger_agent_run(background_tasks: BackgroundTasks) -> dict:
    """Запустить цикл агента в фоновом режиме."""
    if agent.is_running():
        return {"status": "already_running"}
    background_tasks.add_task(agent.run_cycle)
    return {"status": "started"}


@app.get("/api/agent/runs")
async def get_agent_runs() -> list[dict]:
    """Получить список последних запусков агента."""
    runs = await database.get_last_runs(limit=20)
    return runs


@app.get("/api/agent/queries")
async def get_queries() -> list[dict]:
    """Получить все поисковые запросы."""
    queries = await database.get_all_queries()
    return queries


@app.post("/api/cover-letter/{vacancy_id}")
async def generate_cover_letter(vacancy_id: str) -> dict:
    """Сгенерировать сопроводительное письмо для вакансии."""
    vacancy = await database.get_vacancy(vacancy_id)
    if not vacancy:
        raise HTTPException(status_code=404, detail="Вакансия не найдена")
    text = await ai_client.generate_cover_letter(vacancy)
    await database.update_cover_letter(vacancy_id, text)
    return {"text": text}


@app.get("/api/settings")
async def get_settings() -> dict:
    """Получить все настройки (API-ключи маскируются)."""
    s = await database.get_all_settings()
    # Маскировать API-ключи — показывать только последние 6 символов
    for k in ("openrouter_api_key", "deepseek_api_key"):
        val = s.get(k, "")
        if val and len(val) > 6:
            s[k] = "***" + val[-6:]
        elif val:
            s[k] = "***"
    return s


@app.post("/api/settings")
async def update_settings(body: dict) -> dict:
    """Обновить настройки (пропуск замаскированных API-ключей)."""
    allowed = {
        "ai_provider",
        "openrouter_api_key",
        "openrouter_model",
        "deepseek_api_key",
        "deepseek_model",
        "candidate_profile",
        "seed_queries",
        "prompt_score",
        "prompt_cover_letter",
        "prompt_queries",
    }
    for key, value in body.items():
        if key in allowed and value is not None:
            # Пропуск замаскированных значений ключей — не перезаписывать реальный ключ
            if key.endswith("_api_key") and str(value).startswith("***"):
                continue
            await database.set_setting(key, str(value))
    return {"ok": True}


class StatusUpdate(BaseModel):
    """Модель для обновления статуса вакансии."""
    status: str


@app.patch("/api/vacancies/{vacancy_id}/status")
async def update_status(vacancy_id: str, body: StatusUpdate) -> dict:
    """Обновить статус вакансии."""
    allowed = {"new", "viewed", "applied", "rejected"}
    if body.status not in allowed:
        raise HTTPException(status_code=400, detail=f"Статус должен быть одним из: {allowed}")
    await database.update_vacancy_status(vacancy_id, body.status)
    return {"ok": True}
