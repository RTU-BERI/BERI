"""Подтверждение выдачи на существующих моделях и проверках S2-P4."""
from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, models, transaction
from django.utils import timezone

from apps.accounts.access import require_physical_access
from apps.accounts.models import Account
from apps.catalog.models import ProductTariff
from apps.inventory.models import ProductItem
from apps.payments.models import Debt, PaymentMethod
from apps.reservations.models import Reservation, ReservationItemAssignment
from apps.reservations.services import ReservationConflict, _set_item_status, is_reservation_active
from .models import Rental
from .services import _database_conflict, _existing_inspection, _locked_booking, _source

ISSUE_PERMISSIONS = (
    "rentals.add_rental", "reservations.change_reservation",
    "reservations.change_reservationitemassignment", "inventory.change_productitem",
)


# Возвращает результат той же выдачи, не проверяя заново первичные условия.
def _replayed_rental(rental, employee, expected_assignment_id):
    reservation = Reservation.objects.select_for_update().get(pk=rental.reservation_id)
    require_physical_access(employee.account, reservation.pickup_point_id, ISSUE_PERMISSIONS)
    rental = Rental.objects.get(pk=rental.pk)
    assignment = ReservationItemAssignment.objects.filter(reservation=reservation).order_by("-sequence_no").first()
    if (reservation.status != Reservation.Status.FULFILLED
            or rental.started_by_employee_id != employee.pk
            or assignment is None or assignment.pk != expected_assignment_id
            or assignment.product_item_id != rental.product_item_id
            or reservation.assigned_product_item_id != rental.product_item_id
            or assignment.released_at != rental.started_at
            or reservation.completed_at != rental.started_at):
        raise ReservationConflict("Выдача уже выполнена с другим сотрудником или назначением.")
    # После Reservation не берём Item: повтор ничего не меняет и не инвертирует порядок locks.
    return rental


# Проверяет допуск и атомарно начинает одну аренду либо возвращает точный повтор.
def start_rental(*, actor_account_id, reservation_id, expected_assignment_id):
    try:
        with transaction.atomic():
            employee, snapshot, product = _source(
                actor_account_id, reservation_id, expected_assignment_id, ISSUE_PERMISSIONS)
            # Общие Product/ПВ locks сериализуют выдачи ещё до проверки отсутствия Rental.
            existing = Rental.objects.filter(reservation_id=reservation_id).first()
            if existing is not None:
                return _replayed_rental(existing, employee, expected_assignment_id), False

            customer_id = Reservation.objects.values_list("customer_account_id", flat=True).get(pk=reservation_id)
            # Customer до Item, как при бронировании; NOWAIT не создаёт обратного ожидания.
            customer = Account.objects.select_for_update(no_key=True, nowait=True).get(pk=customer_id)
            # После бронирования клиент мог быть деактивирован или сменить тип аккаунта.
            if not customer.is_active or customer.account_type != Account.AccountType.CUSTOMER:
                raise ReservationConflict("Аккаунт клиента недоступен для выдачи.")
            try:
                reservation, assignment, item, _, _ = _locked_booking(
                    reservation_id, snapshot, expected_assignment_id, [snapshot["assigned_product_item_id"]])
            except ValidationError as error:
                raise ReservationConflict("Состояние экземпляра не согласовано с бронью.") from error
            if reservation.customer_account_id != customer.pk:
                raise ReservationConflict("Клиент брони изменился. Обновите данные.")

            method = PaymentMethod.objects.select_for_update(no_key=True).filter(
                customer_account=customer, is_default=True, status=PaymentMethod.Status.ACTIVE,
                verification_status=PaymentMethod.VerificationStatus.CONFIRMED,
            ).first()
            if method is None:
                raise ReservationConflict("Нет допустимого платежного средства по умолчанию.")
            try:
                method.full_clean()
            except ValidationError as error:
                raise ReservationConflict("Платежное средство не прошло проверку данных.") from error

            lookup_time = timezone.now()
            tariff = ProductTariff.objects.select_for_update(no_key=True).filter(
                models.Q(valid_to__isnull=True) | models.Q(valid_to__gt=lookup_time),
                product=product, valid_from__lte=lookup_time,
            ).first()
            # Все ожидающие row locks получены: срок и тариф проверяются на фактический момент.
            require_physical_access(employee.account, reservation.pickup_point_id, ISSUE_PERMISSIONS)
            now = timezone.now()
            if not is_reservation_active(reservation, at=now):
                raise ReservationConflict("Бронь завершена или просрочена.")
            if tariff is None or not (tariff.valid_from <= now and (tariff.valid_to is None or now < tariff.valid_to)):
                raise ReservationConflict("На момент выдачи нет действующего тарифа. Обновите данные.")
            inspection = _existing_inspection(reservation, assignment, item, now)
            if (inspection is None or not inspection.condition_ok or not inspection.completeness_ok
                    or inspection.problems.exists()):
                raise ReservationConflict("Нужен положительный осмотр текущего назначения.")
            # Customer lock также должен использоваться будущими сервисами изменения долгов/default.
            if Debt.objects.filter(rental__reservation__customer_account=customer, status=Debt.Status.OPEN).exists():
                raise ReservationConflict("У клиента есть открытая задолженность.")

            # Фиксируем ставку и одно время события; окончание и стоимость здесь не определяются.
            rental = Rental(reservation=reservation, product_item=item, payment_method=method,
                tariff=tariff, hourly_rate_applied=tariff.hourly_rate,
                started_by_employee=employee, started_at=now, status=Rental.Status.ACTIVE)
            models.Model.save(rental, force_insert=True)
            reservation.status = Reservation.Status.FULFILLED
            reservation.completed_at = now
            models.Model.save(reservation, update_fields=("status", "completed_at"))
            assignment.released_at = now
            models.Model.save(assignment, update_fields=("released_at",))
            _set_item_status(item, ProductItem.Status.RENTED, now)
            return rental, True
    except (OperationalError, IntegrityError) as error:
        # Конфликт разбирается после отката всей транзакции, а не после частичной выдачи.
        _database_conflict(error)
