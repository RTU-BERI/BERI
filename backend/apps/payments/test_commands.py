"""Проверки подготовки учебных данных без банковского сервиса."""
from io import StringIO
from uuid import uuid4
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from .models import PaymentMethod
from .test_support import FixtureMixin


# Проверяет границы одной CLI-команды, не вводя специального режима выдачи.
class TestPaymentMethodCommandTests(FixtureMixin, TestCase):
    # Команда создаёт пригодные данные без реквизитов и не печатает токен.
    @override_settings(DEBUG=True)
    def test_creates_default_without_card_data(self):
        output = StringIO()
        call_command("create_test_payment_method", str(self.customer.pk), stdout=output)
        method = PaymentMethod.objects.get(customer_account=self.customer, is_default=True)
        self.assertEqual((method.status, method.verification_status), ("active", "confirmed"))
        self.assertIsNone(method.last4)
        self.assertIsNone(method.expires_month)
        self.assertIsNone(method.expires_year)
        self.assertNotIn(method.external_id, output.getvalue())
        self.assertIn(str(method.pk), output.getvalue())

    # Существующий default возвращается без замены его данных независимо от префикса.
    @override_settings(DEBUG=True)
    def test_existing_default_is_never_changed(self):
        method = self.make_confirmed_method(is_default=True, external_id="opaque-existing-id")
        before = list(PaymentMethod.objects.order_by("pk").values())
        output = StringIO()
        call_command("create_test_payment_method", str(self.customer.pk), stdout=output)
        self.assertIn(str(method.pk), output.getvalue())
        self.assertEqual(list(PaymentMethod.objects.order_by("pk").values()), before)

    # DEBUG относится только к команде; неподходящий клиент также не изменяет данные.
    def test_disabled_command_and_invalid_customer_do_not_write(self):
        before = list(PaymentMethod.objects.order_by("pk").values())
        with override_settings(DEBUG=False), self.assertRaises(CommandError):
            call_command("create_test_payment_method", str(self.customer.pk))
        with override_settings(DEBUG=True):
            for pk in (uuid4(), self.employee_account.pk):
                with self.subTest(pk=pk), self.assertRaises(CommandError):
                    call_command("create_test_payment_method", str(pk))
            self.customer.is_active = False
            self.customer.save()
            with self.assertRaises(CommandError):
                call_command("create_test_payment_method", str(self.customer.pk))
        self.assertEqual(list(PaymentMethod.objects.order_by("pk").values()), before)
