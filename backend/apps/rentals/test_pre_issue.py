"""S2-P4: real services, employee HTTP authorization, rollback and PG races."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, models, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied as PhysicalPermissionDenied
from rest_framework.test import APIClient
from apps.accounts.models import Account
from apps.catalog.models import ProductComponent
from apps.inventory.models import ProductItem
from apps.reservations.models import Reservation, ReservationItemAssignment
from apps.reservations.services import (
    ReservationConflict,
    cancel_reservation,
    create_reservation,
    expire_reservation,
)
from apps.reservations.test_support import FixtureMixin
from .models import Inspection, InspectionProblem, Rental
from .services import (
    INSPECT_PERMISSIONS,
    REPLACE_PERMISSIONS,
    record_pre_issue,
    replace_after_inspection,
)

class InspectionFixtures(FixtureMixin):

    def setUp(self):
        super().setUp()
        self.actor = self.employee.account
        names = {name.split('.')[1] for name in REPLACE_PERMISSIONS} | {'view_inspection'}
        self.actor.user_permissions.add(*Permission.objects.filter(codename__in=names))
        self.booking = create_reservation(customer_account_id=self.customer.pk, product_id=self.product.pk, pickup_point_id=self.point.pk, request_id=uuid4())
        self.assignment = self.booking.assignments.get()
        self.component = ProductComponent.objects.create(product=self.product, name='Case', quantity=2)

    def healthy(self, **overrides):
        return dict(actor_account_id=self.actor.pk, reservation_id=self.booking.pk, expected_assignment_id=self.assignment.pk, condition_ok=True, completeness_ok=True, **overrides)

    def bad(self, **overrides):
        args = self.healthy()
        args.update(condition_ok=False, reason='Observed damage', problems=[dict(problem_type='damage', description='Cracked casing')])
        args.update(overrides)
        return args

    def snapshot(self):
        return {m._meta.db_table: list(m.objects.order_by('pk').values()) for m in (ProductItem, Reservation, ReservationItemAssignment, Inspection, InspectionProblem, Rental)}

    def reject(self, action, error=ValidationError):
        before = self.snapshot()
        with self.assertRaises(error):
            action()
        self.assertEqual(self.snapshot(), before)

class PreIssueServiceTests(InspectionFixtures, TestCase):

    def test_healthy_inspection_is_immutable_observation_not_issuance(self):
        row, booking, assignment = record_pre_issue(**self.healthy(comment='OK'))
        self.assertEqual(row.product_item_id, self.item.pk)
        self.assertEqual(row.employee_id, self.employee.pk)
        self.assertEqual(row.reservation_id, self.booking.pk)
        self.assertEqual(row.inspection_type, 'pre_issue')
        self.assertIsNone(row.rental_id)
        self.assertIsNone(row.customer_confirmed_at)
        self.assertFalse(row.problems.exists())
        self.assertEqual(booking.status, 'active')
        self.assertIsNone(assignment.released_at)
        self.assertFalse(Rental.objects.exists())
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'reserved')

    def test_identical_retry_preserves_id_timestamp_and_rows(self):
        args = self.healthy(comment=' Same literal comment ')
        first = record_pre_issue(**args)[0]
        before = self.snapshot()
        replay = record_pre_issue(**args)[0]
        self.assertEqual(first.pk, replay.pk)
        self.assertEqual(first.inspected_at, replay.inspected_at)
        self.assertEqual(self.snapshot(), before)

    def test_changed_payload_or_employee_is_conflict_not_history_edit(self):
        record_pre_issue(**self.healthy(comment='Original'))
        self.reject(lambda: record_pre_issue(**self.healthy(comment='Edited')), ReservationConflict)
        other = self.make_customer(account_type='employee')
        from apps.accounts.models import Employee
        Employee.objects.create(account=other, pickup_point=self.point)
        other.user_permissions.add(*self.actor.user_permissions.all())
        self.reject(lambda: record_pre_issue(**{**self.healthy(comment='Original'), 'actor_account_id': other.pk}), ReservationConflict)

    def test_problem_cannot_be_recorded_without_atomic_resolution(self):
        args = self.bad()
        args.pop('reason')
        self.reject(lambda: record_pre_issue(**args))

    def test_employee_role_active_point_and_permissions_are_required(self):
        self.reject(lambda: record_pre_issue(**{**self.healthy(), 'actor_account_id': self.customer.pk}), (PermissionDenied, PhysicalPermissionDenied))
        self.actor.is_active = False
        self.actor.save()
        self.reject(lambda: record_pre_issue(**self.healthy()), (PermissionDenied, PhysicalPermissionDenied))
        self.actor.is_active = True
        self.actor.save()
        self.actor.user_permissions.clear()
        self.reject(lambda: record_pre_issue(**self.healthy()), (PermissionDenied, PhysicalPermissionDenied))

    def test_superuser_without_employee_cannot_bypass_scope(self):
        self.customer.is_staff = self.customer.is_superuser = True
        self.customer.save()
        self.reject(lambda: record_pre_issue(**{**self.healthy(), 'actor_account_id': self.customer.pk}), (PermissionDenied, PhysicalPermissionDenied))

    def test_wrong_pickup_point_is_denied_even_to_employee_superuser(self):
        self.employee.pickup_point = self.other_point
        self.employee.save()
        self.actor.is_staff = self.actor.is_superuser = True
        self.actor.save()
        self.reject(lambda: record_pre_issue(**self.healthy()), (PermissionDenied, PhysicalPermissionDenied))

    def test_expiry_boundary_denies_new_and_replayed_inspections(self):
        record_pre_issue(**self.healthy())
        with patch('apps.rentals.services.timezone.now', return_value=self.booking.expires_at):
            self.reject(lambda: record_pre_issue(**self.healthy()), ReservationConflict)
            self.reject(lambda: replace_after_inspection(**self.bad()), ReservationConflict)

    def test_cancelled_booking_and_stale_assignment_are_denied(self):
        self.reject(lambda: record_pre_issue(**{**self.healthy(), 'expected_assignment_id': self.assignment.pk + 100}), ReservationConflict)
        cancel_reservation(self.booking.pk)
        self.reject(lambda: record_pre_issue(**self.healthy()), ReservationConflict)

    def test_first_inspection_freezes_component_history(self):
        record_pre_issue(**self.healthy())
        with self.assertRaises(ValidationError):
            ProductComponent.objects.create(product=self.product, name='Late', quantity=1)

    def test_replacement_records_problems_and_contiguous_assignment_history(self):
        candidate = self.make_item()
        deadline = self.booking.expires_at
        row, booking, new = replace_after_inspection(**self.bad(replacement_item_id=candidate.pk))
        old = ReservationItemAssignment.objects.get(pk=self.assignment.pk)
        self.item.refresh_from_db()
        candidate.refresh_from_db()
        self.assertEqual((self.item.status, candidate.status), ('unavailable', 'reserved'))
        self.assertEqual(self.item.status_reason, 'Observed damage')
        self.assertEqual(booking.initial_product_item_id, self.item.pk)
        self.assertEqual(booking.assigned_product_item_id, candidate.pk)
        self.assertEqual(booking.expires_at, deadline)
        self.assertEqual(new.sequence_no, 2)
        self.assertEqual(new.assigned_by_employee_id, self.employee.pk)
        self.assertEqual(new.reason, 'Observed damage')
        self.assertEqual(old.released_at, new.assigned_at)
        self.assertEqual(row.inspected_at, new.assigned_at)
        self.assertEqual(row.problems.get().problem_type, 'damage')
        self.assertFalse(Rental.objects.exists())

    def test_multiple_replacements_then_new_successful_inspection(self):
        second = self.make_item()
        third = self.make_item()
        _, booking, assignment = replace_after_inspection(**self.bad(replacement_item_id=second.pk))
        _, booking, assignment = replace_after_inspection(**self.bad(expected_assignment_id=assignment.pk, replacement_item_id=third.pk))
        row, _, _ = record_pre_issue(**{**self.healthy(), 'expected_assignment_id': assignment.pk})
        self.assertEqual(row.product_item_id, third.pk)
        self.assertEqual(list(booking.assignments.values_list('sequence_no', flat=True).order_by('sequence_no')), [1, 2, 3])
        self.assertEqual(Inspection.objects.count(), 3)
        self.assertEqual(booking.initial_product_item_id, self.item.pk)

    def test_replacement_replay_is_conflict_without_duplicate_rows(self):
        self.make_item()
        replace_after_inspection(**self.bad())
        self.reject(lambda: replace_after_inspection(**self.bad()), ReservationConflict)
        self.reject(lambda: record_pre_issue(**self.healthy()), ReservationConflict)

    def test_saved_healthy_result_cannot_be_replaced_with_bad_result(self):
        record_pre_issue(**self.healthy())
        self.make_item()
        self.reject(lambda: replace_after_inspection(**self.bad()), ReservationConflict)

    def test_missing_components_require_same_product_and_aggregate_bounded_quantity(self):
        self.make_item()
        missing = dict(problem_type='missing_component', description='Missing case', product_component_id=self.component.pk, missing_quantity=1)
        row, _, _ = replace_after_inspection(**self.bad(condition_ok=True, completeness_ok=False, problems=[missing, missing]))
        self.assertTrue(row.condition_ok)
        self.assertFalse(row.completeness_ok)
        self.assertEqual(row.problems.count(), 2)

    def test_invalid_flags_quantities_descriptions_and_reasons_rollback(self):
        self.make_item()
        other = ProductComponent.objects.create(product=self.other_product, name='Other', quantity=1)
        bad_cases = [{'reason': ''}, {'reason': 'x' * 31}, {'reason': '   '}, {'problems': []}, {'condition_ok': True}, {'problems': [{'problem_type': 'bad', 'description': 'x'}]}, {'problems': [{'problem_type': 'damage', 'description': '   '}]}, {'problems': [{'problem_type': 'damage', 'description': 'x', 'missing_quantity': 1}]}]
        for cid, qty in [(self.component.pk, 3), (self.component.pk, 0), (self.component.pk, True), (other.pk, 1)]:
            bad_cases.append(dict(condition_ok=True, completeness_ok=False, problems=[dict(problem_type='missing_component', description='x', product_component_id=cid, missing_quantity=qty)]))
        for values in bad_cases:
            with self.subTest(values=values):
                self.reject(lambda: replace_after_inspection(**self.bad(**values)))

    def test_unavailable_foreign_point_product_and_old_items_cannot_be_selected(self):
        candidates = [self.item, self.make_item(status='unavailable'), self.make_item(product=self.other_product), self.make_item(pickup_point=self.other_point)]
        for candidate in candidates:
            with self.subTest(candidate=candidate.pk):
                self.reject(lambda: replace_after_inspection(**self.bad(replacement_item_id=candidate.pk)), ReservationConflict)

    def test_no_stock_without_explicit_cancel_reason_leaves_no_observation(self):
        self.reject(lambda: replace_after_inspection(**self.bad()))

    def test_no_stock_with_explicit_reason_cancels_and_quarantines(self):
        row, booking, assignment = replace_after_inspection(**self.bad(completion_reason='No replacement stock'))
        self.assertEqual(booking.status, 'cancelled')
        self.assertEqual(booking.assigned_product_item_id, self.item.pk)
        self.assertEqual(booking.cancelled_at, booking.completed_at)
        self.assertEqual(booking.completed_at, assignment.released_at)
        self.assertEqual(row.inspected_at, booking.completed_at)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'unavailable')
        self.assertFalse(expire_reservation(booking.pk))
        self.assertFalse(cancel_reservation(booking.pk))

    def test_customer_decline_cancels_without_reserving_free_candidate(self):
        candidate = self.make_item()
        replace_after_inspection(**self.bad(decline_replacement=True, completion_reason='Customer declined'))
        candidate.refresh_from_db()
        self.assertEqual(candidate.status, 'available')

    def test_explicit_unavailable_target_is_not_treated_as_no_stock_cancellation(self):
        self.reject(lambda: replace_after_inspection(**self.bad(replacement_item_id=999999, completion_reason='No stock')), ReservationConflict)

    def test_failure_after_inspection_insert_rolls_back_entire_operation(self):
        self.make_item()
        with patch('apps.rentals.services._set_item_status', side_effect=RuntimeError('Synthetic rollback')):
            self.reject(lambda: replace_after_inspection(**self.bad()), RuntimeError)

    def test_cancel_after_replacement_only_releases_new_item(self):
        candidate = self.make_item()
        replace_after_inspection(**self.bad())
        self.assertTrue(cancel_reservation(self.booking.pk))
        self.item.refresh_from_db()
        candidate.refresh_from_db()
        self.assertEqual((self.item.status, candidate.status), ('unavailable', 'available'))
        self.assertEqual(Inspection.objects.count(), 1)

    def test_expire_after_replacement_only_releases_new_item(self):
        candidate = self.make_item()
        replace_after_inspection(**self.bad())
        with patch('apps.reservations.services.timezone.now', return_value=self.booking.expires_at):
            self.assertTrue(expire_reservation(self.booking.pk))
        self.item.refresh_from_db()
        candidate.refresh_from_db()
        self.assertEqual((self.item.status, candidate.status), ('unavailable', 'available'))

    def test_corrupted_location_refuses_without_auto_repair(self):
        with connection.cursor() as c:
            c.execute('UPDATE product_item SET pickup_point_id=%s WHERE id=%s', [self.other_point.pk, self.item.pk])
        self.reject(lambda: record_pre_issue(**self.healthy()), ReservationConflict)

class PreIssueAPITests(InspectionFixtures, TestCase):

    def setUp(self):
        super().setUp()
        self.client_api = APIClient()
        self.client_api.force_authenticate(self.actor)
        self.url = reverse('pre-issue-inspections', args=[self.booking.pk])
        self.replace_url = reverse('inspection-replacement', args=[self.booking.pk])

    def payload(self):
        return dict(expected_assignment_id=self.assignment.pk, condition_ok=True, completeness_ok=True)

    def test_employee_can_record_replay_and_list_history_without_customer_pii(self):
        a = self.client_api.post(self.url, self.payload(), format='json')
        b = self.client_api.post(self.url, self.payload(), format='json')
        self.assertEqual((a.status_code, b.status_code), (200, 200))
        self.assertEqual(a.data['inspection']['id'], b.data['inspection']['id'])
        response = self.client_api.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['inspections']), 1)
        self.assertNotIn(self.customer.phone, str(response.data))
        self.assertNotIn('password', str(response.data))

    def test_employee_can_obtain_assignment_before_first_observation(self):
        before = self.snapshot()
        response = self.client_api.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['expected_assignment_id'], self.assignment.pk)
        self.assertEqual(response.data['inspections'], [])
        self.assertEqual(self.snapshot(), before)

    def test_anonymous_customer_and_staff_without_employee_denied(self):
        for user in [None, self.customer]:
            self.client_api.force_authenticate(user)
            self.assertIn(self.client_api.post(self.url, self.payload(), format='json').status_code, (401, 403))
        self.customer.is_staff = self.customer.is_superuser = True
        self.customer.save()
        self.client_api.force_authenticate(self.customer)
        self.assertEqual(self.client_api.post(self.url, self.payload(), format='json').status_code, 403)
        self.assertFalse(Inspection.objects.exists())

    def test_revoked_permissions_and_inactive_account_not_trusted_from_cached_user(self):
        self.actor.user_permissions.clear()
        self.assertEqual(self.client_api.post(self.url, self.payload(), format='json').status_code, 403)
        self.actor.user_permissions.add(Permission.objects.get(codename='add_inspection'))
        Account.objects.filter(pk=self.actor.pk).update(is_active=False)
        self.assertEqual(self.client_api.post(self.url, self.payload(), format='json').status_code, 403)

    def test_foreign_point_is_hidden_for_read_and_write(self):
        self.employee.pickup_point = self.other_point
        self.employee.save()
        self.assertEqual(self.client_api.get(self.url).status_code, 404)
        self.assertEqual(self.client_api.post(self.url, self.payload(), format='json').status_code, 404)

    def test_bad_input_and_server_field_forgery_rejected(self):
        for fields in [{'expected_assignment_id': 'bad'}, {'customer_confirmed_at': '2026-11-19'}, {'employee_id': 999}, {'inspection_type': 'return'}, {'problems': [{'problem_type': 'damage', 'description': 'x', 'inspection_id': 1}]}]:
            response = self.client_api.post(self.url, {**self.payload(), **fields}, format='json')
            self.assertEqual(response.status_code, 400)
        self.assertFalse(Inspection.objects.exists())

    def test_replacement_api_and_old_assignment_conflict(self):
        self.make_item()
        payload = {k: v for k, v in self.bad().items() if k not in ('actor_account_id', 'reservation_id')}
        response = self.client_api.post(self.replace_url, payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['sequence_no'], 2)
        self.assertEqual(self.client_api.post(self.replace_url, payload, format='json').status_code, 409)

    def test_expired_and_changed_observation_return_409(self):
        self.client_api.post(self.url, self.payload(), format='json')
        self.assertEqual(self.client_api.post(self.url, {**self.payload(), 'comment': 'changed'}, format='json').status_code, 409)
        with patch('apps.rentals.services.timezone.now', return_value=self.booking.expires_at):
            self.assertEqual(self.client_api.post(self.url, self.payload(), format='json').status_code, 409)

    def test_csrf_enforced_for_real_session_post(self):
        csrf_client = APIClient(enforce_csrf_checks=True)
        csrf_client.force_login(self.actor)
        self.assertEqual(csrf_client.post(self.url, self.payload(), format='json').status_code, 403)
        self.assertFalse(Inspection.objects.exists())

    def test_inspection_permission_does_not_grant_replacement(self):
        self.actor.user_permissions.clear()
        self.actor.user_permissions.add(Permission.objects.get(codename='add_inspection'))
        self.assertEqual(self.client_api.post(self.replace_url, {}, format='json').status_code, 403)

    def test_unsupported_write_verbs_are_rejected(self):
        for method in ('put', 'patch', 'delete'):
            self.assertEqual(getattr(self.client_api, method)(self.url, {}, format='json').status_code, 405)

class PreIssueConcurrencyTests(InspectionFixtures, TransactionTestCase):

    def race(self, actions):
        barrier = Barrier(len(actions))

        def worker(action):
            close_old_connections()
            try:
                connection.ensure_connection()
                with connection.cursor() as c:
                    c.execute("SET lock_timeout='5s'")
                    c.execute("SET statement_timeout='10s'")
                barrier.wait(timeout=10)
                return action()
            except (ReservationConflict, ValidationError):
                return 'conflict'
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            futures = [pool.submit(worker, a) for a in actions]
            return [f.result(timeout=20) for f in futures]

    def test_concurrent_identical_inspection_is_recorded_once(self):
        results = self.race([lambda: record_pre_issue(**self.healthy())[0].pk] * 2)
        self.assertEqual(results[0], results[1])
        self.assertEqual(Inspection.objects.count(), 1)

    def test_two_concurrent_replacements_advance_only_once(self):
        self.make_item()
        self.make_item()
        results = self.race([lambda: replace_after_inspection(**self.bad())[0].pk] * 2)
        self.assertEqual(results.count('conflict'), 1)
        self.assertEqual(Inspection.objects.count(), 1)
        self.assertEqual(ReservationItemAssignment.objects.count(), 2)

    def test_busy_candidate_causes_conflict_not_cancellation(self):
        candidate = self.make_item()
        before = self.snapshot()
        with transaction.atomic():
            ProductItem.objects.select_for_update().get(pk=candidate.pk)

            def attempt():
                close_old_connections()
                try:
                    with self.assertRaises(ReservationConflict):
                        replace_after_inspection(**self.bad(completion_reason='No stock'))
                finally:
                    connection.close()
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(attempt).result(timeout=10)
        self.assertEqual(self.snapshot(), before)

    def test_replacement_and_customer_cancel_preserve_old_quarantine(self):
        candidate = self.make_item()
        results = self.race([lambda: replace_after_inspection(**self.bad())[0].pk, lambda: cancel_reservation(self.booking.pk)])
        self.booking.refresh_from_db()
        self.item.refresh_from_db()
        candidate.refresh_from_db()
        self.assertIn(self.booking.status, ('active', 'cancelled'))
        if Inspection.objects.exists():
            self.assertEqual(self.item.status, 'unavailable')
            self.assertEqual(self.booking.assigned_product_item_id, candidate.pk)
        else:
            self.assertEqual(self.item.status, 'available')
        self.assertLessEqual(ReservationItemAssignment.objects.filter(reservation=self.booking, released_at__isnull=True).count(), 1)

    def test_replacement_and_due_expiry_cannot_inspect_expired_booking(self):
        with connection.cursor() as c:
            c.execute('UPDATE reservation SET expires_at=%s WHERE id=%s', [timezone.now() - timedelta(microseconds=1), self.booking.pk])
        self.make_item()
        results = self.race([lambda: replace_after_inspection(**self.bad())[0].pk, lambda: expire_reservation(self.booking.pk)])
        self.assertIn('conflict', results)
        self.assertIn(True, results)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, 'expired')
        self.assertFalse(Inspection.objects.exists())

    def test_replacement_competes_with_new_booking_for_same_candidate(self):
        candidate = self.make_item()

        def book():
            return create_reservation(customer_account_id=self.other_customer.pk, product_id=self.product.pk, pickup_point_id=self.point.pk, request_id=uuid4()).pk
        results = self.race([lambda: replace_after_inspection(**self.bad())[0].pk, book])
        self.assertEqual(results.count('conflict'), 1)
        self.assertEqual(ReservationItemAssignment.objects.filter(product_item=candidate, released_at__isnull=True).count(), 1)

    def test_concurrent_different_payloads_do_not_rewrite_observation(self):
        results = self.race([lambda: record_pre_issue(**self.healthy(comment='A'))[0].pk, lambda: record_pre_issue(**self.healthy(comment='B'))[0].pk])
        self.assertEqual(results.count('conflict'), 1)
        self.assertEqual(Inspection.objects.count(), 1)
