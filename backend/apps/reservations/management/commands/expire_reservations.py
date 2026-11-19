"""Run one sweep of due reservations in the configured PostgreSQL database."""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ...models import Reservation
from ...services import expire_reservation


class Command(BaseCommand):
    """Close due reservations individually so one invalid record does not block others."""

    help = "Expire due reservations and release their assigned inventory."

    def handle(self, *args, **options):
        due_ids = (
            Reservation.objects.filter(
                status=Reservation.Status.ACTIVE,
                expires_at__lte=timezone.now(),
            )
            .order_by("expires_at", "pk")
            .values_list("pk", flat=True)
        )
        expired = failed = 0
        for reservation_id in due_ids.iterator():
            try:
                expired += expire_reservation(reservation_id)
            except ValidationError as error:
                failed += 1
                self.stderr.write(f"{reservation_id}: {' '.join(error.messages)}")
        self.stdout.write(f"Expired reservations: {expired}")
        if failed:
            raise CommandError(f"Could not expire {failed} reservation(s).")
