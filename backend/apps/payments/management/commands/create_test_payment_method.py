"""Подготовка учебных данных; не подтверждение настоящей карты."""
from uuid import UUID, uuid4
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from apps.accounts.models import Account
from apps.payments.models import PaymentMethod
from apps.payments.privacy import private_queries


# Создаёт одно учебное средство, не изменяя существующий default клиента.
class Command(BaseCommand):
    help = "Create a test payment method for an existing customer (DEBUG only)."

    # Принимает только клиента; банковские идентификаторы и реквизиты не вводятся.
    def add_arguments(self, parser):
        parser.add_argument("customer_id", type=UUID)

    # Проверяет разрешение команды и подготавливает согласованную запись модели.
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Учебная команда доступна только при DEBUG=True.")
        try:
            with transaction.atomic(), private_queries("default"):
                customer = Account.objects.select_for_update(no_key=True).filter(
                    pk=options["customer_id"], account_type=Account.AccountType.CUSTOMER, is_active=True,
                ).first()
                if customer is None:
                    raise CommandError("Нужен существующий активный клиент.")
                method = PaymentMethod.objects.filter(customer_account=customer, is_default=True).first()
                if method is not None:
                    self.stdout.write(f"Default уже существует, изменений нет: {method.pk}")
                    return
                method = PaymentMethod(customer_account=customer, external_id=f"test-{uuid4()}",
                    is_default=True, status=PaymentMethod.Status.ACTIVE,
                    verification_status=PaymentMethod.VerificationStatus.CONFIRMED, created_at=timezone.now())
                method.full_clean()
                models.Model.save(method, force_insert=True)
        except (ValidationError, IntegrityError) as error:
            raise CommandError("Не удалось создать учебное средство; данные не изменены.") from error
        self.stdout.write(self.style.SUCCESS(f"Учебное платежное средство: {method.pk}"))
