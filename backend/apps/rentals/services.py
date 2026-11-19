"""Pre-issue observations and atomic replacement; no issuance or Rental writes."""

from collections import Counter
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, OperationalError, models, transaction
from django.utils import timezone

from apps.accounts.access import require_physical_access
from apps.accounts.models import Account, Employee
from apps.catalog.models import Product, ProductComponent
from apps.inventory.models import ProductItem, ProductItemLocationHistory, _location_state
from apps.pickup_points.models import PickupPoint
from apps.reservations.models import Reservation, ReservationItemAssignment
from apps.reservations.services import (
    ReservationConflict, _set_item_status, is_reservation_active,
)
from .models import Inspection, InspectionProblem, Rental

INSPECT_PERMISSIONS = ("rentals.add_inspection",)
REPLACE_PERMISSIONS = INSPECT_PERMISSIONS + (
    "rentals.add_inspectionproblem", "reservations.change_reservation",
    "reservations.add_reservationitemassignment",
)


def _positive_id(value, field):
    if type(value) is not int or not 1 <= value <= (1 << 63) - 1:
        raise ValidationError({field: "Укажите положительный BIGINT ID."})


def _text(value, field, limit=None):
    if not isinstance(value, str) or not value.strip() or (limit and len(value) > limit):
        raise ValidationError({field: "Укажите непустой текст допустимой длины."})


def _observation(product_id, condition_ok, completeness_ok, comment, problems):
    if type(condition_ok) is not bool or type(completeness_ok) is not bool:
        raise ValidationError("Результаты осмотра должны быть boolean.")
    if comment is not None and not isinstance(comment, str):
        raise ValidationError({"comment": "Ожидается текст или null."})
    if not isinstance(problems, list):
        raise ValidationError({"problems": "Ожидается список проблем."})
    missing = Counter()
    normalized = []
    for problem in problems:
        if not isinstance(problem, dict) or set(problem) - {
            "problem_type", "description", "product_component_id", "missing_quantity"
        }:
            raise ValidationError({"problems": "Неверные поля проблемы."})
        kind, description = problem.get("problem_type"), problem.get("description")
        if kind not in InspectionProblem.Type.values:
            raise ValidationError({"problem_type": "Неизвестный тип проблемы."})
        _text(description, "description")
        component_id, quantity = problem.get("product_component_id"), problem.get("missing_quantity")
        if kind == InspectionProblem.Type.MISSING_COMPONENT:
            _positive_id(component_id, "product_component_id")
            _positive_id(quantity, "missing_quantity")
            missing[component_id] += quantity
        elif component_id is not None or quantity is not None:
            raise ValidationError("Компонент/количество допустимы только для некомплектности.")
        normalized.append(dict(problem_type=kind, description=description,
                               product_component_id=component_id, missing_quantity=quantity))
    components = dict(ProductComponent.objects.filter(
        product_id=product_id, pk__in=missing
    ).values_list("pk", "quantity"))
    if any(pk not in components or amount > components[pk] for pk, amount in missing.items()):
        raise ValidationError({"missing_quantity": "Компонент чужой либо превышено количество комплектации."})
    condition = not any(p["problem_type"] != "missing_component" for p in normalized)
    complete = not missing
    if condition_ok != condition or completeness_ok != complete:
        raise ValidationError("Флаги осмотра не соответствуют списку проблем.")
    return normalized


def _source(actor_account_id, reservation_id, expected_assignment_id, permissions):
    if not isinstance(actor_account_id, UUID) or not isinstance(reservation_id, UUID):
        raise PermissionDenied("Нужен удостоверенный сотрудник и UUID брони.")
    _positive_id(expected_assignment_id, "expected_assignment_id")
    actor = Account.objects.select_for_update(no_key=True).filter(pk=actor_account_id).first()
    if actor is None:
        raise PermissionDenied("Аккаунт сотрудника недоступен.")
    employee = Employee.objects.select_for_update().filter(account_id=actor.pk).first()
    if employee is None:
        raise PermissionDenied("Нужен профиль сотрудника.")
    snapshot = Reservation.objects.filter(pk=reservation_id).values(
        "pickup_point_id", "assigned_product_item_id", "assigned_product_item__product_id"
    ).first()
    if snapshot is None:
        raise ReservationConflict("Бронирование не найдено.")
    require_physical_access(actor, snapshot["pickup_point_id"], permissions)
    # NO KEY UPDATE is compatible with ordinary FK key-share reads. It pins
    # the product/components and serializes inspections of the same product.
    product = Product.objects.select_for_update(no_key=True).filter(
        pk=snapshot["assigned_product_item__product_id"]
    ).first()
    if product is None:
        raise ReservationConflict("Не удалось подтвердить товар.")
    PickupPoint.objects.select_for_update(no_key=True).get(pk=snapshot["pickup_point_id"])
    return employee, snapshot, product


