import asyncio
import os
import re
from typing import List, Tuple, Optional
import httpx

HH_BASE_URL = "https://api.hh.ru"
USER_AGENT = os.getenv("HH_USER_AGENT", "HHJobRanker/1.0 (user@example.com)")


def clean_html(text: str) -> str:
    if not text:
        return ""
    # Replace block-level tags with newlines
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(p|li|div|h\d)[^>]*>", "\n", text, flags=re.IGNORECASE)
    # Remove all remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode common HTML entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&quot;", '"').replace("&#39;", "'")
    # Collapse extra whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def format_salary(salary: Optional[dict]) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    if not salary:
        return None, None, None
    return salary.get("from"), salary.get("to"), salary.get("currency")


async def search_vacancies(query: str, area_ids: List[int] = None) -> List[dict]:
    if area_ids is None:
        area_ids = [1, 2]  # Москва и Санкт-Петербург

    headers = {"User-Agent": USER_AGENT}
    results: dict[str, dict] = {}

    async with httpx.AsyncClient(timeout=30) as client:
        # Поиск по каждому региону
        for area_id in area_ids:
            try:
                resp = await client.get(
                    f"{HH_BASE_URL}/vacancies",
                    params={
                        "text": query,
                        "area": area_id,
                        "per_page": 20,
                        "order_by": "relevance",
                        "search_field": "everywhere",
                    },
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                for item in data.get("items", []):
                    results[item["id"]] = item
                await asyncio.sleep(0.5)
            except Exception as e:
                print(f"[HH] Ошибка поиска area={area_id} query='{query}': {e}")

        # Поиск удалённых вакансий по всей РФ
        try:
            resp = await client.get(
                f"{HH_BASE_URL}/vacancies",
                params={
                    "text": query,
                    "schedule": "remote",
                    "per_page": 20,
                    "order_by": "relevance",
                    "search_field": "everywhere",
                },
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("items", []):
                results[item["id"]] = item
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"[HH] Ошибка поиска remote query='{query}': {e}")

    return list(results.values())


async def get_vacancy_detail(vacancy_id: str) -> Optional[dict]:
    headers = {"User-Agent": USER_AGENT}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{HH_BASE_URL}/vacancies/{vacancy_id}",
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

            salary = data.get("salary")
            sal_from, sal_to, currency = format_salary(salary)

            description_raw = data.get("description", "")
            description = clean_html(description_raw)
            # Обрезаем до 3000 символов для экономии токенов
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
        print(f"[HH] Ошибка получения вакансии {vacancy_id}: {e}")
        return None
