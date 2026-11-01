# БЕРИ (BERI)

**БЕРИ** — веб-сервис краткосрочной аренды товаров через пункты выдачи. Пользователь ищет товар, резервирует конкретный экземпляр, получает его у сотрудника и возвращает в ПВ. Оплата предполагает интеграцию с внешним провайдером. **Не все бизнес-сценарии уже реализованы.**

> Актуализировано **31.10.2026** в `feature/s1-p3-catalog-admin` от `develop` (`5e2a39a`, PR #10). Реализация S1-P3 находится в рабочей ветке и ещё не merged. Анализ/границы — [план S1-P3](docs/s1-p3-plan.md), фактические результаты проверок и объяснение кода — [отчёт S1-P3](docs/s1-p3-report.md). Правила работы — [Specification.md](Specification.md).

## Что уже реализовано

| Область | Состояние |
|---|---|
| БД | **Готово в коде:** PostgreSQL 17, Django ORM, 21 предметная таблица, 11 проектных миграций и ограничения целостности (PR [#3](https://github.com/RTU-BERI/BERI/pull/3)) |
| Категории товаров | **Готово:** публичные `GET /api/categories/` и `GET /api/categories/{id}/`, Django Admin категорий (PR [#6](https://github.com/RTU-BERI/BERI/pull/6)) |
| Пользователи | **Готово на уровне Backend:** Account, Employee, аутентификация по телефону/паролю, permission helpers, разграничение Client/Employee/ПВ, защищённые Account/Employee/Group Admin (PR [#7](https://github.com/RTU-BERI/BERI/pull/7)) |
| Товары, ПВ и наличие | **API в develop:** список/поиск/категория товаров, карточка/характеристики/комплектация/текущий тариф (PR #9); ПВ/расписание и наличие (PR #10) |
| Управление каталогом S1-P3 | **В рабочей ветке:** Django Admin товаров, определений/назначений/значений характеристик, комплектации и тарифов; права, валидация и защита истории; BERI-107-A/B, ожидает review/merge |
| Бронирование, аренда, оплата | Таблицы и DB-защита существуют; атомарные бизнес-сервисы бронирования/выдачи/возврата, расчёт и sandbox/реальная оплата пока не реализованы |
| Сессионная авторизация и frontend | **Локальная демонстрация:** React/TypeScript/Vite, вход, регистрация, выход, восстановление сессии; `/api/auth/*` (PR [#8](https://github.com/RTU-BERI/BERI/pull/8)) |
| JWT / SMS / промышленная регистрация | **Не реализованы**; регистрация ограничена локальным demo, подробности в [docs/auth.md](docs/auth.md) |

Публичное API категорий — это **входящие** запросы от веб-интерфейса БЕРИ к нашему Django Backend, который читает нашу PostgreSQL. Они **не** обращаются к внешним серверам. Запросы к внешнему платёжному провайдеру — отдельная будущая интеграция.

## Стек и структура

- Python **3.13**, Django **5.2.18**, Django REST Framework **3.18.3**, `django-environ`, `psycopg` 3; точные зависимости — `backend/requirements.txt`.
- База данных — **PostgreSQL**, не SQLite. Локальный сервис — `postgres:17` через Docker Compose.
- `backend/config/settings.py` — параметры Django и подключения; `backend/config/urls.py` — глобальные URL.
- `backend/apps/` — семь модулей: `accounts`, `catalog`, `pickup_points`, `inventory`, `reservations`, `rentals`, `payments`.
- В 21 предметную таблицу входят также `reservation_item_assignment`, `product_item_location_history`, `product_tariff` и др.; служебные таблицы Django отдельно.
- `docs/API.md` — обзор действующего API и контракт категорий; `docs/accounts.md` — Backend-аутентификация и политика доступа.
- `frontend/` — React 19 + TypeScript + Vite; Node >=22.12.0, установка через `npm ci`, проверка через `npm run build`.
- `docs/auth.md` — сессионное HTTP API и запуск локального demo; `docs/project-state.md` — исторический обзор PR #8 с указателем на текущий отчёт; `docs/s1-p3-report.md` — управление каталогом и текущие проверки.

## Локальный запуск на чистом окружении

Требуются **Python 3.13**, Docker Engine/Desktop с Compose v2 и Git. Ниже команды из корня клона `BERI`.

### 1. Клонирование и окружение

```bash
git clone https://github.com/RTU-BERI/BERI.git
cd BERI
git switch develop
python3.13 -m venv venv
source venv/bin/activate          # macOS / Linux
python -m pip install -r backend/requirements.txt
```

Windows PowerShell (альтернативная активация):

```powershell
py -3.13 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
```

### 2. Настройка секретов

Скопируйте **только шаблон** и заполните личные значения:

```bash
cp backend/.env.example backend/.env     # macOS / Linux
```

```powershell
Copy-Item backend\.env.example backend\.env  # PowerShell
```

В `backend/.env` обязательно задайте `DJANGO_SECRET_KEY` и собственный `DB_PASSWORD`. Шаблон задаёт `DB_NAME=beri`, `DB_USER=beri_local`, `DB_HOST=127.0.0.1`, `DB_PORT=55432`; порт можно поменять, если занят. **Никогда не коммитьте `.env`.**

Локальный ключ Django можно сгенерировать так (после установки зависимостей):

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

### 3. Запуск PostgreSQL

Из **корня** проекта:

```bash
docker compose --env-file backend/.env -f compose.yaml up -d db
docker compose --env-file backend/.env -f compose.yaml ps
```

Контейнер и volume — **не таблицы**: таблицы создаёт следующая команда Django `migrate`. Если в Docker уже есть БД с иными initial migrations, **не переиспользуйте её без согласованного плана миграции данных**, не запускайте `down -v` и не удаляйте чужие volumes.

### 4. Django и миграции

На **новой собственной локальной БД**:

```bash
cd backend
python manage.py check
python manage.py migrate --plan
python manage.py migrate
python manage.py showmigrations
python manage.py runserver
```

После запуска доступен Django Admin на `http://127.0.0.1:8000/admin/`. Для входа в Admin нужен правильно созданный пользователь с необходимыми флагами и разрешениями; API категорий не требует входа.

Проверка API (на собственных тестовых данных):

```bash
curl http://127.0.0.1:8000/api/categories/
```

Если категорий ещё нет, ожидается `[]`. Django не обслуживает главную страницу frontend. Для интерфейса входа/регистрации запустите Vite из `frontend/` через `npm ci` и `npm run dev`, затем откройте `http://localhost:5173`. Для регистрации дополнительно нужен `run_local_demo` и настройки из [docs/auth.md](docs/auth.md); обычный `runserver` регистрацию не включает.

## API и разграничение доступа

**Действующие публичные маршруты:**

- `GET /api/categories/` — список категорий (`id`, `name`), без пагинации.
- `GET /api/categories/{id}/` — одна категория или 404.
- `HEAD`, `OPTIONS` поддерживаются; `POST`, `PUT`, `PATCH`, `DELETE` по этим адресам запрещены (405).

Запись категорий возможна через Django Admin при наличии действительных модельных разрешений и staff-статуса.

После интеграций #9/#10 также доступны `GET /api/products/?search=...&category=...`, `GET /api/products/{id}/`, `GET /api/pickup-points/`, `GET /api/pickup-points/{id}/` и `GET /api/products/{id}/availability/`. Текущий тариф берётся из ProductTariff, а при его отсутствии возвращается null.

В ветке S1-P3 каталог заполняется через `/admin/`: нужны active staff и соответствующие права `catalog.view_*`, `add_*`, `change_*`, а для разрешённых удалений — `delete_*`. Привязка Employee сама по себе не даёт административных прав. Фото необязательно. Используемые определения/назначения характеристик доступны только для чтения; комплектация замораживается после первого осмотра. Ставка и начало сохранённого тарифа неизменны: закройте открытый период и создайте следующую версию. Массовые действия новых разделов отключены. Подробная матрица операций — [в плане](docs/s1-p3-plan.md).

Дополнительно доступны `GET /api/auth/csrf/`, `GET /api/auth/me/`, `POST /api/auth/login/`, `POST /api/auth/logout/` и ограниченный локальным demo `POST /api/auth/register/`. POST требует CSRF; используются cookie-сессии. Регистрация не выполняет автоматический вход. Полный контракт и ограничения — [docs/auth.md](docs/auth.md).

`Account` — кастомная модель с UUID и телефоном как идентификатором. Аутентификация паролем реализована как Backend и сессионное HTTP API; JWT и SMS-сервиса нет. Вне demo HTTP-вход клиента требует подтверждённого телефона. Администратор — это права Django (`is_superuser`, `is_staff`, Groups/Permissions), а не третье значение `account_type`.

**Важно:** `Account.status` — отдельная непустая строка, не установленный enum `active/blocked`. Управление фактическим доступом идёт через актуальное `is_active`. `status="blocked"` сам по себе вход не запрещает. Работа с физическими операциями требует действительного `Employee`, соответствующего пункта выдачи и разрешений даже для superuser. Администрирование Account/Employee/Groups ограничено активным `is_staff` + `is_superuser`.

## Проверки и совместимость миграций

Для своей **изолированной тестовой PostgreSQL** (штатный Django Test Runner может создавать и удалять тестовую БД, поэтому не использовать общую/продуктивную среду):

```bash
cd backend
python manage.py makemigrations --check --dry-run
python manage.py test apps.accounts apps.catalog apps.pickup_points apps.inventory apps.reservations apps.payments apps.rentals --verbosity 2
```

Исторически получены следующие результаты в отдельных проверках Codex: **449/449** (DB-only), **483/483** (после интеграции категорий), **594/594** (после адаптации авторизации до слияния PR #7). Это **не** подтверждение повторного прохождения всей тестовой батареи на текущем HEAD `develop`.

В рабочей ветке S1-P3 от base `5e2a39a` 31.10.2026 прошли **663/663 backend-теста** на собственной PostgreSQL 17, включая 30 новых тестов Admin. Свежие миграции применены, check и отсутствие расхождений моделей/миграций проверены. Это результат рабочей ветки, а не утверждение о merged-статусе S1-P3. Браузерные E2E в этой работе не запускались.

Миграции `accounts/catalog/pickup_points` из старых несогласованных PR несовместимы с каноническими initial migrations и **не должны заменять** принятые миграции из PR #3. Миграция считается применённой по `(app, name)`, а не по содержимому.

## Git и документация

- `main` — стабильная ветка; `develop` — актуальная интеграционная ветка; задачи вести в `feature/*` с PR в `develop`.
- Нужны Code Review и проверки согласно действующим правилам защиты ветки. Не выполнять force push и не обходить правила без отдельного решения команды.
- Коммиты/PR должны перечислять изменённые файлы, технический и бизнес-эффект, тесты и ограничения.
- **Не публиковать:** `.env`, пароли, локальные базы/volumes, `venv`, дампы, отчёты с секретами.
- Проектная главная спецификация и согласованные бизнес-требования хранятся в источниках проекта; этот README описывает фактический код и запуск.

Последняя локальная сверка: **2026-10-31**, base `5e2a39a` + рабочие изменения S1-P3. [Код ветки develop](https://github.com/RTU-BERI/BERI/tree/develop).
