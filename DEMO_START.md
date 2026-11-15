# Быстрый локальный показ

Нужны запущенный Docker Desktop (Linux containers), интернет для первой сборки,
архив базы знаний `knowledge-base.zip` и рабочие параметры API модели.

1. Скопируйте `.env.example` в `.env` в корне проекта.
2. Заполните `KNOWLEDGE_BASE_PATH` абсолютным путём к архиву. На Windows
   используйте прямые слеши: `C:/energytool/knowledge-base.zip`.
3. Укажите рабочие `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` своего провайдера.
   `RAG_MODE=local` оставьте: поиск работает без API эмбеддингов, генерация требует LLM.
4. Из папки с `compose.yaml` выполните:

```sh
docker compose -f compose.yaml -f compose.demo.yaml up --build -d
docker compose -f compose.yaml -f compose.demo.yaml logs -f backend
```

После строки `Application startup complete` откройте http://localhost:3000.
Swagger: http://localhost:8000/docs.

Проверка и диагностика:

```sh
docker compose -f compose.yaml -f compose.demo.yaml ps
docker compose -f compose.yaml -f compose.demo.yaml logs --tail=100 backend frontend
```

Остановка без удаления истории:

```sh
docker compose -f compose.yaml -f compose.demo.yaml down
```

Демо-конфигурация подставляет общий локальный токен на nginx для совместимости
фронтенда и backend. Она предназначена только для показа на своём ПК через localhost.
Параметры модели, секреты и база знаний в архив сборки не включены.
