# Account authentication, permissions and protected administration

This adapts Kamil's [PR #2](https://github.com/RTU-BERI/BERI/pull/2) to the
canonical database from PR #3, after the category integration in PR #6.
The owner approved policies Q1–Q4: current `is_active` controls access;
sensitive administration requires an active staff superuser; existing Employee
bindings cannot be changed in this Admin; API capabilities use explicit Django
permissions without an additional `is_staff` requirement.

## Canonical database boundary

All 21 domain models, fields, managers, validators, constraints, indexes,
history protections and 11 project migration files remain unchanged.
No new migrations or dependencies are required. PostgreSQL and the existing
psycopg dependency are retained. Do not replace them with PR #2's earlier
SQLite settings, models, initial migrations or `phonenumbers` dependency.

`Account.account_type` remains `customer` or `employee`. There is no third
`admin` type. An administrator uses Django's standard flags and permissions.
An Employee profile remains a distinct canonical record tied to an Account
and a pickup point (PV).

`Account.status` remains an explicitly supplied, nonempty opaque string of up
to 20 characters. There is no status enum, default, status SQL CHECK or automatic
status/is_active synchronization. Writing `status="blocked"` alone does **not**
block access. To block an account, set `is_active=False` through an authorized
management channel.

Phone normalization is the existing canonical algorithm: remove whitespace,
hyphens and parentheses, then require `+` and 2–15 ASCII digits with a nonzero
first digit. No country inference, replacement of a national prefix, metadata
validation or proof of phone ownership is added.

## Password and session authentication

`AUTHENTICATION_BACKENDS` selects `apps.accounts.backends.AccountBackend`, a
`ModelBackend` subclass using the unchanged AccountManager's natural-key lookup.
Both synchronous and asynchronous authentication use that same phone contract.
Passwords use Django's existing hashers; passwordless accounts keep their
canonical unusable-password behavior. Missing or incorrect credentials do not
authenticate. After password verification, the backend rechecks the stored
active flag, phone and password hash before returning an account.

Session restoration reads a current active Account from the database. API access
helpers also reread the Account rather than trusting a cached session object,
role claim or permission cache. Deactivation and permission revocation therefore
affect subsequent checks. These reads are snapshots, not a promise to stop a
business write already executing under a separate transaction.

**Existing sessions stored with `django.contrib.auth.backends.ModelBackend`
require a new login after this backend-path change.** There is no fallback that
retains the previous backend path. New sessions use AccountBackend; changing
a password retains Django's normal session-hash invalidation.

This change adds no public registration, password-reset, login/token, JWT or SMS
endpoint. It does not implement phone verification, rate limiting or a full
production identity-provider integration. Django Admin keeps its standard login
form and CSRF protection. Global DRF authentication/permission defaults are not
changed. The category API remains public with its own explicit read-only policy.

## Sensitive Django Admin

Account, Employee and Group Admin require a **current** Account with all three
flags: `is_active=True`, `is_staff=True`, `is_superuser=True`. A staff user with
every model permission still cannot use those sensitive sections. The rule covers
lists, details, add/change/delete, history, password changes and bulk actions.

Privileged routes hold a database lock on the actor Account for their transaction
and reread its flags after acquiring it. Known target Accounts are locked in
UUID order. This serializes these Admin operations against concurrent changes
of the actor's flags and protects Account-type checks against canonical Employee
creation, which locks the same Account row. Standard password and group/M2M
writes remain inside the guarded route transaction. This is an Admin-layer
contract, not a new global ORM policy.

Account forms require explicit `account_type` and `status`; form-level behavior
does not add model defaults. Standard Django password forms validate and hash
passwords. Hash display is read-only, system dates and phone verification time
are read-only, and password changes use UserAdmin's dedicated password route.
Crafted requests cannot grant ordinary staff access to privileged fields.

If an Account has an Employee, this Admin rejects a change of its account type.
The check is repeated against fresh stored data under the Account lock. It does
not pretend to alter the unchanged model/manager's behavior in arbitrary Python
or direct SQL code.

Only the trusted administrator can create an Employee. **Both `account` and
`pickup_point` are immutable after creation through this Admin**, including
crafted POST attempts. In particular, an existing Employee cannot be assigned to
a different person: historical Inspection, Rental and Assignment references keep
their meaning. A process for transfers between pickup points is deferred.

