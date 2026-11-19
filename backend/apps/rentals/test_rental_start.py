"""Проверки выдачи через реальные сервисы и PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier, Event
from unittest.mock import patch
from uuid import uuid4
from django.contrib.auth.models import Permission
from django.db import close_old_connections, connection, models, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import Account, Employee
from apps.catalog.models import ProductTariff
from apps.inventory.models import ProductItem, ProductItemLocationHistory
from apps.payments.models import Debt, PaymentMethod
from apps.reservations.models import Reservation, ReservationItemAssignment
from apps.reservations.services import ReservationConflict, cancel_reservation, expire_reservation
from .issuance import ISSUE_PERMISSIONS, start_rental
from .models import Inspection, InspectionProblem, Rental
from .services import record_pre_issue, replace_after_inspection
from .test_pre_issue import InspectionFixtures
from .test_support import FixtureMixin as HistoryFixtures, raw_insert


# Дополняет существующие фикстуры осмотра тарифом и допустимым средством без marker-проверок.
class RentalStartFixtures(InspectionFixtures):
    # Готовит обычную действующую бронь, не создавая Rental заранее.
    def setUp(self):
        super().setUp()
        self.grant(self.actor)
        self.method = self.make_payment_method(self.customer)
        self.tariff = ProductTariff.objects.create(product=self.product,
            hourly_rate=Decimal("125.50"), valid_from=timezone.now() - timedelta(days=1))
        self.inspection = record_pre_issue(**self.healthy())[0]

    # Выдаёт существующие Django permissions без изменения политики доступа.
    def grant(self, actor):
        for name in ISSUE_PERMISSIONS:
            app, code = name.split(".")
            actor.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=code))

    # Создаёт только тестовую запись через уже существующий SQL-helper фикстур.
    def make_payment_method(self, customer):
        return raw_insert(PaymentMethod, id=uuid4(), customer_account=customer,
            external_id=f"opaque-fixture-{uuid4()}", is_default=True, status="active",
            verification_status="confirmed", created_at=timezone.now())

    # Вызывает сервис с исходным назначением, позволяя проверить устаревший запрос.
    def start(self, **changes):
        args = dict(actor_account_id=self.actor.pk, reservation_id=self.booking.pk,
                    expected_assignment_id=self.assignment.pk)
        args.update(changes)
        return start_rental(**args)

    # Меняет специально подготовленные фикстуры, не добавляя прикладного writer-сервиса.
    def write(self, obj, **changes):
        for key, value in changes.items():
            setattr(obj, key, value)
        models.Model.save(obj, update_fields=tuple(changes))

    # Снимок позволяет заметить любые частичные изменения при отказе.
    def snapshot(self):
        tables = (Reservation, ReservationItemAssignment, ProductItem, ProductItemLocationHistory,
                  Inspection, InspectionProblem, Rental, PaymentMethod, Debt, ProductTariff)
        return {m._meta.db_table: list(m.objects.order_by("pk").values()) for m in tables}

    # Проверяет ожидаемый конфликт и отсутствие побочных записей.
    def reject(self, action=None, error=ReservationConflict):
        before = self.snapshot()
        with self.assertRaises(error):
            (action or self.start)()
        self.assertEqual(self.snapshot(), before)

    # Убирает только тестовый осмотр для сценария его отсутствия или последующей замены.
    def remove_inspection(self):
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM inspection WHERE id=%s", [self.inspection.pk])

    # Создаёт другого уполномоченного сотрудника того же ПВ.
    def another_actor(self):
        actor = self.make_customer(account_type="employee")
        Employee.objects.create(account=actor, pickup_point=self.point)
        self.grant(actor)
        return actor


# Проверяет бизнес-допуск, историю и атомарность начала аренды.
class RentalStartServiceTests(RentalStartFixtures, TestCase):
    # Подтверждает все связи, единое время и отсутствие выдуманного окончания/подтверждения клиента.
    def test_success_sets_consistent_state_and_preserves_inspection(self):
        history = list(Inspection.objects.values())
        rental, created = self.start()
        self.booking.refresh_from_db()
        self.assignment.refresh_from_db()
        self.item.refresh_from_db()
        self.assertTrue(created)
        self.assertEqual((rental.status, self.booking.status, self.item.status), ("active", "fulfilled", "rented"))
        self.assertEqual((rental.product_item_id, rental.payment_method_id, rental.tariff_id),
                         (self.item.pk, self.method.pk, self.tariff.pk))
        self.assertEqual(rental.started_by_employee_id, self.employee.pk)
        self.assertEqual(rental.hourly_rate_applied, Decimal("125.50"))
        self.assertEqual(rental.started_at, self.booking.completed_at)
        self.assertEqual(rental.started_at, self.assignment.released_at)
        self.assertEqual(rental.started_at, self.item.status_changed_at)
        self.assertIsNone(rental.ended_at)
        self.assertIsNone(rental.ended_by_employee_id)
        self.assertIsNone(rental.final_amount)
        self.assertEqual(list(Inspection.objects.values()), history)
        self.assertIsNone(Inspection.objects.get().customer_confirmed_at)

    # Деактивация после бронирования запрещает выдачу без частичных изменений.
    def test_customer_deactivated_after_booking_cannot_start_rental(self):
        self.customer.is_active = False
        self.customer.save(update_fields=("is_active",))
        self.reject()

    # Новый экземпляр требует собственного осмотра, старые события сохраняются.
    def test_issue_after_replacement_uses_new_inspection_and_keeps_history(self):
        self.remove_inspection()
        candidate = self.make_item()
        _, _, current = replace_after_inspection(**self.bad(replacement_item_id=candidate.pk))
        self.reject()
        self.reject(lambda: self.start(expected_assignment_id=current.pk))
        record_pre_issue(**{**self.healthy(), "expected_assignment_id": current.pk})
        old_assignment = ReservationItemAssignment.objects.get(pk=self.assignment.pk)
        inspections = list(Inspection.objects.order_by("pk").values())
        rental, _ = self.start(expected_assignment_id=current.pk)
        self.assertEqual(rental.product_item_id, candidate.pk)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "unavailable")
        self.assertEqual(ReservationItemAssignment.objects.get(pk=old_assignment.pk).released_at, old_assignment.released_at)
        self.assertEqual(list(Inspection.objects.order_by("pk").values()), inspections)

    # Истёкший TTL и отмена не допускают новую выдачу, включая точную границу времени.
    def test_expired_boundary_and_cancelled_booking_are_rejected(self):
        with patch("apps.rentals.issuance.timezone.now", return_value=self.booking.expires_at):
            self.reject()
        cancel_reservation(self.booking.pk)
        self.reject()

    # Неисправность, некомплектность и осмотр до назначения не дают положительного допуска.
    def test_missing_or_invalid_inspection_is_rejected(self):
        cases = [{"condition_ok": False}, {"completeness_ok": False},
                 {"inspected_at": self.assignment.assigned_at - timedelta(seconds=1)},
                 {"inspected_at": self.booking.expires_at + timedelta(days=1)}]
        for changes in cases:
            with self.subTest(changes=changes), transaction.atomic():
                self.write(self.inspection, **changes)
                self.reject()
                transaction.set_rollback(True)
        with transaction.atomic():
            raw_insert(InspectionProblem, inspection=self.inspection, problem_type="damage", description="Fixture crack")
            self.reject()
            transaction.set_rollback(True)
        self.remove_inspection()
        self.reject()

    # Устаревший ID, закрытое назначение и неверное состояние Item отклоняются без записей.
    def test_stale_assignment_and_item_states_are_rejected(self):
        self.reject(lambda: self.start(expected_assignment_id=self.assignment.pk + 999))
        for state in ("available", "unavailable", "rented", "retired"):
            with self.subTest(state=state), transaction.atomic():
                self.write(self.item, status=state)
                self.reject()
                transaction.set_rollback(True)
        self.write(self.assignment, released_at=timezone.now())
        self.reject()

    # Проверяется модель средства, а не соглашение о префиксе его идентификатора.
    def test_payment_admission_uses_owner_flags_and_model_validation(self):
        cases = [dict(is_default=False), dict(customer_account_id=self.other_customer.pk),
                 dict(is_default=False, verification_status="pending"),
                 dict(is_default=False, verification_status="failed"),
                 dict(is_default=False, status="inactive", deactivated_at=timezone.now()),
                 dict(external_id=" ")]
        for changes in cases:
            with self.subTest(changes=changes), transaction.atomic():
                self.write(self.method, **changes)
                self.reject()
                transaction.set_rollback(True)
        self.write(self.method, external_id="opaque-provider-id-without-a-test-prefix")
        self.assertTrue(self.start()[1])

    # Открытый долг своего клиента блокирует выдачу, чужой или погашенный — нет.
    def test_open_debt_is_scoped_to_customer_and_paid_debt_does_not_block(self):
        debts = []
        for customer in (self.other_customer, self.customer):
            item = self.make_item()
            booking = self.make_reservation(customer=customer, item=item)
            method = self.method if customer.pk == self.customer.pk else self.make_payment_method(customer)
            prior = HistoryFixtures.make_rental(self, reservation=booking, method=method, status="completed")
            debts.append(raw_insert(Debt, id=uuid4(), rental=prior, amount=Decimal("10.00"),
                                   status="open", created_at=timezone.now()))
        self.reject()
        self.write(debts[-1], status="paid", settled_at=timezone.now())
        self.assertTrue(self.start()[1])

    # Завершённый и ещё не начавшийся тариф не заменяются нулевой или случайной ставкой.
    def test_missing_current_tariff_is_rejected(self):
        self.tariff.valid_to = timezone.now() - timedelta(seconds=1)
        self.tariff.save()
        self.reject()
        ProductTariff.objects.create(product=self.product, hourly_rate="9.00", valid_from=self.booking.expires_at)
        self.reject()

    # На стыке периодов фиксируется именно ставка, действующая в момент начала.
    def test_tariff_boundary_is_half_open(self):
        edge = timezone.now() + timedelta(minutes=1)
        self.tariff.valid_to = edge
        self.tariff.save()
        newer = ProductTariff.objects.create(product=self.product, hourly_rate=Decimal("200.25"), valid_from=edge)
        for delta, expected in ((-1, self.tariff), (0, newer), (1, newer)):
            with self.subTest(delta=delta), transaction.atomic():
                with patch("apps.rentals.issuance.timezone.now", return_value=edge + timedelta(microseconds=delta)):
                    rental = self.start()[0]
                self.assertEqual(rental.tariff_id, expected.pk)
                self.assertEqual(rental.hourly_rate_applied, expected.hourly_rate)
                transaction.set_rollback(True)

    # Повтор fulfilled-брони не попадает в проверку active и не повторяет первичный допуск.
    def test_exact_replay_bypasses_locked_booking_and_new_admission(self):
        original = self.start()[0]
        self.write(self.method, is_default=False, status="inactive", deactivated_at=timezone.now())
        before = self.snapshot()
        with patch("apps.rentals.issuance._locked_booking", side_effect=AssertionError("must not run")):
            with patch("apps.rentals.issuance.timezone.now", return_value=self.booking.expires_at + timedelta(days=1)):
                rental, created = self.start()
        self.assertEqual(rental.pk, original.pk)
        self.assertFalse(created)
        self.assertEqual(self.snapshot(), before)

    # Ошибка после создания Rental и обновления истории откатывает всю выдачу.
    def test_write_failure_rolls_back_all_changes(self):
        with patch("apps.rentals.issuance._set_item_status", side_effect=RuntimeError("fixture failure")):
            self.reject(error=RuntimeError)
        self.assertFalse(Rental.objects.exists())
        self.assertTrue(self.start()[1])


# Проверяет внешний контракт и существующие механизмы Employee/session/CSRF.
class RentalStartApiTests(RentalStartFixtures, TestCase):
    # Готовит адрес выдачи и удостоверенного сотрудника для API-проверок.
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.actor)
        self.url = reverse("rental-start", kwargs={"pk": self.booking.pk})
        self.payload = {"expected_assignment_id": self.assignment.pk}

    # Первый запрос создаёт, повтор возвращает тот же публичный результат.
    def test_success_and_repeat_have_201_200_and_no_payment_secrets(self):
        first = self.api.post(self.url, self.payload, format="json")
        self.assertEqual(first.status_code, 201, first.data)
        repeat = self.api.post(self.url, self.payload, format="json")
        self.assertEqual(repeat.status_code, 200, repeat.data)
        self.assertEqual(first.data, repeat.data)
        self.assertEqual(set(first.data), {"id", "reservation_id", "product_item_id", "status",
            "started_at", "started_by_employee_id", "tariff_id", "hourly_rate_applied"})
        self.assertEqual(first.data["hourly_rate_applied"], "125.50")
        self.assertNotIn(self.method.external_id, str(first.data))
        self.assertEqual(Rental.objects.count(), 1)

    # Сессия, роль и профиль не заменяют четыре необходимых разрешения.
    def test_role_profile_activity_and_each_permission_are_required(self):
        before = self.snapshot()
        missing_profile = self.make_customer(account_type="employee")
        self.grant(missing_profile)
        for user in (None, self.customer, missing_profile):
            self.api.force_authenticate(user)
            self.assertEqual(self.api.post(self.url, self.payload, format="json").status_code, 403)
        self.customer.is_staff = self.customer.is_superuser = True
        self.customer.save()
        self.api.force_authenticate(self.customer)
        self.assertEqual(self.api.post(self.url, self.payload, format="json").status_code, 403)
        self.api.force_authenticate(self.actor)
        for name in ISSUE_PERMISSIONS:
            app, code = name.split(".")
            permission = Permission.objects.get(content_type__app_label=app, codename=code)
            self.actor.user_permissions.remove(permission)
            self.assertEqual(self.api.post(self.url, self.payload, format="json").status_code, 403)
            self.actor.user_permissions.add(permission)
        Account.objects.filter(pk=self.actor.pk).update(is_active=False)
        self.assertEqual(self.api.post(self.url, self.payload, format="json").status_code, 403)
        self.assertEqual(self.snapshot(), before)

    # Scoped queryset одинаково скрывает неизвестную и чужую по ПВ бронь.
    def test_unknown_or_foreign_pickup_point_is_404(self):
        unknown = reverse("rental-start", kwargs={"pk": uuid4()})
        self.assertEqual(self.api.post(unknown, self.payload, format="json").status_code, 404)
        self.employee.pickup_point = self.other_point
        self.employee.save()
        self.assertEqual(self.api.post(self.url, self.payload, format="json").status_code, 404)
        self.assertFalse(Rental.objects.exists())

    # Серверные поля и неправильные ID не превращаются в частичную выдачу.
    def test_bad_input_and_stale_assignment_do_not_write(self):
        before = self.snapshot()
        cases = [{}, {"expected_assignment_id": "bad"}, {"expected_assignment_id": 0},
                 {"expected_assignment_id": True}, {"expected_assignment_id": 2**63}]
        for field in ("payment_method_id", "employee_id", "tariff_id", "started_at", "status", "customer_confirmed_at"):
            cases.append({**self.payload, field: "forged"})
        for data in cases:
            with self.subTest(data=data):
                self.assertEqual(self.api.post(self.url, data, format="json").status_code, 400)
                self.assertEqual(self.snapshot(), before)
        response = self.api.post(self.url, {"expected_assignment_id": self.assignment.pk + 999}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.snapshot(), before)

    # Реальная session authentication продолжает требовать CSRF для POST.
    def test_session_post_requires_csrf(self):
        api = APIClient(enforce_csrf_checks=True)
        api.force_login(self.actor)
        self.assertEqual(api.post(self.url, self.payload, format="json").status_code, 403)
        api.get("/api/auth/csrf/")
        response = api.post(self.url, self.payload, format="json", HTTP_X_CSRFTOKEN=api.cookies["csrftoken"].value)
        self.assertEqual(response.status_code, 201, response.data)

    # Готовую выдачу может повторить только исходный сотрудник с прежним назначением и правами.
    def test_incompatible_replay_and_revoked_permissions_are_rejected(self):
        self.assertEqual(self.api.post(self.url, self.payload, format="json").status_code, 201)
        before = self.snapshot()
        other = self.another_actor()
        self.api.force_authenticate(other)
        self.assertEqual(self.api.post(self.url, self.payload, format="json").status_code, 409)
        self.api.force_authenticate(self.actor)
        self.assertEqual(self.api.post(self.url, {"expected_assignment_id": self.assignment.pk + 1}, format="json").status_code, 409)
        self.actor.user_permissions.clear()
        self.assertEqual(self.api.post(self.url, self.payload, format="json").status_code, 403)
        self.assertEqual(self.snapshot(), before)


# Проверяет основные гонки на отдельных настоящих соединениях PostgreSQL.
class RentalStartConcurrencyTests(RentalStartFixtures, TransactionTestCase):
    # Одновременно запускает операции и не скрывает deadlock за ожидаемым конфликтом.
    def race(self, actions):
        barrier = Barrier(len(actions))

        # Каждый поток получает собственное соединение и ограниченное время ожидания.
        def worker(action):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout='5s'")
                    cursor.execute("SET statement_timeout='10s'")
                barrier.wait(timeout=10)
                return action()
            except ReservationConflict as error:
                cause = getattr(error.__cause__, "__cause__", None)
                self.assertNotEqual(getattr(cause, "sqlstate", None), "40P01")
                return "conflict"
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            futures = [pool.submit(worker, action) for action in actions]
            return [future.result(timeout=20) for future in futures]

    # Два одинаковых запроса дают создание и повтор, а не две Rental.
    def test_concurrent_exact_issue_creates_once_then_replays(self):
        self.assertCountEqual(self.race([lambda: self.start()[1]] * 2), [True, False])
        self.assertEqual(Rental.objects.count(), 1)

    # Разные сотрудники не могут присвоить себе одну и ту же выдачу.
    def test_concurrent_different_employees_have_one_winner(self):
        other = self.another_actor()
        results = self.race([lambda: self.start()[1], lambda: self.start(actor_account_id=other.pk)[1]])
        self.assertCountEqual(results, [True, "conflict"])
        self.assertEqual(Rental.objects.count(), 1)

    # Выдача и отмена завершаются одним согласованным состоянием.
    def test_issue_and_cancel_preserve_terminal_state(self):
        self.race([lambda: self.start()[1], lambda: cancel_reservation(self.booking.pk)])
        self.booking.refresh_from_db()
        self.item.refresh_from_db()
        expected = ("fulfilled", "rented") if Rental.objects.exists() else ("cancelled", "available")
        self.assertEqual((self.booking.status, self.item.status), expected)
        self.assertFalse(self.booking.assignments.filter(released_at__isnull=True).exists())

    # Истечение не освобождает экземпляр одновременно с началом аренды.
    def test_issue_and_due_expiry_never_issue_expired_booking(self):
        self.write(self.booking, expires_at=timezone.now() - timedelta(microseconds=1))
        results = self.race([lambda: self.start()[1], lambda: expire_reservation(self.booking.pk)])
        self.assertCountEqual(results, ["conflict", True])
        self.assertFalse(Rental.objects.exists())
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "expired")

    # TTL проверяется снова после реального ожидания тарифа, а не только внутри _locked_booking.
    def test_expiry_is_rechecked_after_tariff_lock_wait(self):
        deadline = timezone.now() + timedelta(seconds=2)
        self.write(self.booking, expires_at=deadline)
        attempted = Event()

        # Отмечает попытку взять тарифную блокировку, пока другое соединение её удерживает.
        def worker():
            close_old_connections()

            # Наблюдает SQL без подмены результата или серверного времени.
            def watch(execute, sql, params, many, context):
                if 'FROM "product_tariff"' in sql and "FOR NO KEY UPDATE" in sql:
                    attempted.set()
                return execute(sql, params, many, context)
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout='5s'")
                with connection.execute_wrapper(watch):
                    return self.start()
            except ReservationConflict:
                return "conflict"
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                ProductTariff.objects.select_for_update().get(pk=self.tariff.pk)
                future = pool.submit(worker)
                self.assertTrue(attempted.wait(timeout=5))
                Event().wait(max(0, (deadline - timezone.now()).total_seconds()) + 0.05)
            self.assertEqual(future.result(timeout=10), "conflict")
        self.assertFalse(Rental.objects.exists())
        self.assertTrue(expire_reservation(self.booking.pk))
