from zoneinfo import ZoneInfo
from django.utils import timezone
from .models import CampusSettings

class CampusTimezoneMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
    def __call__(self, request):
        try:
            campus = CampusSettings.objects.filter(pk=1).first()
            timezone.activate(ZoneInfo(campus.timezone if campus else 'Asia/Bishkek'))
            return self.get_response(request)
        finally:
            timezone.deactivate()
