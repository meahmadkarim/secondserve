from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from io import StringIO
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.management import call_command
from django.db import connections, close_old_connections
from django.test import TestCase, TransactionTestCase, Client, override_settings
from django.urls import reverse
from .models import (MealService, MealIntention, ForecastSnapshot, SurplusListing,
                     Reservation, CampusSettings, AuditEvent)
from . import services as s

NOW = datetime(2030, 4, 15, 12, tzinfo=dt_timezone.utc)


def user(name, role='student'):
    u = User.objects.create_user(name, password='Testing-password!123', first_name=name)
    u.profile.role = role
    u.profile.save()
    return u


def meal(staff, **kwargs):
    defaults = dict(date=NOW.date(), meal_type='lunch', title='Garden bowl', description='Rice and greens', capacity=110, cutoff=NOW-timedelta(hours=4), service_start=NOW-timedelta(hours=3), service_end=NOW-timedelta(hours=1), creator=staff)
    defaults.update(kwargs)
    return MealService.objects.create(**defaults)


def listing(staff, m, **kwargs):
    defaults = dict(meal=m, offered=2, pickup_start=NOW-timedelta(minutes=30), deadline=NOW+timedelta(hours=1), location='Garden counter', status='open', handling_confirmed=True, creator=staff)
    defaults.update(kwargs)
    return SurplusListing.objects.create(**defaults)


class CoreTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student = user('student')
        cls.other = user('other')
        cls.staff = user('staff', 'kitchen')
        cls.admin = user('admin', 'admin')
        cls.m = meal(cls.staff, state='surplus_collection', prepared=10, served=8, preparation_recorded=True, serving_recorded=True)
        cls.l = listing(cls.staff, cls.m)

    def reserve(self, u=None):
        return s.reserve(u or self.student, self.l.pk, NOW)

    def test_student_cannot_access_kitchen_or_admin(self):
        self.client.force_login(self.student)
        for url in ['/kitchen/', '/kitchen/pickup/', '/administration/users/', '/administration/settings/', '/administration/audit/', '/reports/']:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        response = self.client.post(reverse('meal_action', args=[self.m.pk, 'totals']), {'prepared': 1, 'served': 0})
        self.assertEqual(response.status_code, 403)

    def test_staff_cannot_manage_administrator_settings_or_csv(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post('/administration/settings/', {}).status_code, 403)
        self.assertEqual(self.client.get('/administration/users/').status_code, 403)
        self.assertEqual(self.client.get('/reports/?format=csv').status_code, 403)

    def test_registration_cannot_escalate(self):
        response = self.client.post('/register/', {'username': 'newstudent', 'first_name': 'New', 'password1': 'Hiking-is-good!123', 'password2': 'Hiking-is-good!123', 'role': 'admin', 'is_superuser': 'true'})
        self.assertEqual(response.status_code, 302)
        u = User.objects.get(username='newstudent')
        self.assertEqual(u.profile.role, 'student')
        self.assertFalse(u.is_superuser)
        self.assertFalse(u.is_staff)
        self.assertTrue(u.check_password('Hiking-is-good!123'))

    def test_reservation_ownership_and_code_privacy(self):
        r = self.reserve()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('reservation', args=[r.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('cancel', args=[r.pk])).status_code, 403)
        self.assertNotContains(self.client.get(reverse('listing', args=[self.l.pk])), r.code)
        self.client.force_login(self.student)
        self.assertContains(self.client.get(reverse('reservation', args=[r.pk])), r.code)

    def test_mutations_require_post_and_csrf(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse('reserve', args=[self.l.pk])).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.student)
        self.assertEqual(client.post(reverse('reserve', args=[self.l.pk])).status_code, 403)

    def test_reserving_consumes_capacity_but_not_collected_metric(self):
        self.reserve()
        self.l.refresh_from_db()
        self.assertEqual(self.l.committed, 1)
        data = s.report(MealService.objects.all(), NOW)
        self.assertEqual(data['totals']['collected'], 0)
        self.assertEqual(data['totals']['awaiting'], 2)

    def test_duplicate_reservation_rejected(self):
        self.reserve()
        with self.assertRaises(ValidationError):
            self.reserve()
        self.assertEqual(Reservation.objects.count(), 1)

    def test_cancellation_releases_and_allows_reserving_again(self):
        r = self.reserve()
        s.cancel_reservation(self.student, r.pk, NOW)
        self.l.refresh_from_db()
        self.assertEqual(self.l.committed, 0)
        again = self.reserve()
        self.assertNotEqual(r.code, again.code)
        self.assertEqual(Reservation.objects.filter(status='reserved').count(), 1)

    def test_collection_stays_consumed_and_changes_metrics(self):
        r = self.reserve()
        s.collect(self.staff, r.code, NOW)
        self.l.refresh_from_db()
        self.assertEqual(self.l.committed, 1)
        r.refresh_from_db()
        self.assertEqual(r.collecting_staff, self.staff)
        self.assertEqual(r.collected_at, NOW)
        with self.assertRaises(ValidationError):
            s.cancel_reservation(self.student, r.pk, NOW)
        with self.assertRaises(ValidationError):
            self.reserve()
        self.assertEqual(s.report(MealService.objects.all(), NOW)['totals']['collected'], 1)

    def test_full_capacity_rejected(self):
        self.reserve()
        self.reserve(self.other)
        third = user('third')
        with self.assertRaises(ValidationError):
            self.reserve(third)
        self.l.refresh_from_db()
        self.assertEqual(self.l.committed, 2)

    def test_expired_listing_rejects_without_expiry_command(self):
        with self.assertRaises(ValidationError):
            s.reserve(self.student, self.l.pk, self.l.deadline)

    def test_paused_draft_closed_listings_reject_reservations(self):
        for status in ['draft', 'paused', 'closed', 'withdrawn']:
            self.l.status = status
            self.l.save()
            with self.subTest(status=status), self.assertRaises(ValidationError):
                self.reserve()

    def test_withdrawal_invalidates_and_keeps_collected(self):
        r = self.reserve()
        second = self.reserve(self.other)
        s.collect(self.staff, second.code, NOW)
        s.withdraw_listing(self.staff, self.l.pk, 'Handling concern')
        r.refresh_from_db()
        self.l.refresh_from_db()
        self.assertEqual(r.status, 'withdrawn')
        self.assertEqual(self.l.committed, 1)
        self.assertEqual(self.m.collected, 1)
        with self.assertRaises(ValidationError):
            s.collect(self.staff, r.code, NOW)
        with self.assertRaises(ValidationError):
            self.reserve()

    def test_expired_reservation_cannot_collect_or_cancel(self):
        r = self.reserve()
        with self.assertRaises(ValidationError):
            s.collect(self.staff, r.code, self.l.deadline)
        with self.assertRaises(ValidationError):
            s.cancel_reservation(self.student, r.pk, self.l.deadline)
        with patch('django.utils.timezone.now', return_value=self.l.deadline):
            self.assertEqual(r.effective_status, 'expired')
        data = s.report(MealService.objects.all(), self.l.deadline)
        self.assertEqual(data['totals']['expired'], 1)
        self.assertEqual(Reservation.objects.get(pk=r.pk).status, 'reserved')

    def test_expiry_command_idempotent(self):
        r = self.reserve()
        with patch('django.utils.timezone.now', return_value=self.l.deadline):
            call_command('expire_reservations', stdout=StringIO())
            call_command('expire_reservations', stdout=StringIO())
        r.refresh_from_db()
        self.l.refresh_from_db()
        self.assertEqual(r.status, 'expired')
        self.assertEqual(self.l.committed, 0)
        with self.assertRaises(ValidationError):
            s.collect(self.staff, r.code, self.l.deadline)

    def test_collection_before_window_and_duplicate_rejected(self):
        r = self.reserve()
        with self.assertRaises(ValidationError):
            s.collect(self.staff, r.code, self.l.pickup_start-timedelta(seconds=1))
        s.collect(self.staff, r.code, NOW)
        with self.assertRaises(ValidationError):
            s.collect(self.staff, r.code, NOW)
        self.assertEqual(self.m.collected, 1)

    def test_cancelled_reservation_not_collectible(self):
        r = self.reserve()
        s.cancel_reservation(self.student, r.pk, NOW)
        with self.assertRaises(ValidationError):
            s.collect(self.staff, r.code, NOW)

    def test_invalid_code_safe_and_rate_limited(self):
        for _ in range(10):
            reservation, problem = s.lookup_pickup(self.staff, 'nonsense', NOW)
            self.assertIsNone(reservation)
            self.assertIn('Unknown', problem)
        r = self.reserve()
        self.assertIn('Too many', s.lookup_pickup(self.staff, r.code, NOW)[1])
        self.assertEqual(s.lookup_pickup(self.staff, r.code, NOW+timedelta(minutes=5))[0].pk, r.pk)

    def test_invalid_code_http_response(self):
        self.client.force_login(self.staff)
        response = self.client.post('/kitchen/pickup/', {'code': '<script>bad</script>', 'confirm': 'yes'})
        self.assertContains(response, 'Unknown pickup code')
        self.assertNotContains(response, '<script>bad</script>')

    def test_servings_cannot_exceed_preparation(self):
        with self.assertRaises(ValidationError):
            s.record_totals(self.staff, self.m.pk, 10, 11, adjustment=True, reason='Check')
        self.m.refresh_from_db()
        self.assertEqual(self.m.served, 8)

    def test_totals_frozen_and_audited_adjustment(self):
        with self.assertRaises(ValidationError):
            s.record_totals(self.staff, self.m.pk, 11, 8)
        s.record_totals(self.staff, self.m.pk, 11, 8, adjustment=True, reason='One additional tray counted')
        self.assertTrue(AuditEvent.objects.filter(action='Frozen totals adjusted', reason='One additional tray counted').exists())
        with self.assertRaises(ValidationError):
            s.record_totals(self.staff, self.m.pk, 9, 8, adjustment=True, reason='Invalid correction')

    def test_invalid_quantities_rejected(self):
        for bad in ['-1', '1.5', 'NaN', '', '1e4']:
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                s.record_totals(self.staff, self.m.pk, bad, 0, adjustment=True, reason='Test')

    def listing_data(self, **kwargs):
        data = {k: getattr(self.l, k) for k in ['offered', 'pickup_start', 'deadline', 'location', 'status', 'handling_confirmed', 'notes']}
        data.update(kwargs)
        return data

    def test_offering_cannot_exceed_unserved(self):
        with self.assertRaises(ValidationError):
            s.publish_listing(self.staff, self.m.pk, self.listing_data(offered=3), NOW)

    def test_offer_cannot_drop_below_committed(self):
        self.reserve()
        with self.assertRaises(ValidationError):
            s.publish_listing(self.staff, self.m.pk, self.listing_data(offered=0), NOW)

    def test_food_handling_confirmation_required(self):
        with self.assertRaises(ValidationError):
            s.publish_listing(self.staff, self.m.pk, self.listing_data(handling_confirmed=False), NOW)

    def test_expired_listing_cannot_reopen_with_new_deadline(self):
        with self.assertRaises(ValidationError):
            s.publish_listing(self.staff, self.m.pk, self.listing_data(deadline=self.l.deadline+timedelta(days=1)), self.l.deadline)

    def test_closure_requires_reconciliation(self):
        with self.assertRaises(ValidationError):
            s.close_meal(self.staff, self.m.pk, 0, 0, now=NOW)
        s.close_meal(self.staff, self.m.pk, 1, 1, now=NOW)
        self.m.refresh_from_db()
        self.assertEqual(self.m.state, 'closed')
        self.assertEqual(self.m.awaiting, 0)

    def test_uncollected_reservations_block_closure(self):
        self.reserve()
        with self.assertRaises(ValidationError):
            s.close_meal(self.staff, self.m.pk, 1, 1, now=NOW)
        s.close_meal(self.staff, self.m.pk, 1, 1, now=self.l.deadline)
        self.assertEqual(Reservation.objects.get().status, 'expired')
        self.m.refresh_from_db()
        self.assertEqual(self.m.discarded, 1)
        self.assertEqual(self.m.retained, 1)

    def test_forecast_evaluation_uses_unbuffered_snapshot(self):
        self.m.state = 'closed'
        self.m.discarded = 1
        self.m.retained = 1
        self.m.save()
        ForecastSnapshot.objects.create(meal=self.m, data={'estimated_demand': 10, 'recommended': 11})
        result = s.report(MealService.objects.all(), NOW)
        self.assertEqual(result['mae'], 2)
        self.m.sold_out = True
        self.m.save()
        result = s.report(MealService.objects.all(), NOW)
        self.assertIsNone(result['mae'])
        self.assertEqual(result['constrained_count'], 1)

    def test_report_zero_denominators_no_history(self):
        result = s.report(MealService.objects.none(), NOW)
        self.assertEqual(result['collection_rate'], 0)
        self.assertEqual(result['discard_rate'], 0)
        self.assertIsNone(result['mae'])
        self.assertEqual(result['evaluated'], 0)

    def test_collection_rate_uses_completed_windows_excludes_cancellations(self):
        r = self.reserve()
        s.collect(self.staff, r.code, NOW)
        r2 = self.reserve(self.other)
        self.assertEqual(s.report(MealService.objects.all(), NOW)['collection_rate'], 0)
        self.assertEqual(s.report(MealService.objects.all(), self.l.deadline)['collection_rate'], 50)
        s.cancel_reservation(self.other, r2.pk, NOW)
        result = s.report(MealService.objects.all(), self.l.deadline)
        self.assertEqual(result['collection_rate'], 100)
        self.assertEqual(result['totals']['cancelled'], 1)

    def test_report_date_filter_and_csv_formula_injection(self):
        self.m.title = '=HYPERLINK("https://example.org")'
        self.m.save()
        self.client.force_login(self.admin)
        url = '/reports/?start=2030-04-15&end=2030-04-15'
        self.assertContains(self.client.get(url), '=HYPERLINK')
        self.assertNotContains(self.client.get('/reports/?start=2030-04-16'), '=HYPERLINK')
        response = self.client.get(url+'&format=csv')
        self.assertContains(response, "'=HYPERLINK")
        self.assertEqual(self.client.get('/reports/?start=2030-05-01&end=2030-04-01&format=csv').status_code, 400)
        for value in ['=SUM(1)', '+cmd', '-cmd', '@cmd', ' \t=cmd', '\n=cmd']:
            self.assertTrue(s.csv_safe(value).startswith("'"))

    def test_withdraw_after_deadline_preserves_no_show(self):
        r = self.reserve()
        with patch('django.utils.timezone.now', return_value=self.l.deadline):
            s.withdraw_listing(self.staff, self.l.pk, 'Window ended; food retained')
        r.refresh_from_db()
        self.assertEqual(r.status, 'expired')
        self.assertEqual(s.report(MealService.objects.all(), self.l.deadline)['totals']['expired'], 1)

    def test_early_publication_rejected(self):
        self.m.service_end = NOW + timedelta(minutes=10)
        self.m.save()
        with self.assertRaises(ValidationError):
            s.publish_listing(self.staff, self.m.pk, self.listing_data(), NOW)

    def test_missing_records_reject_publication(self):
        self.m.preparation_recorded = False
        self.m.save()
        with self.assertRaises(ValidationError):
            s.publish_listing(self.staff, self.m.pk, self.listing_data(), NOW)

    def test_staff_only_collection_and_student_only_reservation(self):
        r = self.reserve()
        with self.assertRaises(PermissionDenied):
            s.collect(self.student, r.code, NOW)
        with self.assertRaises(PermissionDenied):
            s.reserve(self.staff, self.l.pk, NOW)

    def test_all_role_pages_render(self):
        cases = [(self.student, ['/home/', '/meals/', '/surplus/', '/reservations/', '/profile/']), (self.staff, ['/kitchen/', '/kitchen/pickup/', '/reports/', f'/kitchen/meals/{self.m.pk}/', f'/kitchen/meals/{self.m.pk}/edit/', f'/kitchen/meals/{self.m.pk}/surplus/', f'/kitchen/meals/{self.m.pk}/reconcile/', '/kitchen/meals/new/']), (self.admin, ['/administration/users/', '/administration/settings/', '/administration/audit/', '/reports/'])]
        for actor, urls in cases:
            self.client.force_login(actor)
            for url in urls:
                with self.subTest(role=actor.username, url=url):
                    self.assertEqual(self.client.get(url).status_code, 200)


class IntentionForecastTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student = user('student')
        cls.other = user('other')
        cls.staff = user('staff', 'kitchen')
        cls.m = meal(cls.staff, cutoff=NOW+timedelta(hours=1), service_start=NOW+timedelta(hours=2), service_end=NOW+timedelta(hours=3))

    def test_create_update_unique_before_cutoff(self):
        s.set_intention(self.student, self.m.pk, 'attending', NOW)
        s.set_intention(self.student, self.m.pk, 'unsure', NOW)
        self.assertEqual(MealIntention.objects.count(), 1)
        self.assertEqual(MealIntention.objects.get().response, 'unsure')

    def test_at_and_after_cutoff_rejected(self):
        for now in [self.m.cutoff, self.m.cutoff+timedelta(seconds=1)]:
            with self.assertRaises(ValidationError):
                s.set_intention(self.student, self.m.pk, 'attending', now)
        self.assertEqual(MealIntention.objects.count(), 0)

    def test_unsure_and_no_response_separate(self):
        s.set_intention(self.student, self.m.pk, 'unsure', NOW)
        result = s.forecast(self.m)
        self.assertEqual(result['unsure'], 1)
        self.assertEqual(result['no_response'], 1)
        self.assertEqual(result['eligible'], 2)

    def test_zero_responses_no_historical_data(self):
        result = s.forecast(self.m)
        self.assertEqual(result['estimated_demand'], 1)
        self.assertEqual(result['recommended'], 2)
        self.assertEqual(ForecastSnapshot.objects.count(), 0)

    def test_no_eligible_students(self):
        User.objects.filter(profile__role='student').update(is_active=False)
        result = s.forecast(self.m)
        self.assertEqual(result['estimated_demand'], 0)
        self.assertEqual(result['recommended'], 0)

    def test_formula_example_rounding_capacity(self):
        User.objects.filter(profile__role='student').delete()
        students = []
        for i in range(150):
            u = User.objects.create(username=f's{i}')  # Unusable passwords; fast synthetic test identities.
            students.append(u)
        MealIntention.objects.bulk_create([MealIntention(student=u, meal=self.m, response='attending' if i < 100 else 'unsure') for i, u in enumerate(students[:120])])
        result = s.forecast(self.m)
        self.assertEqual((result['attending'], result['unsure'], result['no_response']), (100, 20, 30))
        self.assertEqual(result['estimated_demand'], 109)
        self.assertEqual(result['buffered_quantity'], 115)
        self.assertEqual(result['recommended'], 110)
        self.assertTrue(result['capacity_constrained'])

    def test_rates_clamped_even_if_database_configuration_invalid(self):
        c = CampusSettings.current()
        c.attendance_rate = Decimal('-1')
        c.no_response_rate = Decimal('2')
        c.save()
        result = s.forecast(self.m)
        self.assertEqual(result['no_response_rate'], '1')
        self.assertEqual(result['attendance_rate'], '0')
        self.assertLessEqual(result['estimated_demand'], result['eligible'])

    def test_snapshot_stays_frozen_and_invalid_transition_rejected(self):
        s.set_intention(self.student, self.m.pk, 'attending', NOW)
        s.start_preparation(self.staff, self.m.pk)
        original = ForecastSnapshot.objects.get().data
        c = CampusSettings.current()
        c.attendance_rate = Decimal('.1')
        c.buffer = Decimal('.8')
        c.save()
        self.assertEqual(ForecastSnapshot.objects.get().data, original)
        with self.assertRaises(ValidationError):
            s.start_preparation(self.staff, self.m.pk)
        with self.assertRaises(ValidationError):
            s.set_intention(self.student, self.m.pk, 'unsure', NOW)

    def test_override_reason_and_capacity(self):
        with self.assertRaises(ValidationError):
            s.start_preparation(self.staff, self.m.pk, 111, 'Big event')
        with self.assertRaises(ValidationError):
            s.start_preparation(self.staff, self.m.pk, 100, '')
        s.start_preparation(self.staff, self.m.pk, 100, 'Orientation event')
        self.assertEqual(ForecastSnapshot.objects.get().override, 100)

    def test_menu_change_flag_and_reason(self):
        s.set_intention(self.student, self.m.pk, 'attending', NOW)
        fields = ['date', 'meal_type', 'title', 'description', 'allergens', 'dietary_tags', 'capacity', 'cutoff', 'service_start', 'service_end', 'location', 'event_notes']
        data = {k: getattr(self.m, k) for k in fields}
        data['title'] = 'Changed menu'
        with self.assertRaises(ValidationError):
            s.save_meal(self.staff, data, self.m.pk)
        updated = s.save_meal(self.staff, data, self.m.pk, 'Supplier substitution')
        self.assertTrue(updated.menu_changed)
        self.assertTrue(AuditEvent.objects.filter(reason='Supplier substitution').exists())

    def test_model_timing_and_capacity_validation(self):
        self.m.cutoff = self.m.service_start
        with self.assertRaises(ValidationError):
            self.m.full_clean()
        self.m.cutoff = NOW
        self.m.service_end = self.m.service_start
        with self.assertRaises(ValidationError):
            self.m.full_clean()

    def test_lifecycle_records_and_closure(self):
        self.m.cutoff = NOW-timedelta(hours=4)
        self.m.service_start = NOW-timedelta(hours=3)
        self.m.service_end = NOW-timedelta(hours=1)
        self.m.save()
        with self.assertRaises(ValidationError):
            s.start_regular_service(self.staff, self.m.pk)
        s.start_preparation(self.staff, self.m.pk)
        s.record_totals(self.staff, self.m.pk, 10, 0)
        s.start_regular_service(self.staff, self.m.pk)
        s.record_totals(self.staff, self.m.pk, 10, 9)
        data = dict(offered=1, pickup_start=NOW-timedelta(minutes=10), deadline=NOW+timedelta(hours=1), location='Counter', status='open', handling_confirmed=True, notes='')
        l = s.publish_listing(self.staff, self.m.pk, data, NOW)
        r = s.reserve(self.student, l.pk, NOW)
        s.collect(self.staff, r.code, NOW)
        s.close_meal(self.staff, self.m.pk, 0, 0, now=NOW)
        self.m.refresh_from_db()
        self.assertEqual(self.m.state, 'closed')
        self.assertEqual(self.m.awaiting, 0)


class InventoryConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_two_concurrent_attempts_for_final_portion(self):
        staff = user('kitchen', 'kitchen')
        a, b = user('a'), user('b')
        m = meal(staff, state='surplus_collection', prepared=10, served=9, preparation_recorded=True, serving_recorded=True)
        l = listing(staff, m, offered=1)
        barrier = Barrier(2)
        def attempt(pk):
            close_old_connections()
            student = User.objects.get(pk=pk)
            barrier.wait(timeout=10)
            try:
                s.reserve(student, l.pk, NOW)
                return 'reserved'
            except ValidationError as exc:
                return 'rejected: ' + str(exc)
            finally:
                connections['default'].close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, [a.pk, b.pk]))
        self.assertEqual(outcomes.count('reserved'), 1, outcomes)
        self.assertEqual(sum(o.startswith('rejected:') for o in outcomes), 1, outcomes)
        self.assertEqual(Reservation.objects.filter(status='reserved').count(), 1)
        l.refresh_from_db()
        self.assertEqual(l.committed, 1)
        self.assertEqual(l.offered-l.committed, 0)


class SeedTests(TestCase):
    @override_settings(DEBUG=True)
    def test_seed_idempotent_preserves_data_and_invariants(self):
        outsider = user('unrelated')
        call_command('seed_demo', stdout=StringIO())
        counts = (User.objects.count(), MealService.objects.count(), Reservation.objects.count(), MealIntention.objects.count())
        call_command('seed_demo', stdout=StringIO())
        self.assertEqual(counts, (User.objects.count(), MealService.objects.count(), Reservation.objects.count(), MealIntention.objects.count()))
        self.assertTrue(User.objects.filter(pk=outsider.pk).exists())
        self.assertGreaterEqual(User.objects.filter(profile__role='student').count(), 32)
        self.assertEqual(MealService.objects.filter(state='closed').values('date').distinct().count(), 14)
        from django.utils import timezone
        now = timezone.now()
        self.assertTrue(SurplusListing.objects.filter(status='open', pickup_start__lte=now, deadline__gt=now).exists())
        for m in MealService.objects.all():
            m.full_clean()
            if m.state == 'closed':
                self.assertEqual(m.awaiting, 0)
        for l in SurplusListing.objects.all():
            l.full_clean()
            self.assertEqual(l.committed, l.reservations.filter(status__in=['reserved', 'collected']).count())

    @override_settings(DEBUG=False)
    def test_demo_disabled_in_production(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command('seed_demo', stdout=StringIO())
