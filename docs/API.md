# BERI API — current public contracts

Updated 2026-11-03 against develop `c018bb4a3371f3cc14bf7d8ae6524a6da3343f9b` (merged PR #8–13). The unpublished `fix/s1-audit-closure` adds controlled category-query validation; success payloads and endpoint paths are unchanged. Verification and limits: [audit closure](s1-audit-closure-report.md).

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
tariff or component write API is added. Booking/rental/payment APIs remain future work.

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