def _locked_booking(reservation_id, snapshot, expected_assignment_id, item_ids):
    # Never lock Reservation before either item. NOWAIT means a busy candidate
    # is a conflict, never a false claim that no replacement stock exists.
    items = {item.pk: item for item in ProductItem.objects.select_for_update(nowait=True)
             .filter(pk__in=item_ids).order_by("pk")}
    reservation = Reservation.objects.select_for_update().get(pk=reservation_id)
    assignments = list(ReservationItemAssignment.objects.select_for_update()
                       .filter(reservation=reservation).order_by("sequence_no"))
    histories_by_item = {
        item_id: list(ProductItemLocationHistory.objects.select_for_update()
                      .filter(product_item_id=item_id).order_by("valid_from", "pk"))
        for item_id in sorted(item_ids)
    }
    # Every potentially waiting row lock precedes the deadline decision.
    now = timezone.now()
    if (reservation.assigned_product_item_id != snapshot["assigned_product_item_id"]
            or reservation.pickup_point_id != snapshot["pickup_point_id"]
            or not is_reservation_active(reservation, at=now)):
        raise ReservationConflict("Бронь завершена, просрочена или назначение изменилось.")
    if not assignments or assignments[-1].pk != expected_assignment_id:
        raise ReservationConflict("Назначение устарело. Обновите бронь.")
    current = assignments[-1]
    if (current.product_item_id != reservation.assigned_product_item_id
            or current.released_at is not None or current.assigned_at > now
            or assignments[0].product_item_id != reservation.initial_product_item_id
            or [a.sequence_no for a in assignments] != list(range(1, len(assignments) + 1))
            or any(a.released_at is None for a in assignments[:-1])):
        raise ReservationConflict("История назначений не согласована с бронью.")
    for item_id in item_ids:
        item = items.get(item_id)
        if (item is None or item.product_id != snapshot["assigned_product_item__product_id"]
                or item.pickup_point_id != reservation.pickup_point_id
                or Rental.objects.filter(product_item_id=item_id, status=Rental.Status.ACTIVE).exists()):
            raise ReservationConflict("Экземпляр изменился или не соответствует брони.")
        _location_state(item, histories_by_item[item_id], now)
    old = items[reservation.assigned_product_item_id]
    if old.status != ProductItem.Status.RESERVED:
        raise ReservationConflict("Текущий экземпляр не зарезервирован.")
    open_rows = list(ReservationItemAssignment.objects.filter(
        product_item_id=old.pk, released_at__isnull=True
    ).values_list("pk", flat=True))
    if open_rows != [current.pk]:
        raise ReservationConflict("Открытое назначение экземпляра не согласовано.")
    return reservation, current, old, items, now


def _existing_inspection(reservation, assignment, old, now):
    rows = list(Inspection.objects.filter(
        reservation=reservation, product_item=old, inspection_type=Inspection.Type.PRE_ISSUE,
        inspected_at__gte=assignment.assigned_at,
    ).order_by("pk"))
    if len(rows) > 1 or any(row.inspected_at > now for row in rows):
        raise ReservationConflict("История осмотра не согласована.")
    return rows[0] if rows else None


def _insert_observation(reservation, employee, old, now, condition_ok, completeness_ok, comment, problems):
    inspection = Inspection(product_item=old, employee=employee,
                            inspection_type=Inspection.Type.PRE_ISSUE,
                            reservation=reservation, condition_ok=condition_ok,
                            completeness_ok=completeness_ok, comment=comment, inspected_at=now)
    models.Model.save(inspection, force_insert=True)
    for fields in problems:
        models.Model.save(InspectionProblem(inspection=inspection, **fields), force_insert=True)
    return inspection


def _database_conflict(error):
    code = getattr(error.__cause__, "sqlstate", None)
    if code in {"55P03", "40P01", "23505"}:
        raise ReservationConflict("Экземпляр занят либо операция конфликтует. Обновите данные.") from error
    raise error


def record_pre_issue(*, actor_account_id, reservation_id, expected_assignment_id,
                     condition_ok, completeness_ok, comment=None, problems=None):
    """One healthy observation per current assignment; exact own retries are safe."""
    problems = [] if problems is None else problems
    try:
        with transaction.atomic():
            employee, snapshot, product = _source(
                actor_account_id, reservation_id, expected_assignment_id, INSPECT_PERMISSIONS)
            normalized = _observation(product.pk, condition_ok, completeness_ok, comment, problems)
            if not condition_ok or not completeness_ok:
                raise ValidationError("Проблемный осмотр отправьте вместе с решением о замене/отмене.")
            reservation, assignment, old, _, now = _locked_booking(
                reservation_id, snapshot, expected_assignment_id, [snapshot["assigned_product_item_id"]])
            require_physical_access(employee.account, reservation.pickup_point_id, INSPECT_PERMISSIONS)
            existing = _existing_inspection(reservation, assignment, old, now)
            if existing is not None:
                if (existing.employee_id != employee.pk or existing.condition_ok != condition_ok
                        or existing.completeness_ok != completeness_ok or existing.comment != comment
                        or existing.customer_confirmed_at is not None or existing.problems.exists()):
                    raise ReservationConflict("Осмотр уже сохранён с другими данными.")
                return existing, reservation, assignment
            inspection = _insert_observation(reservation, employee, old, now,
                                             condition_ok, completeness_ok, comment, normalized)
            return inspection, reservation, assignment
    except (OperationalError, IntegrityError) as error:
        _database_conflict(error)


