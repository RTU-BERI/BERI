# БЕРИ (BERI)

**БЕРИ** — веб-сервис краткосрочной аренды бытовых, технических и развлекательных товаров через сеть пунктов выдачи (ПВ).

Пользователь выбирает товар в каталоге, бронирует доступный физический экземпляр в выбранном ПВ, получает его у сотрудника, использует и возвращает в тот же ПВ. Сотрудник подтверждает возврат и проверяет состояние товара. Система рассчитывает стоимость по фактическому времени аренды и взаимодействует с внешним платёжным сервисом.

## 1. Технологический стек

| Область | Технология | Состояние |
|---|---|---|
| Backend | Python **3.13.x** | Зафиксирована ветка версии |
| Web framework | Django **5.2.x** | Базовый проект |
| API framework | Django REST Framework | Подключён как зависимость/приложение |
| Конфигурация | `django-environ`, `.env` | Локальная конфигурация |
| База данных | PostgreSQL | **Целевая СУБД; подключение в Sprint 2** |
| Временная БД | SQLite | Только локальная проверка Django |
| IDE | PyCharm | Рекомендуемая среда |
| Контроль версий | Git, GitHub Organization | Общий репозиторий |

Точные установленные версии библиотек определяются файлом `backend/requirements.txt`.

### 2. За что отвечают каталоги Django

- `backend/config/settings.py` — конфигурация Django, приложения, окружение и подключение БД.
- `backend/config/urls.py` — маршруты всего Django-проекта.
- `backend/config/asgi.py` и `wsgi.py` — точки входа ASGI/WSGI.
- `backend/manage.py` — команды Django (`check`, `migrate`, `runserver` и др.).
- `backend/apps/` — предметные модули. Каждый модуль имеет собственный `apps.py`, `models.py`, `views.py`, `migrations/` и другие файлы.

## 3. Django-приложения и будущие сущности

Архитектура разделена по функциональным областям, а не по одной таблице на приложение:

| Django-приложение | Назначение | Сущности проектируемой БД |
|---|---|---|
| `accounts` | Учётные записи и сотрудники | `account`, `employee` |
| `catalog` | Товары, категории, характеристики, комплектация и тарифы | `product_category`, `product`, `attribute_definition`, `category_attribute`, `product_attribute_value`, `product_component`, `product_tariff` |
| `pickup_points` | Пункты выдачи и расписания | `pickup_point`, `pickup_point_working_hours` |
| `inventory` | Физические экземпляры и история их расположения | `product_item`, `product_item_location_history` |
| `reservations` | Бронирования | `reservation` |
| `rentals` | Выдача, аренда, возврат и проверки | `rental`, `inspection`, `inspection_problem` |
| `payments` | Платёжные средства, попытки оплаты и долги | `payment_method`, `payment`, `debt` |


## 4. Как запустить проект с чистого клона

Все команды ниже рассчитаны на **Python 3.13**

### Шаг 1. Клонировать репозиторий

В PyCharm: **Get from VCS** → URL репозитория → **Clone**. Также можно в терминале:

```powershell
git clone https://github.com/RTU-BERI/BERI.git
cd BERI
```

Откройте в PyCharm **корень `BERI`**, а не одну папку `backend`.

### Шаг 2. Проверить Python

```powershell
python --version
```

Ожидается `Python 3.13.x`. Если в системе несколько версий, в Windows можно явно использовать `py -3.13` для создания окружения.

### Шаг 3. Создать виртуальное окружение

Из корня `BERI`:

```powershell
py -3.13 -m venv venv
```

Если команда `py` недоступна, но `python --version` показывает 3.13:

```powershell
python -m venv venv
```

