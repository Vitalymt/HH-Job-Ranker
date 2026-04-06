# HH Job Ranker

Автономный AI-агент для поиска и оценки вакансий на **HH.ru**.

Сам генерирует запросы → парсит API → оценивает через LLM → показывает в веб-интерфейсе → генерирует сопроводительные письма.

```
┌─────────────────────────────────────────────────────────┐
│  HH Job Ranker              [▶ Запустить сейчас]        │
│  🟢 Агент активен · последний запуск: 14:32             │
│  Найдено: 312 · Показано (A+B): 47 · Новых сегодня: 12  │
├─────────────────────────────────────────────────────────┤
│  [A] 87%  Head of AI Automation · Контур   💰 350–450k  │
│           📍 Удалёнка · 2 часа назад                    │
│  ✅ Автоматизация  ✅ AI-фокус  ✅ n8n                  │
│  ⚠️  Требуют Python-разработку                          │
│  "Сильное совпадение: автоматизация + лидерство в AI"   │
│  [🔗 Открыть на HH]  [✉️ Сопроводительное]  [статус ▾] │
└─────────────────────────────────────────────────────────┘
```

---

## Как это работает

Агент запускается при старте и затем каждые N часов:

1. **Генерирует запросы** — LLM придумывает новые поисковые фразы на основе профиля и предыдущих результатов
2. **Парсит HH.ru** — ищет по Москве, Санкт-Петербургу и удалёнке по всей РФ (Public API, без ключей)
3. **Оценивает вакансии** — LLM сравнивает каждую с профилем кандидата, выставляет оценку A/B/C/D и score 0–100
4. **Сохраняет в SQLite** — дедупликация по vacancy_id, повторная обработка не нужна
5. **Показывает в UI** — тёмная тема, фильтры по оценке/формату/статусу, поиск по тексту
6. **Генерирует письмо** — по клику LLM пишет уникальное сопроводительное под конкретную вакансию

---

## Быстрый старт

