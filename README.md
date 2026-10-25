# БЕРИ (BERI)

**БЕРИ** — веб-сервис краткосрочной аренды товаров через пункты выдачи. Пользователь ищет товар, резервирует конкретный экземпляр, получает его у сотрудника и возвращает в ПВ. Оплата предполагает интеграцию с внешним провайдером. **Не все бизнес-сценарии уже реализованы.**

> Актуализировано **26.10.2026** по ветке `develop` (`7dd09a0fb786`). Сверка GitHub-исходников; повторный запуск полного набора тестов после последнего merge не проводился в рамках этой актуализации.

## Что уже реализовано

| Область | Состояние |
|---|---|
| БД | **Готово в коде:** PostgreSQL 17, Django ORM, 21 предметная таблица, 11 проектных миграций и ограничения целостности (PR [#3](https://github.com/RTU-BERI/BERI/pull/3)) |
| Категории товаров | **Готово:** публичные `GET /api/categories/` и `GET /api/categories/{id}/`, Django Admin категорий (PR [#6](https://github.com/RTU-BERI/BERI/pull/6)) |
| Пользователи | **Готово на уровне Backend:** Account, Employee, аутентификация по телефону/паролю, permission helpers, разграничение Client/Employee/ПВ, защищённые Account/Employee/Group Admin (PR [#7](https://github.com/RTU-BERI/BERI/pull/7)) |
| Товары, бронирование, аренда, оплата | Таблицы и DB-защита существуют; публичные API товаров, атомарные бизнес-сервисы бронирования/выдачи/возврата, реальная оплата пока не реализованы |
| Регистрация / JWT / SMS | **Не реализованы** как готовые публичные endpoints и внешние интеграции |

Публичное API категорий — это **входящие** запросы от веб-интерфейса БЕРИ к нашему Django Backend, который читает нашу PostgreSQL. Они **не** обращаются к внешним серверам. Запросы к внешнему платёжному провайдеру — отдельная будущая интеграция.

## Стек и структура

- Python **3.13**, Django **5.2.18**, Django REST Framework **3.18.3**, `django-environ`, `psycopg` 3; точные зависимости — `backend/requirements.txt`.
- База данных — **PostgreSQL**, не SQLite. Локальный сервис — `postgres:17` через Docker Compose.
- `backend/config/settings.py` — параметры Django и подключения; `backend/config/urls.py` — глобальные URL.
- `backend/apps/` — семь модулей: `accounts`, `catalog`, `pickup_points`, `inventory`, `reservations`, `rentals`, `payments`.
- В 21 предметную таблицу входят также `reservation_item_assignment`, `product_item_location_history`, `product_tariff` и др.; служебные таблицы Django отдельно.
- `docs/API.md` — контракт API категорий; `docs/accounts.md` — Backend-аутентификация и политика доступа.

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

Если категорий ещё нет, ожидается `[]`. Путь `/` не обязан возвращать домашнюю страницу: веб-клиент пока не реализован в этом Backend.

## API и разграничение доступа

**Действующие публичные маршруты:**

- `GET /api/categories/` — список категорий (`id`, `name`), без пагинации.
- `GET /api/categories/{id}/` — одна категория или 404.
- `HEAD`, `OPTIONS` поддерживаются; `POST`, `PUT`, `PATCH`, `DELETE` по этим адресам запрещены (405).

Запись категорий возможна через Django Admin при наличии действительных модельных разрешений и staff-статуса.

`Account` — кастомная модель с UUID и телефоном как идентификатором. **Аутентификация паролем существует как Backend**, но готового публичного `POST /api/login/`, JWT или SMS-сервиса в текущем коде нет. Администратор — это права Django (`is_superuser`, `is_staff`, Groups/Permissions), а не третье значение `account_type`.

**Важно:** `Account.status` — отдельная непустая строка, не установленный enum `active/blocked`. Управление фактическим доступом идёт через актуальное `is_active`. `status="blocked"` сам по себе вход не запрещает. Работа с физическими операциями требует действительного `Employee`, соответствующего пункта выдачи и разрешений даже для superuser. Администрирование Account/Employee/Groups ограничено активным `is_staff` + `is_superuser`.

## Проверки и совместимость миграций

Для своей **изолированной тестовой PostgreSQL** (штатный Django Test Runner может создавать и удалять тестовую БД, поэтому не использовать общую/продуктивную среду):

```bash
cd backend
python manage.py makemigrations --check --dry-run
python manage.py test apps.accounts apps.catalog apps.pickup_points apps.inventory apps.reservations apps.payments apps.rentals --verbosity 2
```

Исторически получены следующие результаты в отдельных проверках Codex: **449/449** (DB-only), **483/483** (после интеграции категорий), **594/594** (после адаптации авторизации до слияния PR #7). Это **не** подтверждение повторного прохождения всей тестовой батареи на текущем HEAD `develop`.

Миграции `accounts/catalog/pickup_points` из старых несогласованных PR несовместимы с каноническими initial migrations и **не должны заменять** принятые миграции из PR #3. Миграция считается применённой по `(app, name)`, а не по содержимому.

## Git и документация

- `main` — стабильная ветка; `develop` — актуальная интеграционная ветка; задачи вести в `feature/*` с PR в `develop`.
- Нужны Code Review и проверки согласно действующим правилам защиты ветки. Не выполнять force push и не обходить правила без отдельного решения команды.
- Коммиты/PR должны перечислять изменённые файлы, технический и бизнес-эффект, тесты и ограничения.
- **Не публиковать:** `.env`, пароли, локальные базы/volumes, `venv`, дампы, отчёты с секретами.
- Проектная главная спецификация и согласованные бизнес-требования хранятся в источниках проекта; этот README описывает фактический код и запуск.

Последняя сверка: **2026-10-26**. Фактический код — https://github.com/RTU-BERI/BERI/tree/develop.
