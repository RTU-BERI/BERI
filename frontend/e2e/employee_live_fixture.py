"""Run through manage.py shell on a new, private PostgreSQL database only."""
from datetime import timedelta
from decimal import Decimal
from io import StringIO
import json
import os
from pathlib import Path
import secrets
from uuid import uuid4

from django.conf import settings
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.db import connection, transaction
from django.utils import timezone
from apps.accounts.models import Account, Employee
from apps.catalog.models import Product, ProductCategory, ProductTariff
from apps.inventory.services import create_item
from apps.pickup_points.models import PickupPoint
from apps.reservations.services import create_reservation
from apps.rentals.services import REPLACE_PERMISSIONS
from apps.rentals.issuance import ISSUE_PERMISSIONS

database = settings.DATABASES['default']
if (os.environ.get('BERI_LIVE_ISOLATED') != '1' or not settings.DEBUG
        or database['ENGINE'] != 'django.db.backends.postgresql'
        or not database['NAME'].startswith('beri_s2p6')
        or database['HOST'] not in ('localhost', '127.0.0.1')):
    raise RuntimeError('Use DEBUG, BERI_LIVE_ISOLATED=1 and your new local beri_s2p6* PostgreSQL database.')
if Path.cwd().name != 'backend':
    raise RuntimeError('Run this fixture from the backend directory.')
fixture_path = Path(os.environ['BERI_EMPLOYEE_LIVE_FIXTURE']).resolve()
if fixture_path.is_relative_to(Path.cwd().parent) or fixture_path.exists():
    raise RuntimeError('Choose a new private fixture path outside the repository.')
if Account.objects.exists() or Product.objects.exists() or PickupPoint.objects.exists():
    raise RuntimeError('The disposable database must be empty. Existing records are never cleared.')

password = secrets.token_urlsafe(24)
with transaction.atomic():
    accounts = {}
    for index, role in enumerate(['customer', 'employee', 'colleague', 'restricted'], 1):
        accounts[role] = Account.objects.create_user(
            phone=f'+1555{index:09d}', password=password,
            account_type='customer' if role == 'customer' else 'employee',
            status='synthetic_demo', phone_verified_at=timezone.now(),
        )
    point = PickupPoint.objects.create(name='Учебный ПВ S2-P6', address='Учебный адрес',
        latitude='55.750000', longitude='37.610000', status='demo')
    other_point = PickupPoint.objects.create(name='Другой учебный ПВ', address='Другой учебный адрес',
        latitude='55.760000', longitude='37.620000', status='demo')
    permissions = set(REPLACE_PERMISSIONS) | set(ISSUE_PERMISSIONS) | {'accounts.view_employee', 'rentals.view_inspection'}
    for role in ['employee', 'colleague', 'restricted']:
        Employee.objects.create(account=accounts[role], pickup_point=point)
        if role != 'restricted':
            for permission in permissions:
                app, codename = permission.split('.')
                accounts[role].user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=codename))
    call_command('create_test_payment_method', str(accounts['customer'].pk), stdout=StringIO())
    category = ProductCategory.objects.create(name='Учебные инструменты S2-P6')
    bookings = {}
    for scenario in ['healthy', 'replace', 'no_replacement', 'stale', 'expired', 'foreign', 'client']:
        product = Product.objects.create(name=f'Учебный инструмент {scenario}', category=category,
            description='Синтетические данные S2-P6', status='demo')
        ProductTariff.objects.create(product=product, hourly_rate=Decimal('125.50'),
            valid_from=timezone.now() - timedelta(days=1))
        location = other_point if scenario == 'foreign' else point
        create_item(product=product, pickup_point=location, inventory_code=f'S2P6-{scenario}-1', status='available')
        if scenario in ['replace', 'stale']:
            create_item(product=product, pickup_point=location, inventory_code=f'S2P6-{scenario}-2', status='available')
        if scenario == 'client':
            continue
        booking = create_reservation(customer_account_id=accounts['customer'].pk,
            product_id=product.pk, pickup_point_id=location.pk, request_id=uuid4())
        if scenario == 'expired':
            # Только отрицательная TTL-фикстура этой новой учебной БД.
            with connection.cursor() as cursor:
                cursor.execute('UPDATE reservation SET created_at=%s, expires_at=%s WHERE id=%s', [
                    timezone.now() - timedelta(hours=2), timezone.now() - timedelta(hours=1), booking.pk])
        bookings[scenario] = str(booking.pk)

manifest = {'password': password, 'users': {role: account.phone for role, account in accounts.items()}, 'bookings': bookings}
with fixture_path.open('x', encoding='utf-8') as target:
    json.dump(manifest, target)
print('Created disposable employee fixtures. Private credentials saved outside the repository.')
