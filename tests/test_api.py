"""Базовые тесты API HH Job Ranker.

Запуск: pytest tests/ -v
"""

import json
import os
import sys

import pytest
import httpx

# Добавить корень проекта в PATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _test_env(tmp_path, monkeypatch):
    """Изолировать тесты: временная БД, без реальных API ключей."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AI_PROVIDER", "openrouter")
    monkeypatch.setenv("SEARCH_INTERVAL_HOURS", "999")  # Не запускать планировщик
    # Убираем реальные ключи если есть
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)


@pytest.fixture
async def client():
    """Создать тестовый httpx.AsyncClient для FastAPI приложения."""
    # Импортируем ПОСЛЕ установки env vars
    from main import app
    from database import init_db

    await init_db()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as ac:
        yield ac


# ─── Health ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "uptime_seconds" in data
    assert isinstance(data["uptime_seconds"], int)


# ─── Vacancies (empty) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_vacancies_empty(client):
    resp = await client.get("/api/vacancies")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "pages" in data
    assert data["items"] == []
    assert data["total"] == 0


# ─── Vacancies pagination ─────────────────────────────────────

@pytest.mark.asyncio
async def test_vacancies_pagination_params(client):
    resp = await client.get("/api/vacancies?page=1&page_size=5")
    assert resp.status_code == 200
    data = resp.json()
    assert data["page"] == 1
    assert data["pages"] >= 1


@pytest.mark.asyncio
async def test_vacancies_pagination_invalid(client):
    resp = await client.get("/api/vacancies?page=0")
    assert resp.status_code == 422  # Validation error (page >= 1)


# ─── Stats ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stats(client):
    resp = await client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "shown" in data
    assert "today_new" in data
    assert "agent_running" in data


# ─── Settings ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_settings_get(client):
    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert "ai_provider" in data
    # Ключ должен быть замаскирован
    if data.get("openrouter_api_key"):
        assert data["openrouter_api_key"].startswith("***")


@pytest.mark.asyncio
async def test_settings_update(client):
    resp = await client.post("/api/settings", json={
        "ai_provider": "deepseek",
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Проверить что применилось
    resp2 = await client.get("/api/settings")
    assert resp2.json()["ai_provider"] == "deepseek"


@pytest.mark.asyncio
async def test_settings_skip_masked_keys(client):
    """Замаскированные ключи не должны перезаписывать реальные."""
    # Сначала установим ключ
    await client.post("/api/settings", json={
        "openrouter_api_key": "sk-or-real-key-12345",
    })
    # Попробуем перезаписать замаскированным
    await client.post("/api/settings", json={
        "openrouter_api_key": "***345",
    })
    # Ключ должен остаться реальным (но замаскированным в GET)
    resp = await client.get("/api/settings")
    assert "345" in resp.json()["openrouter_api_key"]


# ─── Export ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_export_json(client):
    resp = await client.get("/api/vacancies/export?format=json")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_export_csv(client):
    resp = await client.get("/api/vacancies/export?format=csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/csv; charset=utf-8"
    assert "attachment" in resp.headers.get("content-disposition", "")
    # Первая строка — заголовок
    lines = resp.text.strip().split("\n")
    assert lines[0].startswith("title,company,")


# ─── Agent runs ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_runs(client):
    resp = await client.get("/api/agent/runs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ─── Agent queries ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_queries(client):
    resp = await client.get("/api/agent/queries")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ─── Status update ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_update_invalid(client):
    resp = await client.patch(
        "/api/vacancies/123/status",
        json={"status": "invalid_status"},
    )
    assert resp.status_code == 400


# ─── Cover letter (no vacancy) ────────────────────────────────

@pytest.mark.asyncio
async def test_cover_letter_not_found(client):
    resp = await client.post("/api/cover-letter/999999")
    assert resp.status_code == 404


# ─── Static index ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_index(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "HH Job Ranker" in resp.text
