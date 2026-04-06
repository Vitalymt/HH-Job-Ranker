import asyncio
import os
from typing import List

import ai_client

MAX_PARALLEL = int(os.getenv("MAX_PARALLEL_AI_REQUESTS", "5"))


async def score_batch(vacancies: List[dict]) -> List[dict]:
    semaphore = asyncio.Semaphore(MAX_PARALLEL)
    total = len(vacancies)
    scored = 0

    async def score_one(vacancy: dict) -> dict:
        nonlocal scored
        async with semaphore:
            result = await ai_client.score_vacancy(vacancy)
            scored += 1
            print(f"[Ranker] Оценено {scored}/{total}: '{vacancy.get('title')}' → {result['grade']} ({result['score']})")
            return {**vacancy, **result}

    tasks = [score_one(v) for v in vacancies]
    return await asyncio.gather(*tasks)
