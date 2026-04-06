import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import agent
import ai_client
import database


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.init_db()
    agent.start_scheduler()
    yield


app = FastAPI(title="HH Job Ranker", lifespan=lifespan)

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/vacancies")
async def get_vacancies(
    grade: Optional[str] = None,
    schedule: Optional[str] = None,
    status: Optional[str] = None,
    sort: str = "score",
    q: Optional[str] = None,
):
    vacancies = await database.get_vacancies(
        grade=grade,
        schedule=schedule,
        status=status,
        sort=sort,
        q=q,
    )
    return vacancies


@app.get("/api/stats")
async def get_stats():
    stats = await database.get_stats()
    stats["next_run_at"] = agent.get_next_run_time()
    stats["agent_running"] = agent.is_running()
    return stats


@app.post("/api/agent/run")
async def trigger_agent_run(background_tasks: BackgroundTasks):
    if agent.is_running():
        return {"status": "already_running"}
    background_tasks.add_task(agent.run_cycle)
    return {"status": "started"}


@app.get("/api/agent/runs")
async def get_agent_runs():
    runs = await database.get_last_runs(limit=20)
    return runs


@app.get("/api/agent/queries")
async def get_queries():
    queries = await database.get_all_queries()
    return queries


@app.post("/api/cover-letter/{vacancy_id}")
async def generate_cover_letter(vacancy_id: str):
    vacancy = await database.get_vacancy(vacancy_id)
    if not vacancy:
        raise HTTPException(status_code=404, detail="Вакансия не найдена")
    text = await ai_client.generate_cover_letter(vacancy)
    await database.update_cover_letter(vacancy_id, text)
    return {"text": text}


@app.get("/api/settings")
async def get_settings():
    s = await database.get_all_settings()
    # Mask API keys — show only last 6 chars
    for k in ("openrouter_api_key", "deepseek_api_key"):
        val = s.get(k, "")
        if val and len(val) > 6:
            s[k] = "***" + val[-6:]
        elif val:
            s[k] = "***"
    return s


@app.post("/api/settings")
async def update_settings(body: dict):
    allowed = {
        "ai_provider",
        "openrouter_api_key",
        "openrouter_model",
        "deepseek_api_key",
        "deepseek_model",
    }
    for key, value in body.items():
        if key in allowed and value is not None:
            # Skip masked key values — don't overwrite real key with mask
            if key.endswith("_api_key") and str(value).startswith("***"):
                continue
            await database.set_setting(key, str(value))
    return {"ok": True}


class StatusUpdate(BaseModel):
    status: str


@app.patch("/api/vacancies/{vacancy_id}/status")
async def update_status(vacancy_id: str, body: StatusUpdate):
    allowed = {"new", "viewed", "applied", "rejected"}
    if body.status not in allowed:
        raise HTTPException(status_code=400, detail=f"Статус должен быть одним из: {allowed}")
    await database.update_vacancy_status(vacancy_id, body.status)
    return {"ok": True}
