"""Synthetic fixtures, never destructive: only create missing demo records."""
import random
from datetime import timedelta, datetime, time
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from core.models import (CampusSettings, MealService, MealIntention, ForecastSnapshot,
                         SurplusListing, Reservation, AuditEvent)
from core.services import forecast

PASSWORD = 'SecondServe!2026'
MENU = [
    ('Roasted garden bowl', 'Roasted seasonal vegetables, herbed rice and a lemon chickpea dressing.', 'Chickpeas, rice, carrots, pumpkin, lemon. Dressing contains sesame.', 'Plant-based · Sesame'),
    ('Golden lentil dal', 'Slow-cooked lentils with turmeric rice, cucumber salad and warm flatbread.', 'Lentils, rice, cucumber, wheat flatbread. Contains wheat/gluten.', 'Vegetarian · Wheat'),
    ('Harvest pasta', 'Tomato and basil pasta with roasted squash and a crisp campus salad.', 'Wheat pasta, tomatoes, squash, basil, cheese. Contains wheat and milk.', 'Vegetarian · Milk · Wheat'),
    ('Herby chicken & grains', 'Oven-roasted chicken, bulgur wheat and seasonal greens.', 'Chicken, bulgur wheat, lemon, herbs. Contains wheat/gluten.', 'Contains wheat'),
]

