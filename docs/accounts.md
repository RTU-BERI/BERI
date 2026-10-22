# Account, Employee и права БЕРИ

Регистрация, JWT, SMS,
бронирования, аренда и платежи в этот набор изменений не входят.

## Модели и миграции

`accounts.Account` — `AbstractBaseUser + PermissionsMixin`, UUID,
`USERNAME_FIELD = phone`. В БД используются `account`, `employee`,
`pickup_point`, стандартные таблицы Django permissions и M2M-связи.
Отдельной таблицы администратора или собственной RBAC нет.

Порядок зависимостей:

- `accounts/0001_initial`: Account, Django M2M прав, CHECK типов/статусов,
  запрет blocked + is_active, канонический формат телефона.
- `pickup_points/0001_initial`: настоящий PickupPoint с восемью согласованными
  полями. `status` обязателен, без неутверждённых choices/default/CHECK.
- `accounts/0002_employee`: Employee после обеих начальных миграций.

Employee имеет BIGINT PK, уникальный обязательный FK на Account и обязательный
FK на PickupPoint. Обе связи используют PROTECT. Создание/сохранение Employee
проверяет служебный тип аккаунта; смена Account на customer при существующем
профиле отклоняется. Эти сохранения сериализуются блокировкой Account на
PostgreSQL. SQLite не подтверждает поведение блокировок PostgreSQL.

Физические события будущих моделей должны ссылаться на Employee с PROTECT,
чтобы сохранять историю. Связь employee/account_type — межтабличный инвариант:
обычный SQL CHECK не может проверить другую таблицу. Серверная валидация
применяется при save/create и в формах. QuerySet запрещает update/bulk_update
типа Account, его изменение через bulk_create с update_conflicts и смену
account у Employee; bulk_create Employee также запрещён:
для этих изменений нужно использовать save/create с проверками и блокировкой.
Прямой SQL и внутренние save_base обходят прикладные проверки и допустимы
только в доверенных миграционных/операторских инструментах с собственной
проверкой инвариантов. При необходимости поддержки таких загрузок отдельно
проектируются PostgreSQL-триггеры. Проверки доступа дополнительно проверяют
текущий тип аккаунта и отклоняют некорректный профиль.

## Телефон и состояние аккаунта

Телефон хранится в E.164: явно заданный `+`, код страны, не более 15 цифр.
Удаляются пробелы, скобки и дефисы. `phonenumbers` проверяет номер по метаданным
международных планов нумерации; это не доказывает владение номером или его
текущую доступность. Локальные номера и неизвестные коды страны отклоняются.
Нормализация выполняется перед model/form unique validation и ORM-записью;
глобальный UNIQUE в БД защищает от гонок независимо от типа аккаунта.
Модуль `phone.py` используется также backend аутентификации.

`phone_verified_at` остаётся NULL до отдельного подтверждения; ввод номера
не подтверждает его автоматически. Пароли — только Django set_password/hashers.

| status | is_active | Поведение |
|---|---|---|
| active | True | Вход возможен при правильном пароле |
| active | False | Вход запрещён |
| blocked | False | Вход запрещён |
| blocked | True | Запрещено CHECK БД |

Обычное сохранение blocked автоматически устанавливает is_active=False,
включая save(update_fields={"status"}). Разблокировка не включает is_active.
Для bulk update статуса нужно явно записать согласованную пару значений:
DB CHECK отклонит противоречивое обновление. AccountBackend проверяет оба
поля при входе и восстановлении пользователя из сессии. DRF использует
существующие стандартные authentication mechanisms; глобальные defaults
доступа не менялись.

## Назначение административных прав

Первоначальный доверенный оператор запускает:

```powershell
python manage.py createsuperuser
```

Команда запрашивает phone, account_type, status и пароль. Тип и статус
обязательны также в `Account.objects.create_superuser(...)`; скрытых значений
нет. Superuser имеет is_staff/is_superuser=True. Employee не создаётся
автоматически. Для blocked устанавливается is_active=False.

В Django Admin доверенным назначающим права считается текущий активный
superuser. Только он меняет account_type, is_staff, is_superuser, группы и
permissions; только он изменяет группы, поскольку их permissions влияют
на всех участников. Это консервативная политика без дополнительной системы
ролей и без публичного REST API выдачи прав.

Обычный staff должен иметь соответствующие Django permissions. Наличие
is_staff даёт лишь вход в Admin. Staff с change_account может менять только
чужие обычные customer-аккаунты без групп/permissions/служебных флагов.
Редактирование своего или привилегированного аккаунта запрещено: изменение
чужого пароля/телефона также могло бы дать административный доступ.
Поля назначения прав для такого staff read-only, подложенные POST-поля
не применяются. Пароль редактируется стандартной защищённой формой Django,
а не как строка hash/plaintext. EmployeeAdmin использует add/change/view/delete
permissions модели Employee; профиль требует настоящего служебного Account.

## Подключение будущих API

В `apps.accounts.access`:

