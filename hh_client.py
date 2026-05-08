"""Клиент для HH.ru Public API.

Поиск вакансий по запросам с учётом регионов и удалённой работы.
Поддерживает retry при ошибках и rate-limiting.
"""

import asyncio
import logging
import os
import re
from typing import List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

HH_BASE_URL = "https://api.hh.ru"
USER_AGENT = os.getenv(
    "HH_USER_AGENT", "HHJobRanker/1.0 (user@example.com)"
).encode("ascii", errors="replace").decode("ascii")

# Переиспользуемый клиент
_client: Optional[httpx.AsyncClient] = None


async def get_client() -> httpx.AsyncClient:
    """Возвращает общий httpx.AsyncClient (ленивая инициализация)."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": USER_AGENT},
        )
    return _client


async def close_client() -> None:
    """Закрывает общий клиент. Вызывать при завершении приложения."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


def clean_html(text: str) -> str:
    """Убирает HTML-теги и декодирует сущности из описания вакансии."""
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(p|li|div|h\d)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&quot;", '"').replace("&#39;", "'")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def format_salary(salary: Optional[dict]) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """Извлекает зарплату из ответа HH API."""
    if not salary:
        return None, None, None
    return salary.get("from"), salary.get("to"), salary.get("currency")


async def _request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    max_retries: int = 3,
    **kwargs,
) -> httpx.Response:
    """Выполняет HTTP-запрос с retry и backoff при ошибках.

    При 429 (rate limit) ждёт Retry-After или 60 секунд.
    При сетевых ошибках делает exponential backoff.
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            resp = await client.request(method, url, **kwargs)

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                logger.warning(
                    "HH API rate limit (429), ожидание %d сек (попытка %d/%d)",
                    retry_after, attempt + 1, max_retries,
                )
                await asyncio.sleep(retry_after)
                continue

            resp.raise_for_status()
            return resp

        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                last_error = e
                wait = 2 ** attempt
                logger.warning(
                    "HH API ошибка %d, retry через %d сек (попытка %d/%d)",
                    e.response.status_code, wait, attempt + 1, max_retries,
                )
                await asyncio.sleep(wait)
                continue
            raise

        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as e:
            last_error = e
            wait = 2 ** attempt
            logger.warning(
                "HH API сетевая ошибка: %s, retry через %d сек (попытка %d/%d)",
                e, wait, attempt + 1, max_retries,
            )
            await asyncio.sleep(wait)

    raise last_error or httpx.ConnectError("Все попытки запроса к HH API исчерпаны")


async def search_vacancies(query: str, area_ids: List[int] = None) -> List[dict]:
    """Ищет вакансии по запросу в HH.ru.

    Ищет по Москве, Санкт-Петербургу и удалёнке по всей РФ.
    Дедупликация по ID вакансии внутри одного запроса.

    Args:
        query: Поисковый запрос.
        area_ids: Список ID регионов. По умолчанию [1, 2] (МСК + СПб).

    Returns:
        Список словарей с данными вакансий из HH API.
    """
    if area_ids is None:
        area_ids = [1, 2]  # Москва и Санкт-Петербург

    client = await get_client()
    results: dict[str, dict] = {}

    # Поиск по каждому региону
    for area_id in area_ids:
        try:
            resp = await _request_with_retry(
                client, "GET",
                f"{HH_BASE_URL}/vacancies",
                params={
                    "text": query,
                    "area": area_id,
                    "per_page": 50,
                    "order_by": "relevance",
                },
            )
            data = resp.json()
            count = len(data.get("items", []))
            logger.info("query='%s' area=%d: %d вакансий", query, area_id, count)
            for item in data.get("items", []):
                results[item["id"]] = item
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error("Ошибка поиска area=%d query='%s': %s", area_id, query, e)

    # Поиск удалённых вакансий по всей РФ
    try:
        resp = await _request_with_retry(
            client, "GET",
            f"{HH_BASE_URL}/vacancies",
            params={
                "text": query,
                "schedule": "remote",
                "per_page": 50,
                "order_by": "relevance",
            },
        )
        data = resp.json()
        count = len(data.get("items", []))
        logger.info("query='%s' remote: %d вакансий", query, count)
        for item in data.get("items", []):
            results[item["id"]] = item
        await asyncio.sleep(0.5)
    except Exception as e:
        logger.error("Ошибка поиска remote query='%s': %s", query, e)

    return list(results.values())


async def get_vacancy_detail(vacancy_id: str) -> Optional[dict]:
    """Получает полное описание вакансии по ID.

    Args:
        vacancy_id: Идентификатор вакансии на HH.ru.

    Returns:
        Словарь с нормализованными данными вакансии или None при ошибке.
    """
    client = await get_client()
    try:
        resp = await _request_with_retry(
            client, "GET", f"{HH_BASE_URL}/vacancies/{vacancy_id}"
        )
        data = resp.json()

        salary = data.get("salary")
        sal_from, sal_to, currency = format_salary(salary)

        description_raw = data.get("description", "")
        description = clean_html(description_raw)
        if len(description) > 3000:
            description = description[:3000] + "..."

        schedule_obj = data.get("schedule") or {}
        schedule = schedule_obj.get("id", "")

        area_obj = data.get("area") or {}
        area = area_obj.get("name", "")

        employer_obj = data.get("employer") or {}
        company = employer_obj.get("name", "")

        return {
            "id": str(data.get("id", vacancy_id)),
            "title": data.get("name", ""),
            "company": company,
            "salary_from": sal_from,
            "salary_to": sal_to,
            "currency": currency,
            "url": data.get("alternate_url", ""),
            "schedule": schedule,
            "area": area,
            "description": description,
        }
    except Exception as e:
        logger.error("Ошибка получения вакансии %s: %s", vacancy_id, e)
        return None