class Command(BaseCommand):
    help = 'Create local-only synthetic demo accounts and relative-date meals without deleting or resetting user data.'

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('Demo accounts are disabled when DJANGO_DEBUG=0.')
        rng = random.Random(42)
        CampusSettings.current()
        def demo_user(username, display, role):
            user, created = User.objects.get_or_create(username=username, defaults={'first_name': display, 'last_name': 'SecondServe demo'})
            if created:
                user.set_password(PASSWORD)
                user.save()
                user.profile.role = role
                user.profile.save()
            elif user.last_name != 'SecondServe demo':
                raise CommandError(f'Username {username} already belongs to a non-demo user. No records changed.')
            return user
        admin = demo_user('admin', 'Aida · Campus admin', 'admin')
        kitchen = demo_user('kitchen1', 'Mira · Kitchen', 'kitchen')
        demo_user('kitchen2', 'Timur · Kitchen', 'kitchen')
        names = ['Amina', 'Daniyar', 'Elina', 'Arsen', 'Nurai', 'Bek', 'Alina', 'Samat']
        students = [demo_user(f'student{i:02}', f'{names[(i-1)%8]} {i:02}', 'student') for i in range(1, 33)]
        now = timezone.localtime()
        today = now.date()
        def at(day, hour, minute=0):
            return timezone.make_aware(datetime.combine(day, time(hour, minute)))
        def intentions(meal):
            for student in students:
                choice = rng.choices(['attending', 'not_attending', 'unsure', None], weights=[55, 15, 15, 15])[0]
                if choice:
                    MealIntention.objects.create(student=student, meal=meal, response=choice)
        created_count = 0
        for offset in range(14, 0, -1):
            day = today - timedelta(days=offset)
            for kind, hour in [('lunch', 12), ('dinner', 18)]:
                menu = MENU[(offset + hour) % len(MENU)]
                meal, created = MealService.objects.get_or_create(date=day, meal_type=kind, defaults=dict(title=menu[0], description=menu[1], allergens=menu[2], dietary_tags=menu[3], capacity=32, cutoff=at(day, hour-2), service_start=at(day, hour), service_end=at(day, hour+1), creator=kitchen, synthetic=True))
                if not created:
                    continue
                created_count += 1
                intentions(meal)
                data = forecast(meal)
                constrained = offset == 7 and kind == 'lunch'
                meal.capacity = 18 if constrained else 32
                if constrained:
                    data = forecast(meal)
                ForecastSnapshot.objects.create(meal=meal, data=data)
                meal.prepared = 18 if constrained else rng.randint(26, 32)
                meal.served = 18 if constrained else meal.prepared - rng.randint(4, 7)
                collected = 0 if constrained else 2
                meal.discarded = 0 if constrained else 1
                meal.retained = meal.prepared - meal.served - collected - meal.discarded
                meal.preparation_recorded = meal.serving_recorded = True
                meal.sold_out = constrained
                meal.state = 'closed'
                meal.save()
                if constrained:
                    continue
                listing = SurplusListing.objects.create(meal=meal, offered=meal.prepared-meal.served, committed=collected, pickup_start=at(day, hour+1), deadline=at(day, hour+2), location=meal.location, status='closed', handling_confirmed=True, creator=kitchen, notes='Synthetic demonstration data. Suitable unserved portions confirmed by staff.')
                for index in range(collected):
                    Reservation.objects.create(student=students[index+4], listing=listing, status='collected', collected_at=at(day, hour+1, 15+index*5), collecting_staff=kitchen)
                Reservation.objects.create(student=students[9], listing=listing, status='expired')
                Reservation.objects.create(student=students[10], listing=listing, status='cancelled', cancelled_at=at(day, hour+1, 5))
                AuditEvent.objects.create(actor=kitchen, action='Synthetic historical meal reconciled', object_type='MealService', object_id=meal.pk, changes={'dataset': 'Synthetic demonstration data'})
        # Immediately reservable and collectible for the current demo session.
        menu = MENU[0]
        meal, created = MealService.objects.get_or_create(date=today, meal_type='lunch', defaults=dict(title=menu[0], description=menu[1], allergens=menu[2], dietary_tags=menu[3], capacity=32, cutoff=now-timedelta(hours=4), service_start=now-timedelta(hours=3), service_end=now-timedelta(hours=1), creator=kitchen, synthetic=True))
        if created:
            created_count += 1
            intentions(meal)
            ForecastSnapshot.objects.create(meal=meal, data=forecast(meal))
            meal.state, meal.prepared, meal.served = 'surplus_collection', 30, 20
            meal.preparation_recorded = meal.serving_recorded = True
            meal.save()
            listing = SurplusListing.objects.create(meal=meal, offered=8, committed=2, pickup_start=now-timedelta(minutes=15), deadline=now+timedelta(hours=4), location='Campus kitchen · Garden counter', status='open', handling_confirmed=True, creator=kitchen, notes='Collect from the garden counter. Please ask staff about ingredients. Synthetic demonstration data.')
            Reservation.objects.create(student=students[1], listing=listing)
            Reservation.objects.create(student=students[2], listing=listing, status='collected', collected_at=now-timedelta(minutes=5), collecting_staff=kitchen)
            Reservation.objects.create(student=students[3], listing=listing, status='cancelled', cancelled_at=now-timedelta(minutes=3))
        # Cook-along meal: intentionally planned with elapsed timings for a no-wait staff demo.
        menu = MENU[1]
        meal, created = MealService.objects.get_or_create(date=today, meal_type='dinner', defaults=dict(title='Demo cook-along · Golden lentil dal', description=menu[1], allergens=menu[2], dietary_tags=menu[3], capacity=32, cutoff=now-timedelta(hours=3), service_start=now-timedelta(hours=2), service_end=now-timedelta(minutes=10), creator=kitchen, synthetic=True, event_notes='Synthetic no-wait demonstration service. Start preparation, record 20 prepared and 19 served, then offer one portion.'))
        if created:
            intentions(meal)
            created_count += 1
        for offset in range(1, 5):
            for kind, hour in [('breakfast', 8), ('lunch', 12), ('dinner', 18)]:
                day = today + timedelta(days=offset)
                menu = MENU[(offset+hour) % len(MENU)]
                meal, created = MealService.objects.get_or_create(date=day, meal_type=kind, defaults=dict(title=menu[0], description=menu[1], allergens=menu[2], dietary_tags=menu[3], capacity=32, cutoff=at(day, hour-2), service_start=at(day, hour), service_end=at(day, hour+1), creator=kitchen, synthetic=True))
                if created:
                    intentions(meal)
                    created_count += 1
        self.stdout.write(self.style.SUCCESS(f'Demo ready: {created_count} new meals; 32 synthetic students, 2 staff, 1 admin. Existing records preserved.'))
        self.stdout.write('Local accounts: student01 / kitchen1 / kitchen2 / admin. Password: SecondServe!2026')
