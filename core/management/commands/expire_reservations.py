from django.core.management.base import BaseCommand
from core.services import expire_reservations
class Command(BaseCommand):
    help = 'Idempotently persist past-deadline reservations as expired. Request-time deadline checks work without this command.'
    def handle(self, *args, **options):
        self.stdout.write(f'{expire_reservations()} reservations expired.')