Активация в Windows PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
```

В Windows cmd:

```cmd
venv\Scripts\activate.bat
```

В Linux/macOS (при установленном Python 3.13):

```bash
python3.13 -m venv venv
source venv/bin/activate
```

После активации в начале строки терминала обычно отображается `(venv)`.

### Шаг 4. Настроить интерпретатор PyCharm

**Settings → Project → Python Interpreter** → выбрать существующее окружение:

```text
Windows:     BERI\venv\Scripts\python.exe
Linux/macOS: BERI/venv/bin/python
```

### Шаг 5. Установить зависимости

Из **корня** репозитория с активным `venv`:

```powershell
python -m pip install -r backend/requirements.txt
```

Зависимости устанавливаются **из зафиксированного `requirements.txt`**;

### Шаг 6. Создать свой `.env`

Файл `backend/.env` не загружается в Git. Создайте его из шаблона:

```powershell
Copy-Item backend\.env.example backend\.env
```

В Windows cmd:

```cmd
copy backend\.env.example backend\.env
```

В Linux/macOS:

```bash
cp backend/.env.example backend/.env
```

Откройте `backend/.env` и задайте:

```dotenv
DJANGO_SECRET_KEY=your-own-local-development-secret-key
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
```

Для генерации локального ключа (после установки зависимостей) можно выполнить:

```powershell
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Скопируйте выведенный ключ в `DJANGO_SECRET_KEY` **только в локальном файле**. Никогда не публикуйте этот ключ в репозитории.

### Шаг 7. Проверить и запустить Django

```powershell
cd backend
python manage.py check
python manage.py migrate
python manage.py runserver
```

После успешного запуска откройте: **http://127.0.0.1:8000/**.

Ожидаемая проверка:

```text
System check identified no issues
```

`migrate` пока применяет стандартные миграции Django во временную локальную SQLite. Остановить сервер — **Ctrl+C**.

> **Важно перед реализацией `Account`:** сейчас ещё не следует без согласования создавать пользовательскую модель и бизнес-миграции. Выбор `AUTH_USER_MODEL` нужно окончательно согласовать до первых миграций, зависящих от пользовательской модели. Если вы уже применяли стандартные миграции Django локально, это не означает, что целевая PostgreSQL-модель пользователей утверждена.

## 5. Настройки Django и переменные окружения

Файлы:

- `backend/.env.example` — список необходимых переменных **без секретов**; в Git.
- `backend/.env` — реальные локальные значения; **в `.gitignore`**.
- `backend/config/settings.py` — читает переменные через `django-environ`.

Пример конфигурации:

```python
from pathlib import Path
import environ

BASE_DIR = Path(__file__).resolve().parent.parent
env = environ.Env(DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list(
    "DJANGO_ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1"],
)
```

DRF и локальные приложения добавлены в `INSTALLED_APPS` через конфигурационные классы:

```python
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.accounts.apps.AccountsConfig",
    "apps.catalog.apps.CatalogConfig",
    "apps.pickup_points.apps.PickupPointsConfig",
    "apps.inventory.apps.InventoryConfig",
    "apps.reservations.apps.ReservationsConfig",
    "apps.rentals.apps.RentalsConfig",
    "apps.payments.apps.PaymentsConfig",
]
```

Внутри каждого `apps/<имя>/apps.py` поле `name` должно содержать **полный путь**, например:

```python
from django.apps import AppConfig

class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.catalog"
```

Если увидите `ModuleNotFoundError: No module named 'apps.catalog.apps'`, проверьте, что папка `backend/apps/catalog/` содержит `__init__.py` и `apps.py`, а пути в `INSTALLED_APPS` и `AppConfig.name` корректны.

## 6. Git: работа в команде

По регламенту проекта используется упрощённый **GitFlow**:

- `main` — стабильные выпущенные версии;
- `develop` — общая ветка текущей разработки;
- `feature/...` — новая функциональность от `develop`;
- `fix/...` — обычные исправления от `develop`;
- `release/...` — подготовка версии;
- `hotfix/...` — срочные исправления от `main`.

Рабочий процесс новой задачи:

