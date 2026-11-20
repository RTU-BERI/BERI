# BERI API — current public contracts

Updated 2026-11-20 against develop `9bf42db47f76b848e883f285ffd0caf67ce0c8b7` (merged PR #8–17). Category validation from S1 is merged in #14, inspections/replacement in #17. The S2-P6 frontend consumes these contracts and the issue contract from open PR #19. Their combined live verification is recorded in [S2-P6 report](s2-p6-report.md).

| Method | Path | Current contract |
|---|---|---|
| GET | `/api/categories/`, `/api/categories/{id}/` | Category id/name; details below |
| GET | `/api/products/` | Array; optional search by name (`search`) and category ID (`category`); order name/id |
| GET | `/api/products/{id}/` | Product card, category, attributes, components and current tariff or null |
| GET | `/api/pickup-points/`, `/api/pickup-points/{id}/` | Pickup point address, coordinates and working hours |
| GET | `/api/products/{id}/availability/` | Product ID and pickup points with available_count |
| GET/POST | `/api/auth/*` | Session/CSRF endpoints; exact methods and payloads in [auth.md](auth.md) |

Product list fields: id, name, category (ID), status, image_url, brand, model.
`id` and `category` are JSON numbers (BIGINT IDs), `name` and `status` are
strings; `image_url`, `brand` and `model` are nullable strings (also may be empty).
Both category/product lists are unpaginated arrays, including `[]` for no matches.
Product list is public with authentication disabled and AllowAny, just like categories.
`search` uses trimmed name `icontains`; `category` is a category ID. Together
they narrow the same queryset (AND); results are ordered by name/id.

### Product list category validation (S1 audit correction)

`category` is optional. Omitted or exactly empty (`?category=`) means no category
filter, preserving existing behavior. A supplied nonempty value must contain
**1–19 ASCII digits**, with numeric value **1…9223372036854775807** (PostgreSQL
BIGINT ID range). Leading zeroes are accepted within the 19-character limit.
Whitespace, signs, decimal/exponent notation, Unicode digits, zero, negative
numbers and oversized values return **HTTP 400**. Input is bounded before integer
conversion, including strings longer than Python's integer-conversion limit.

The default JSON response (request `Accept: application/json`) is:

```json
{"category":["Укажите целый ID категории от 1 до 9223372036854775807 (1–19 ASCII-цифр)."]}
```

This is DRF field-error JSON local to product query validation; auth's
`error.code/message/fields` and Django Admin error handling do not change.
A well-formed but nonexistent ID returns **200 []**, not 400/404. Search remains
trimmed case-insensitive name matching; search and category combine with AND.
There is no newly introduced Product.status enum or status filter.

Examples: `/api/products/?category=abc` → 400;
`/api/products/?category=9223372036854775808` → 400;
`/api/products/?category=` → the unfiltered list;
`/api/products/?search=drill&category=1` → the intersection, possibly `[]`.

The Desktop UI `/catalog` uses these endpoints via the existing same-origin
client and URLSearchParams. It maps product.category to category names using
the category list; it ignores stale responses, distinguishes API errors from
empty results, and falls back for missing/broken images. A changed array/field
contract is treated as an error. Contract changes are a BERI-104-A dependency;
do not replace the API with permanent fixtures. Details: [S1-P5 report](s1-p5-report.md).

Detail fields: id, name, description, image_url, brand, model, status, category
(id/name), attributes, components, tariff, created_at, updated_at.
Attributes expose name, unit, value_text, value_number, value_boolean;
components expose name/quantity; tariff exposes hourly_rate/valid_from/valid_to.
The current tariff satisfies `valid_from <= now < valid_to` (or has no end).
No tariff means null, not an invented zero price.

The S1-P6 Desktop UI `/products/:id` reads product detail, pickup points and
availability through the existing same-origin client. Selecting a pickup point
matches `pickup_point_id` locally; it does not add an API filter or request.
Loading, retry, 404, missing tariff/image and unavailable stock have explicit UI
states. A failed availability request is not displayed as zero stock. The UI
uses the label `₽/час`; the current API has no separate currency field.
See [S1-P6 report](s1-p6-report.md) for tests and scope.

Availability currently counts ProductItem rows with status `available` per
pickup point. It does not reserve an item or guarantee later booking; GET does
not update status. Stronger booking/issuance checks belong to S2, not S1-P3.
PickupPoint.status is an opaque string with no agreed active/inactive vocabulary
or filter in these APIs. S1-P6 displays all returned pickup points; changing
that business rule requires a separate decision.

Data entered using the new catalog Admin is read by these existing endpoints.
The Admin requires active staff and model permissions and preserves tariff and
inspection history. See [merged PR #11](https://github.com/RTU-BERI/BERI/pull/11),
[Admin code](../backend/apps/catalog/admin.py) and
[history tests](../backend/apps/catalog/test_admin_history.py). The former local
S1-P3 plan/report are not present in this Git tree; their content is not invented here. No public product,
tariff or component write API is added. Booking creation/expiry and the customer/employee booking APIs are merged in PR #15/#16. Rental issuance/return and payment APIs remain future work.

## Category integration context (PR #6)

This adapts the Backend functionality from PR #1 to `develop` after PR #3.
The canonical models, managers, validators, database constraints, migrations,
settings and existing DB-only tests are unchanged. No new dependency is needed.

## Public read contract

`GET /api/categories/` returns a JSON array containing only `id` and `name`:

```json
[
  {"id": 2, "name": "Бытовая техника"},
  {"id": 1, "name": "Инструменты"}
]
```

An empty dictionary returns `[]`. Results are ordered by `name`, then `id`,
using PostgreSQL's configured collation. There is no pagination in this contract.
Ordering is defined in the API queryset, not in the canonical model's Meta.

`GET /api/categories/{id}/` returns one object with the same two fields.
An absent or invalid identifier returns HTTP 404. The URL names remain
`category-list` and `category-detail`, as in PR #1.

Both endpoints are public. They do not authenticate sessions or credentials,
and do not expose accounts, products, payment data or nested relationships.
Their explicit access policy does not depend on global DRF permission settings.
This endpoint never represents proof of a customer's or employee's identity.

HEAD and OPTIONS are supported. POST, PUT, PATCH and DELETE are unavailable
on the list and detail routes and return HTTP 405 without changing categories.
The serializer exposes read-only fields. No category write API is introduced.

The `/api/` prefix preserves PR #1. A future switch to `/api/v1/` requires a
separate API-contract decision; no second alias is introduced here.

## Django Admin

PR #6 registered only `ProductCategory`. S1-P3 now additionally registers
Product, AttributeDefinition, CategoryAttribute, ProductAttributeValue,
ProductComponent and ProductTariff through merged PR #11. The category list shows ID/name,
supports name search, and orders by name/ID. Historical operational
records outside catalog remain outside these editing screens. Allowed catalog
operations are documented in [merged PR #11](https://github.com/RTU-BERI/BERI/pull/11),
[backend/apps/catalog/admin.py](../backend/apps/catalog/admin.py),
[Admin behavior tests](../backend/apps/catalog/test_product_admin.py) and
[history protection tests](../backend/apps/catalog/test_admin_history.py).
Historical rentals/payments are not registered.

Admin uses Django's existing access rules: an active staff account plus the
relevant `catalog.view_productcategory`, `add_productcategory`,
`change_productcategory` or `delete_productcategory` permission. A view permission
does not grant add/change/delete. An active staff superuser has all permissions.
Merely having `is_staff`, an Employee profile, or a particular `Account.status`
does not grant category CRUD rights. No new status vocabulary or admin account
type is introduced. Sensitive Account/Employee/Group Admin follows the separate
PR #7 policy described in accounts.md.

The standard admin form enforces required names, the 100-character limit and
uniqueness. Existing `PROTECT` relationships prevent deletion of categories used
by Product or CategoryAttribute, including through bulk delete. No forced delete
or alteration of historical records is added.

## Database and migration boundary

ProductCategory remains the canonical BIGINT ID plus unique VARCHAR(100) name.
The 21 domain models and 11 project migration files from PR #3 are retained.
In particular, do not copy PR #1's alternative `catalog.0001_initial`, add its
model Meta options, or replace the canonical catalogue tests with its four tests.
The canonical initial migration also creates AttributeDefinition.

The old PR #2/#4 models were not copied into PR #6. Account access was integrated
separately by PR #7, and product reads by PR #9, using the canonical schema.

## Verification in a disposable PostgreSQL environment

The PR #6 tests are `apps.catalog.test_category_api` and
`apps.catalog.test_category_admin`; the original 449 DB/auth test IDs remain.
Tests cover API responses, rejected mutations, Admin permissions, validation
and protected deletion. Execution results belong to the integration report;
the old four-test result from PR #1 is not a result for this adapted tree.

Use a dedicated PostgreSQL 17 server/database with its own credentials and port.
Set DB_NAME/DB_USER/DB_PASSWORD/DB_HOST/DB_PORT and Django environment variables
for that environment. Do not reuse the main `beri` database, its named volume,
or a populated database from another branch's alternative initial migrations.
The following commands are for that disposable environment only, from `backend`:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py migrate --plan
python manage.py migrate --noinput
python manage.py migrate --plan
python manage.py test apps.catalog.test_category_api apps.catalog.test_category_admin --noinput
python manage.py test apps.accounts apps.catalog apps.pickup_points apps.inventory apps.reservations apps.payments apps.rentals --noinput
```

`migrate` writes to the configured disposable database. The test runner creates
and drops a separate `test_<DB_NAME>` database on that dedicated server.
Neither command should target the main server or an existing shared volume.
The current README describes PostgreSQL setup. Do not substitute SQLite for these checks.

## S2 booking context (merged PR #15/#16)

- `POST /api/reservations/`: customer, product_id/pickup_point_id/request_id UUID;
  request UUID is the Reservation PK. Retry reuses the record; TTL60 minutes.
- `GET /api/reservations/`: own bookings; `POST /api/reservations/{uuid}/cancel/`:
  own active unexpired booking. Existing contracts are unchanged.
- `GET /api/employee/reservations/`: actual Employee of the point, active account
  and accounts.view_employee. Existing serializer includes customer phone within
  this authorized scope; new inspection output below does not include it.
- `python manage.py expire_reservations`: synchronous sweep; no scheduler added.

## S2-P4 pre-issue inspections and replacement

These endpoints are merged in PR #17, BERI-301-A/B.
Session authentication and CSRF remain enabled. An active Account of employee
type AND an Employee profile in the booking's stored pickup point are required.
Staff/superuser flags never bypass Employee or point checks. API code does not
require is_staff. Permission/profile/point are resolved from fresh database data
and repeated inside the write transaction; client employee/point claims are rejected.

| Method | Path | Django permissions |
|---|---|---|
| GET | `/api/employee/reservations/{uuid}/inspections/` | rentals.view_inspection |
| POST | same inspections URL | rentals.add_inspection |
| POST | `/api/employee/reservations/{uuid}/replace/` | rentals.add_inspection + rentals.add_inspectionproblem + reservations.change_reservation + reservations.add_reservationitemassignment |

GET returns reservation_id/status, initial_product_item_id, assigned_product_item_id,
expires_at, expected_assignment_id (null after closing), and inspections array.
It can read terminal history and performs no lifecycle writes. Its short header
lock prevents an inconsistent assignment/header snapshot; it does not subsequently
lock items. It supplies the expected ID before the first observation for S2-P6.

Healthy inspection POST:

```json
{
  "expected_assignment_id": 17,
  "condition_ok": true,
  "completeness_ok": true,
  "comment": "Проверено сотрудником",
  "problems": []
}
```

Problem inspection with atomic replacement POST:

```json
{
  "expected_assignment_id": 17,
  "condition_ok": false,
  "completeness_ok": true,
  "comment": null,
  "problems": [{"problem_type": "damage", "description": "Трещина корпуса"}],
  "reason": "Повреждение корпуса",
  "replacement_item_id": 42
}
```

replacement_item_id may be omitted/null for automatic lowest-ID eligible stock.
The item must be available, same Product/PV, never previously assigned in this
reservation, without open Assignment or active Rental. Busy/changing stock is409,
not automatic cancellation. No automatic retries or network calls under locks.

If no automatic candidate exists or decline_replacement=true, cancellation in
that same transaction requires explicit nonblank completion_reason<=50. Without
it,400 and complete rollback. An explicitly invalid/unavailable target is409,
even if completion_reason was supplied. decline_replacement and an explicit
replacement ID cannot be combined. No fallback cancels for a temporarily locked item.

reason is required, nonblank<=30, literal free text (no new enum), stored on the
new Assignment and on quarantined item's status_reason. Employee is recorded on
the Inspection/new Assignment. For employee cancellation it remains on Inspection;
the old assignment is closed without rewriting its original author/reason.

Problems: existing choices damage/malfunction/missing_component/other. Nonblank
description is required. missing_component additionally requires product_component_id
and missing_quantity>0; component must belong to this Product and summed quantities
for it in this inspection cannot exceed stored component quantity. Other types
must have both optional fields null/omitted. condition_ok iff no damage/malfunction/other;
completeness_ok iff no missing components. Healthy endpoint accepts no problems.
Unknown/server-controlled fields are400, not silently ignored. Comment is nullable
literal text; no trimming/casefolding defines a retry. Problem rows may repeat
within the approved aggregate bound. No penalty/debt is inferred from a problem.

Successful POST returns200 with inspection (explicit history fields/problems),
reservation_id/status, initial/assigned_product_item_id, assignment_id, sequence_no.
Inspection timestamps/actor/type/parent are server-owned. customer_confirmed_at
remains null, no Rental or issuance is created. The first inspection freezes the
existing product's component set via canonical guards.

One immutable observation per current assignment: same employee and exact same
healthy payload return the stored ID/time without another event; differing data,
an existing observation before attempted replacement, stale assignment or repeated
replacement give409. New assignments may be inspected again after replacement.
All writes require active status AND server T<expires_at after all row locks.
Expiry/terminal state returns409 without writes; no extension or auto-expiry here.

Lock order: Actor Account -> Employee -> Product -> PickupPoint -> Items ordered
by PK -> Reservation -> Assignment -> location histories. Product/point NO KEY
UPDATE pins components/parents, also serializing inspections of the same product
or point (a deliberate conservative throughput limit). Item locks use NOWAIT;
busy item is409. Thus replacement never locks Reservation and then the new item.
Server time is sampled after all waiting row locks. Supported ORM services guard
history; arbitrary raw SQL remains outside these cross-table invariants.

Existing cancel/expire retain their logic: only the final reserved item is freed,
old quarantined items stay unavailable. A close that pre-read an old pointer while
replacement won may refuse with a controlled conflict instead of modifying the
new assignment; refresh/reinvoke the caller or next expiry sweep is needed.

Errors:400 invalid payload/domain data,403 access/CSRF,404 unknown/foreign-PV
booking,409 lifecycle/stale/busy conflicts. Default DRF error shapes are retained;
auth's separate error envelope and global Admin policy are unchanged. API status
uses is_active, not an invented Account.status or Product/PV status vocabulary.

Implementation: [services](../backend/apps/rentals/services.py),
[API](../backend/apps/rentals/views.py), [tests](../backend/apps/rentals/test_pre_issue.py).
Checks, limitations and handoff: [S2-P4 report](s2-p4-report.md).

## S2-P6 employee Desktop consumer (BERI-206-B)

UI routes are `/employee/reservations` and `/employee/reservations/{uuid}`.
The list GET returns an array using the existing ReservationSerializer. The
screen keeps ID, product/PV/item IDs, status and expires_at. Customer phone is
discarded from screen state and is not rendered. History GET supplies the
current expected_assignment_id for every write, including the first inspection.

The healthy inspection uses condition_ok=true, completeness_ok=true and an
empty problems array. The minimal replacement form records one condition
problem (damage, malfunction or other), description and reason up to 30 chars.
Completeness is explicitly checked. Missing-component input, which requires
component IDs and quantities, is outside this minimal form. History can display
all problem descriptions returned by the server.

Replacement omits replacement_item_id for automatic server selection. An
unchecked cancellation consent omits completion_reason. No candidate then
produces the existing 400 rollback. Explicit consent includes the literal
`Нет свободной замены`. No candidate then cancels atomically. A busy/conflicting
item remains a 409 and does not imply cancellation.

The issue request in PR #19 is
`POST /api/employee/reservations/{uuid}/issue/` with only
`{"expected_assignment_id":17}`. First success is 201, exact replay is 200.
The Rental JSON includes id, reservation_id, product_item_id, status, started_at,
started_by_employee_id, tariff_id and hourly_rate_applied. The screen keeps only
id/status/started_at, shows successful handover/start and reloads history. The
list is fetched again when returning from details. Admission, actual timestamps,
financial conditions and all lifecycle writes belong to Django.

Session/CSRF use the existing shared client. A synchronous in-flight guard plus
disabled controls prevents a double click. Failed POST is never automatically
retried. After a conflict or an unknown result, only a GET refresh re-enables
actions. A recorded healthy inspection or fulfilled booking is reconciled from
history rather than replaying the write. HTTP 400, 401/403, 404, 409 and network
failures have explicit Russian messages. No server contract changes are made.
