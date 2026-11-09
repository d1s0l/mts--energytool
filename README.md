# MTS Energytool

Каркас проекта: frontend, FastAPI и PostgreSQL. Бизнес-логика пока не реализована.

## Запуск через Docker

```sh
docker compose up --build
```

- Frontend: http://localhost:8080
- Swagger: http://localhost:8000/docs
- OpenAPI: http://localhost:8000/openapi.json

PostgreSQL хранит данные в volume `pgdata`. При старте backend открывает пул
подключений к БД. Таблицы, прикладные эндпоинты, модель и RAG пока отсутствуют.
В Swagger пока нет прикладных операций. Frontend не подключён к backend.

Для изменения локального пароля скопируйте `.env.example` в `.env` в корне.
Пароль по умолчанию предназначен для локальной разработки. При смене пароля
существующего PostgreSQL нужно также изменить пароль роли в самой БД.

## Backend локально

Используйте Python 3.12 (та же версия, что в Docker).

```sh
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

Из корня проекта запустите БД с локальным портом:

```sh
docker compose -f compose.yaml -f compose.local.yaml up -d postgres
```

Затем из `backend/`:

```sh
uvicorn app.main:app --reload
ruff check .
```

При изменении пароля согласуйте DATABASE_URL в `backend/.env` с корневым `.env`.
Тесты добавим вместе с реализацией; сейчас папка `backend/tests` пустая.

## Структура backend

- `app/main.py` — приложение FastAPI и жизненный цикл пула БД.
- `app/config.py` — настройки окружения.
- `app/db.py` — подключение PostgreSQL без схемы таблиц.
- `app/api/` — место для будущих маршрутов.
- `app/schemas.py` — место для моделей запросов и ответов.
- `app/consultant.py`, `app/rag.py` — места для будущих интеграций.
- `solution.py` — заготовка адаптера для платформы, пока не выполняет сабмит.

Контракты API, схема БД, интеграция LLM/RAG и формат сабмита требуют согласования.
LLM SDK, LangGraph и векторная БД пока не добавлены: выбор зависит от команды.