Canonical `PROTECT` relationships still govern deletion. No historical model is
registered for writes, and no forced deletion or new historical write service is
introduced. The catalogue's ProductCategory Admin keeps its existing staff plus
model-permission policy unchanged; the stricter Account/Employee/Group policy
does not replace it.

## Reusable API access building blocks

These helpers authorize operations only when attached correctly by trusted
server code. They do not publish new business endpoints or silently secure
existing/future views that do not use them.

| Component | Contract |
|---|---|
| `get_active_account(user)` | Current stored Account with `is_active=True`, ignoring stale flags/cache. |
| `get_employee(user)` | Current active employee-type Account with an existing Employee and its actual PV. |
| `has_permissions(user, permissions)` | Explicit nonempty collection of `app_label.codename` strings; current Django permissions, no staff requirement. |
| `require_physical_access(user, pickup_point_id, permissions)` | Employee, required permissions and exact PV match; target PV must be resolved from trusted stored objects. |
| `IsClient` | Current active customer; an Employee or superuser flag alone is insufficient. |
| `HasDjangoPermissions` | Generic capability check, not a distinct Admin account type or a staff test. |
| `IsClientOwner` + `ClientOwnedQuerysetMixin` | Filter list/detail querysets by owner and independently check the stored owner of each object. |
| `CanPerformPhysicalOperation` + `EmployeePickupPointQuerysetMixin` | Required permissions and PV-filtered queryset/object checks, including for superusers. |

Mixin classes go before the DRF generic view class. Views declare a class queryset
and a server-owned `owner_lookup` or `pickup_point_lookup` containing actual ORM
field names. Missing or malformed configuration fails closed. Lookup operators
and transforms are not accepted as field paths.

For the existing canonical Reservation, the ownership path is
`customer_account_id`, and the PV path is `pickup_point_id`. For a Debt, the
owner path would be `rental__reservation__customer_account_id`. These are examples
for future read views, not new endpoints or permission grants.

Unsafe physical requests additionally implement `get_physical_pickup_point_id()`
using an already scoped stored target. A PV/owner/account ID from request data is
never evidence of authorization. Object checks alone cannot filter a list, so
the queryset mixin is mandatory. A create service must assign ownership from the
fresh authorized Account, not accept ownership from a client payload.

Superusers retain ordinary Django permission semantics, but **do not bypass
Client ownership or Employee/PV scope**. Having a permission without staff is
enough for the generic API capability helper; it is not enough to enter sensitive
Django Admin. Missing permissions fail closed even for a superuser.

## Transaction and scope limits

Access helpers reread stored state, but do not retain locks across an arbitrary
business write. Wrapping a later write in `atomic()` alone does not eliminate
the interval between authorization and mutation. Future issue/return/reservation
services must reauthorize inside their transaction and share an explicit lock
protocol with changes of roles, profiles, permissions and operation targets.
No such business write service is introduced here.

The unchanged canonical ORM can still be called by trusted internal Python.
The Admin's immutable Employee bindings are not a new global ORM restriction.
Raw SQL and external application code remain outside these Backend guarantees.

## Verification

Run checks and tests only with environment variables targeting a **dedicated
disposable PostgreSQL server** with its own credentials, port and storage.
Neither the main `beri` database nor existing/shared volumes may be used.
From `backend`, in that isolated environment:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py migrate --plan
python manage.py migrate --noinput
python manage.py migrate --plan
python manage.py test --noinput
```

The test runner creates and drops `test_<DB_NAME>` on that same isolated server.
Fresh migration replay is required: SQLite cannot verify PostgreSQL EXCLUDE,
partial indexes or locking behavior. The main environment's older README setup
instructions are not instructions to apply migrations to a shared database.

New authentication, API permissions, Admin and security tests live in separate
`apps.accounts.test_*` modules. All 483 previous test IDs are retained. Exactly
two existing structural tests were adapted with explicit owner approval:
they now require protected Account/Employee/Group Admin and permit the new forms
import, while retaining the model, migration, no-database-import and login
assertions. Their previous absence-of-Account-Admin assumption no longer
describes the approved Backend functionality. Other old tests are unchanged.

Actual run counts and results belong to the integration report. Historical
72-test SQLite results from PR #2 are not verification of this adapted tree.