def replace_after_inspection(*, actor_account_id, reservation_id, expected_assignment_id,
                             condition_ok, completeness_ok, reason, comment=None, problems=None,
                             replacement_item_id=None, decline_replacement=False, completion_reason=None):
    """Record problems and quarantine+replace/cancel atomically; stale replay is 409."""
    problems = [] if problems is None else problems
    _text(reason, "reason", 30)
    if type(decline_replacement) is not bool:
        raise ValidationError({"decline_replacement": "Ожидается boolean."})
    if completion_reason is not None:
        _text(completion_reason, "completion_reason", 50)
    if replacement_item_id is not None:
        _positive_id(replacement_item_id, "replacement_item_id")
    if decline_replacement and replacement_item_id is not None:
        raise ValidationError("При отказе от замены не указывайте новый экземпляр.")
    try:
        with transaction.atomic():
            employee, snapshot, product = _source(
                actor_account_id, reservation_id, expected_assignment_id, REPLACE_PERMISSIONS)
            normalized = _observation(product.pk, condition_ok, completeness_ok, comment, problems)
            if condition_ok and completeness_ok:
                raise ValidationError("Для замены необходим проблемный осмотр.")
            history_ids = ReservationItemAssignment.objects.filter(
                reservation_id=reservation_id).values_list("product_item_id", flat=True)
            candidates = ProductItem.objects.filter(product_id=product.pk,
                pickup_point_id=snapshot["pickup_point_id"], status=ProductItem.Status.AVAILABLE
            ).exclude(pk__in=history_ids).exclude(
                pk__in=ReservationItemAssignment.objects.filter(released_at__isnull=True).values("product_item_id")
            ).exclude(pk__in=Rental.objects.filter(status=Rental.Status.ACTIVE).values("product_item_id"))
            if decline_replacement:
                candidate_id = None
            elif replacement_item_id is not None:
                candidate_id = candidates.filter(pk=replacement_item_id).values_list("pk", flat=True).first()
                if candidate_id is None:
                    raise ReservationConflict("Выбранный экземпляр недоступен для замены.")
            else:
                candidate_id = candidates.order_by("pk").values_list("pk", flat=True).first()
            ids = [snapshot["assigned_product_item_id"]]
            if candidate_id is not None:
                ids.append(candidate_id)
            reservation, assignment, old, items, now = _locked_booking(
                reservation_id, snapshot, expected_assignment_id, ids)
            require_physical_access(employee.account, reservation.pickup_point_id, REPLACE_PERMISSIONS)
            if _existing_inspection(reservation, assignment, old, now) is not None:
                raise ReservationConflict("Результат осмотра этого назначения уже зафиксирован.")
            new = items.get(candidate_id)
            if new is not None and (new.status != ProductItem.Status.AVAILABLE
                    or ReservationItemAssignment.objects.filter(product_item=new, released_at__isnull=True).exists()
                    or ReservationItemAssignment.objects.filter(reservation=reservation, product_item=new).exists()):
                raise ReservationConflict("Новый экземпляр уже занят или использовался в этой брони.")
            if new is None and completion_reason is None:
                raise ValidationError({"completion_reason": "Для отмены необходима явная причина."})
            inspection = _insert_observation(reservation, employee, old, now,
                                             condition_ok, completeness_ok, comment, normalized)
            _set_item_status(old, ProductItem.Status.UNAVAILABLE, now)
            old.status_reason = reason
            models.Model.save(old, update_fields=("status_reason",))
            assignment.released_at = now
            models.Model.save(assignment, update_fields=("released_at",))
            if new is not None:
                next_assignment = ReservationItemAssignment(reservation=reservation,
                    product_item=new, sequence_no=assignment.sequence_no + 1,
                    assigned_by_employee=employee, reason=reason, assigned_at=now)
                models.Model.save(next_assignment, force_insert=True)
                _set_item_status(new, ProductItem.Status.RESERVED, now)
                reservation.assigned_product_item_id = new.pk
                models.Model.save(reservation, update_fields=("assigned_product_item",))
                return inspection, reservation, next_assignment
            reservation.status = Reservation.Status.CANCELLED
            reservation.completion_reason = completion_reason
            reservation.cancelled_at = reservation.completed_at = now
            models.Model.save(reservation, update_fields=(
                "status", "completion_reason", "cancelled_at", "completed_at"))
            return inspection, reservation, assignment
    except (OperationalError, IntegrityError) as error:
        _database_conflict(error)
