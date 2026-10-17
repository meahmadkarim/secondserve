from django.conf import settings

def navigation(request):
    role = request.user.profile.role if request.user.is_authenticated else ''
    return {'role': role, 'demo_mode': settings.DEBUG}