```bash
git switch develop
git pull origin develop
git switch -c feature/task-name
# Внести и проверить изменения
git status
git add <нужные-файлы>
git commit -m "feat: short description"
git push -u origin feature/task-name
```

Затем создать **Pull Request в `develop`**. Нужна проверка другим участником (Code Review); для `feature/` и `fix/` применяется **Squash and merge**. Для `release/` и `hotfix/` — обычный Merge согласно регламенту. Прямые рабочие изменения в `main` и `develop` не вносятся.

Сообщения коммитов оформляются на английском, строчными буквами, по **Conventional Commits**:

| Тип | Пример |
|---|---|
| `feat` | `feat: add product booking` |
| `fix` | `fix: correct rental price` |
| `docs` | `docs: update backend setup` |
| `test` | `test: add reservation tests` |
| `refactor` | `refactor: simplify rental service` |
| `style` | `style: format booking module` |
| `chore` | `chore: update dependencies` |

Код оформляется по **PEP 8**: 4 пробела для отступа, `snake_case` для функций и переменных, `PascalCase` для классов, `UPPER_SNAKE_CASE` для констант. `ruff`, pre-commit и CI добавляются отдельными задачами по мере внедрения автоматических проверок.

### Что нельзя коммитить

`venv/`, `.venv/`, `.env`, `.env.*` (кроме `.env.example`), `db.sqlite3`, `.idea/`, `__pycache__/`, `*.pyc`, временные файлы и секреты.

### Что обязательно коммитить

`backend/config/`, `backend/apps/`, `backend/requirements.txt`, `backend/.env.example`, `backend/.python-version`, `README.md`, `.gitignore` и **Django migrations**.

## 7. Правила предметной области, важные для backend

- Каталог и поиск ничего не изменяют в состоянии экземпляров.
- Бронирование закрепляет **один конкретный** доступный `product_item` на ограниченный срок.
- Для выдачи необходимы действующее бронирование, подтверждённое платёжное средство и отсутствие открытого долга.
- При проблеме на осмотре выдачи экземпляр становится недоступным; возможна замена, если есть другой подходящий экземпляр.
- Длительность аренды заранее не задаётся; применяется почасовой тариф к фактическому времени.
- Возврат выполняется в **тот же ПВ**, где товар был выдан.
- После подтверждения возврата сотрудником время аренды больше не начисляется.
- Состояние возвращённого экземпляра зависит от осмотра, **не от результата оплаты**.
- При неуспешной оплате аренда всё равно завершается; образуется задолженность, блокирующая начало новой аренды.
- Внешний платёжный сервис хранит чувствительные реквизиты карты; PAN и CVV в БД БЕРИ не сохраняются.

Для операций бронирования, выдачи и возврата нужно обеспечить атомарность изменений связанных объектов. В реализации с PostgreSQL будут применяться транзакции и, где необходимо, блокировки строк.

## 8. Целевая модель PostgreSQL (следующий этап)

Текущая проектная модель включает 20 предметных таблиц и использует **смешанную стратегию UUID и BIGINT**, а не UUID для всех сущностей.

- `UUID`: `account`, `reservation`, `rental`, `payment_method`, `payment`, `debt`.
- `BIGINT`: `employee`, `pickup_point`, `product_category`, `product`, `attribute_definition`, `category_attribute`, `product_component`, `product_item`, `product_item_location_history`, `product_tariff`, `inspection`, `inspection_problem`.
- Составные ключи без отдельного `id`: `pickup_point_working_hours` и `product_attribute_value`.

## 9. Краткая памятка для нового участника

```powershell
# Из любой удобной директории:
git clone https://github.com/RTU-BERI/BERI.git
cd BERI
py -3.13 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
Copy-Item backend\.env.example backend\.env
# Заполнить backend/.env, особенно DJANGO_SECRET_KEY
cd backend
python manage.py check
python manage.py migrate
python manage.py runserver
```