- `get_active_account(user)` перечитывает статус/is_active из БД.
- `get_employee(user)` возвращает свежий корректный профиль с текущим ПВ.
- `has_permissions(user, permission_names)` проверяет явный непустой список
  Django permissions на новом экземпляре Account, без старого permission cache.
- `require_physical_access(user, pickup_point_id, permissions)` возвращает
  Employee либо PermissionDenied; ПВ должен быть разрешён сервером из
  сохранённого бизнес-объекта, а не взят как доказательство из запроса.

| Операция | Применение |
|---|---|
| Клиентская операция | IsClient |
| Свои объекты: list/detail | ClientOwnedQuerysetMixin + IsClientOwner; owner_lookup — ORM-путь к account PK |
| Административная операция | HasDjangoPermissions; required_permissions — явный непустой список |
| Физическая операция/служебная выборка | EmployeePickupPointQuerysetMixin + CanPerformPhysicalOperation; pickup_point_lookup и required_permissions обязательны |

Mixin указывается перед DRF generic view в списке базовых классов. Например,
owner_lookup для бронирования будет `customer_account_id`, для задолженности —
`rental__reservation__customer_account_id`. Для ПВ используется серверный
ORM-путь к пункту операции. Отсутствие обязательной конфигурации запрещает доступ.

Для unsafe HTTP-методов физическая view дополнительно реализует
`get_physical_pickup_point_id()`: разрешает целевой объект из уже ограниченного
queryset/проверенной серверной связи и возвращает его ПВ. Одного payload ID
недостаточно. View вызывает object permission через get_object(), а write-сервис
повторно вызывает require_physical_access внутри транзакции.

Для операций создания клиентских объектов владелец устанавливается сервисом
из свежего request.user; customer_account_id из payload не принимается.
Permission сам по себе не подставляет владельца в serializer.save().

Физические операции всегда требуют Employee, свой ПВ и business permission.
Superuser обходит только стандартные Django permission checks, но не Employee
и scope. При смене ПВ проверка видит новую привязку. При реализации конкурентных
переводов сотрудника и физических write-сервисов нужно удерживать блокировку
Employee в той же транзакции, согласовав порядок блокировок. Read helpers
не удерживают lock между проверкой и будущей записью.

Production-endpoints не добавлялись. HTTP-сценарии существуют только в test URLConf.
Бизнес-permissions выдачи/возврата будут объявлены соответствующими приложениями;
этот код не придумывает разрешения и не выдаёт их сотрудникам автоматически.

## Проверки и ограничения среды

Из backend с установленными requirements:

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py migrate --settings=config.test_settings --noinput
python manage.py test --settings=config.test_settings --noinput
```

`config.test_settings` использует отдельную SQLite :memory:, публичный тестовый
ключ и быстрый тестовый password hasher. Никогда не использовать эти настройки
для сервера. Команды с этим settings не меняют backend/db.sqlite3.
Обычный `migrate` допускается лишь после проверки целевой БД; для старой среды
с auth.User нужен отдельный согласованный план переноса данных.

SQLite подтверждает базовую миграционную цепочку и прикладные проверки.
PostgreSQL-подключение/драйвер не добавляются этой задачей; переносимость
миграций, ограничения и конкурентные блокировки нужно повторно проверить
на отдельной PostgreSQL перед готовностью к merge.

72 теста прошли на SQLite :memory:; Django check и
makemigrations --check --dry-run успешны, миграции на чистой тестовой БД
применены. Независимое Code/Security Review не оставило обнаруженных
неисправленных замечаний: найденные обходы проверки Employee/account_type
через массовые ORM-записи и конфликтный upsert закрыты guards и тестами.
Это не заявление о проверке PostgreSQL или готовности к развёртыванию.

## Файлы и передача изменений

| Файлы | Назначение |
|---|---|
| backend/apps/accounts/models.py, managers.py | Account, Employee, менеджер и ограничения ORM |
| backend/apps/accounts/phone.py, backends.py | E.164 и Django authentication |
| backend/apps/accounts/forms.py, admin.py | Создание, пароли и защищённое управление |
| backend/apps/accounts/access.py, permissions.py | Проверки сервисного слоя, DRF permissions и queryset scopes |
| backend/apps/accounts/migrations/0001_initial.py, 0002_employee.py | Account и Employee |
| backend/apps/accounts/test_models.py, test_admin.py, test_access.py | Модели, HTTP Admin и права доступа |
| backend/apps/pickup_points/models.py, tests.py, migrations/0001_initial.py | Минимальный PickupPoint |
| backend/config/settings.py, test_settings.py | Account auth и изолированные тесты |
| backend/requirements.txt | Добавлен phonenumbers==9.0.41; файл переведён из UTF-16 в UTF-8, прочие версии сохранены |
| README.md, docs/accounts.md | Состояние реализации, архитектура и инструкции |
| specification.md | Общая инструкция команды, версия 1.2.0 и журнал изменений |