**Требования:** Docker, API ключ [OpenRouter](https://openrouter.ai/keys) или [DeepSeek](https://platform.deepseek.com)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/vitalymt/hh-job-ranker/claude/hh-job-ranker-app-AmK1X/setup.sh)
```

Скрипт сам:
- Установит Docker если нет
- Склонирует репозиторий
- Спросит API ключ, модель, порт, интервал поиска
- Соберёт и запустит Docker контейнер

Через 30 секунд открывай `http://YOUR_VM_IP:8000`.

### Альтернативный вариант

```bash
git clone https://github.com/vitalymt/hh-job-ranker ~/hh-job-ranker
cd ~/hh-job-ranker
bash setup.sh
```

---

## Настройка под себя

Перед запуском отредактируй **один файл** — `config/profile.py`:

```python
CANDIDATE_PROFILE = """
Иван, 28 лет. Москва / удалёнка.
Целевой доход: от 250 000 ₽/мес.

ОПЫТ: ...
СТЕК: ...
ЦЕЛЕВЫЕ РОЛИ: ...
НЕ ИНТЕРЕСНО: ...
"""

SEED_QUERIES = [
    "python developer",
    "backend engineer",
    ...
]
```

`CANDIDATE_PROFILE` — свободный текст на любом языке. Чем подробнее опыт, стек и ожидания — тем точнее AI будет оценивать совпадение.

`SEED_QUERIES` — начальные запросы для первого цикла. После первого запуска AI генерирует новые сам, ориентируясь на то, что уже нашёл.

---

## AI провайдеры

Поддерживается переключение прямо из UI (вкладка ⚙️ Настройки) без рестарта:

| Провайдер | Ключ | Рекомендуемая модель | Стоимость |
|---|---|---|---|
| [OpenRouter](https://openrouter.ai) | `sk-or-...` | `deepseek/deepseek-chat` | ~$0.0001/вакансия |
| [DeepSeek](https://platform.deepseek.com) | `sk-...` | `deepseek-chat` | ~$0.00008/вакансия |

Другие модели через OpenRouter: `anthropic/claude-haiku-4-5`, `openai/gpt-4o-mini`, `google/gemini-flash-1.5`.

---

## Конфигурация

Все настройки задаются при запуске `setup.sh` и сохраняются в `.env`:

| Переменная | Описание | По умолчанию |
|---|---|---|
| `OPENROUTER_API_KEY` | Ключ OpenRouter | — |
| `OPENROUTER_MODEL` | Модель OpenRouter | `deepseek/deepseek-chat` |
| `DEEPSEEK_API_KEY` | Ключ DeepSeek (опционально) | — |
| `DEEPSEEK_MODEL` | Модель DeepSeek | `deepseek-chat` |
| `AI_PROVIDER` | Активный провайдер | `openrouter` |
| `HH_USER_AGENT` | User-Agent для HH API | `HHJobRanker/1.0 (email)` |
| `PORT` | Порт веб-интерфейса | `8000` |
| `SEARCH_INTERVAL_HOURS` | Интервал поиска в часах | `2` |
| `MAX_PARALLEL_AI_REQUESTS` | Параллельных AI-запросов | `5` |
| `DB_PATH` | Путь к базе данных | `./data/jobs.db` |

---

## Структура проекта

```
hh-job-ranker/
├── setup.sh              # Полный bootstrap: клон + Docker + запуск
├── main.py               # FastAPI приложение, все API роуты
├── agent.py              # Агентный цикл + APScheduler
├── query_generator.py    # AI-генерация поисковых запросов
├── hh_client.py          # HH.ru Public API клиент
├── ai_client.py          # OpenRouter / DeepSeek клиент
├── ranker.py             # Параллельная batch-оценка вакансий
├── database.py           # SQLite + все CRUD функции
├── config/
│   └── profile.py        # ← редактировать под себя
├── static/
│   └── index.html        # Весь фронтенд — один файл (Vanilla JS)
├── data/                 # SQLite база (gitignored, пересоздаётся)
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## API роуты

| Метод | Роут | Описание |
|---|---|---|
| `GET` | `/` | Веб-интерфейс |
| `GET` | `/api/vacancies` | Список вакансий (фильтры: grade, schedule, status, sort, q) |
| `GET` | `/api/stats` | Статистика дашборда |
| `POST` | `/api/agent/run` | Запустить цикл поиска вручную |
| `GET` | `/api/agent/runs` | История запусков агента |
| `GET` | `/api/agent/queries` | Поисковые запросы и их эффективность |
| `POST` | `/api/cover-letter/{id}` | Сгенерировать сопроводительное письмо |
| `GET` | `/api/settings` | Получить настройки (ключи замаскированы) |
| `POST` | `/api/settings` | Обновить провайдер / модель / ключи |
| `PATCH` | `/api/vacancies/{id}/status` | Обновить статус вакансии |

---

## Полезные команды

```bash
# Смотреть логи в реальном времени
docker logs hh-ranker -f

# Остановить / запустить
docker stop hh-ranker
docker start hh-ranker

# Обновить до новой версии
cd ~/hh-job-ranker
git pull
docker build -t hh-ranker . --quiet
docker stop hh-ranker && docker rm hh-ranker
docker run -d --name hh-ranker --restart unless-stopped \
  -p 8000:8000 --env-file .env -v "$(pwd)/data:/app/data" hh-ranker

# Переустановить с нуля (сохраняет данные в data/)
bash setup.sh
```

---

## Стек

- **Backend**: Python 3.11, FastAPI, Uvicorn, aiosqlite, APScheduler, httpx
- **Frontend**: Vanilla JS, HTML/CSS (один файл, без фреймворков)
- **База данных**: SQLite
- **Деплой**: Docker
- **AI**: OpenRouter API / DeepSeek API (OpenAI-совместимые)
- **Данные**: HH.ru Public API (без авторизации)

---

## Лицензия

MIT — используй как хочешь, адаптируй под свой профиль.
