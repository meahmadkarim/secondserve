"""Authoritative mutations. SQLite BEGIN IMMEDIATE serialises writers before reads.

Inventory counter and reservation rows always commit or roll back together.
Views must not duplicate these rules. No select_for_update assumptions on SQLite.
"""
from decimal import Decimal
from math import ceil
from datetime import timedelta
from functools import wraps
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction, OperationalError, IntegrityError
from django.db.models import F, Count, Q, Sum
from django.contrib.auth.models import User
from django.utils import timezone
from .models import (Profile, CampusSettings, MealService, MealIntention,
                     ForecastSnapshot, SurplusListing, Reservation, AuditEvent, PickupAttempt)


def atomic_operation(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            with transaction.atomic():
                return fn(*args, **kwargs)
        except OperationalError as exc:
            if 'locked' in str(exc).lower():
                raise ValidationError('The kitchen is busy updating this record. Please try again.') from exc
            raise
    return wrapped


def require_role(user, *roles):
    if not user.is_authenticated or not user.is_active or user.profile.role not in roles:
        raise PermissionDenied


def audit(actor, action, obj, changes=None, reason=''):
    AuditEvent.objects.create(actor=actor, action=action, object_type=type(obj).__name__, object_id=obj.pk, changes=changes or {}, reason=reason)


def number(value):
    try:
        result = int(value)
        if result < 0 or str(result) != str(value).strip():
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise ValidationError('Enter a nonnegative whole number.')


@atomic_operation
def set_intention(user, meal_id, response, now=None):
    require_role(user, 'student')
    meal = MealService.objects.get(pk=meal_id)
    if (now or timezone.now()) >= meal.cutoff or meal.state != 'planned':
        raise ValidationError('The kitchen is preparing this meal; the intention cutoff has passed.')
    if response not in dict(MealIntention.RESPONSES):
        raise ValidationError('Choose a valid response.')
    result, _ = MealIntention.objects.update_or_create(student=user, meal=meal, defaults={'response': response})
    return result


def forecast(meal):
    campus = CampusSettings.current()
    eligible = User.objects.filter(is_active=True, profile__role='student')
    responses = meal.intentions.filter(student__in=eligible).values('response').annotate(n=Count('id'))
    counts = {key: 0 for key in dict(MealIntention.RESPONSES)}
    counts.update({r['response']: r['n'] for r in responses})
    counts['eligible'] = eligible.count()
    counts['no_response'] = max(0, counts['eligible'] - sum(counts[k] for k in dict(MealIntention.RESPONSES)))
    def rate(v):
        return max(Decimal(0), min(Decimal(1), Decimal(v)))
    a, u, n, buffer = map(rate, [campus.attendance_rate, campus.unsure_rate, campus.no_response_rate, campus.buffer])
    estimate = min(counts['eligible'], ceil(counts['attending'] * a + counts['unsure'] * u + counts['no_response'] * n))
    buffered = ceil(estimate * (1 + buffer))
    return {**counts, 'attendance_rate': str(a), 'unsure_rate': str(u), 'no_response_rate': str(n), 'buffer': str(buffer), 'capacity': meal.capacity, 'estimated_demand': estimate, 'buffered_quantity': buffered, 'recommended': min(meal.capacity, buffered), 'capacity_constrained': buffered > meal.capacity}


@atomic_operation
def save_meal(user, cleaned, meal_id=None, reason=''):
    require_role(user, 'kitchen', 'admin')
    meal = MealService.objects.get(pk=meal_id) if meal_id else MealService(creator=user)
    if meal.pk and meal.state in ['closed', 'cancelled']:
        raise ValidationError('Closed or cancelled services cannot be edited.')
    significant = ['title', 'description', 'allergens', 'dietary_tags']
    changed = meal.pk and any(getattr(meal, k) != cleaned[k] for k in significant)
    menu_before = {k: getattr(meal, k) for k in significant} if changed else {}
    if changed and meal.intentions.exists():
        if not reason.strip():
            raise ValidationError('Explain the menu change; students will see a visible change notice.')
        meal.menu_changed = True
    if meal.pk and meal.state != 'planned' and any(getattr(meal, k) != cleaned[k] for k in ['date', 'meal_type', 'cutoff', 'service_start', 'service_end']):
        raise ValidationError('Service timings are fixed after preparation begins.')
    old_capacity = meal.capacity
    for key, value in cleaned.items():
        setattr(meal, key, value)
    meal.full_clean()
    meal.save()
    audit(user, 'Meal updated' if meal_id else 'Meal created', meal, {'capacity_before': old_capacity, 'capacity_after': meal.capacity, 'menu_changed': bool(changed), 'menu_before': menu_before, 'menu_after': {k: getattr(meal, k) for k in significant} if changed else {}}, reason)
    return meal


@atomic_operation
def start_preparation(user, meal_id, override=None, reason=''):
    require_role(user, 'kitchen', 'admin')
    meal = MealService.objects.get(pk=meal_id)
    if meal.state != 'planned':
        raise ValidationError('Only planned meals can start preparation.')
    data = forecast(meal)
    amount = None if override in (None, '') else number(override)
    if amount is not None and (amount > meal.capacity or not reason.strip()):
        raise ValidationError('An override needs a reason and must respect capacity. Edit capacity explicitly first if needed.')
    ForecastSnapshot.objects.create(meal=meal, data=data, override=amount, reason=reason)
    meal.state = 'preparing'
    meal.save()
    audit(user, 'Preparation started', meal, data, reason)


@atomic_operation
def start_regular_service(user, meal_id):
    require_role(user, 'kitchen', 'admin')
    meal = MealService.objects.get(pk=meal_id)
    if meal.state != 'preparing' or not meal.preparation_recorded:
        raise ValidationError('Record prepared portions before starting regular service.')
    meal.state = 'regular_service'
    meal.save()
    audit(user, 'Regular service started', meal)


@atomic_operation
def record_totals(user, meal_id, prepared, served, sold_out=False, adjustment=False, reason=''):
    require_role(user, 'kitchen', 'admin')
    meal = MealService.objects.get(pk=meal_id)
    if meal.state not in ['preparing', 'regular_service', 'surplus_collection']:
        raise ValidationError('Start preparation before recording portions.')
    if meal.state == 'surplus_collection' and (not adjustment or not reason.strip()):
        raise ValidationError('Totals are frozen. Use an explicit adjustment with a reason.')
    prepared, served = number(prepared), number(served)
    if served > prepared or prepared > meal.capacity:
        raise ValidationError('Regular servings must be ≤ prepared portions ≤ capacity.')
    if meal.state == 'preparing' and served:
        raise ValidationError('Start regular service before recording servings.')
    listing = SurplusListing.objects.filter(meal=meal).first()
    if listing and prepared - served < listing.offered:
        raise ValidationError('Adjustment would leave fewer unserved portions than offered. Adjust the listing first.')
    before = {'prepared': meal.prepared, 'served': meal.served}
    meal.prepared, meal.served, meal.sold_out = prepared, served, sold_out
    meal.preparation_recorded = True
    if meal.state in ['regular_service', 'surplus_collection']:
        meal.serving_recorded = True
    meal.full_clean()
    meal.save()
    audit(user, 'Frozen totals adjusted' if adjustment else 'Cumulative totals recorded', meal, {'before': before, 'prepared': prepared, 'served': served}, reason)


@atomic_operation
def publish_listing(user, meal_id, cleaned, now=None):
    require_role(user, 'kitchen', 'admin')
    now = now or timezone.now()
    meal = MealService.objects.get(pk=meal_id)
    listing = SurplusListing.objects.filter(meal=meal).first()
    if meal.state not in ['regular_service', 'surplus_collection'] or not meal.preparation_recorded or not meal.serving_recorded:
        raise ValidationError('Record prepared and regular-served totals during regular service first.')
    if meal.service_end > now:
        raise ValidationError('Wait until normal meal service ends before offering unserved portions.')
    if listing and listing.status in ['withdrawn', 'closed']:
        raise ValidationError('A withdrawn or closed listing cannot be reopened.')
    if cleaned['deadline'] <= now or (listing and listing.deadline <= now):
        raise ValidationError('An expired listing cannot be reopened or extended.')
    if cleaned['pickup_start'] < meal.service_end:
        raise ValidationError('Pickup must begin after regular service ends.')
    if listing and listing.reservations.exists() and (listing.deadline != cleaned['deadline'] or listing.pickup_start != cleaned['pickup_start']):
        raise ValidationError('Pickup times are fixed once reservations exist; withdraw if unsafe.')
    listing = listing or SurplusListing(meal=meal, creator=user)
    for key, value in cleaned.items():
        setattr(listing, key, value)
    listing.full_clean()
    listing.save()
    meal.state = 'surplus_collection'
    meal.save()
    audit(user, 'Surplus listing saved', listing, {'offered': listing.offered, 'status': listing.status})
    return listing


@atomic_operation
def reserve(user, listing_id, now=None):
    require_role(user, 'student')
    now = now or timezone.now()
    listing = SurplusListing.objects.select_related('meal').get(pk=listing_id)
    if listing.status != 'open' or listing.deadline <= now or listing.meal.state != 'surplus_collection':
        raise ValidationError('This listing is no longer accepting reservations.')
    if Reservation.objects.filter(student=user, listing__meal=listing.meal, status__in=['reserved', 'collected']).exists():
        raise ValidationError('You already reserved or collected a portion for this meal.')
    # First conditional write wins. Rollback also restores the counter if create fails.
    changed = SurplusListing.objects.filter(pk=listing.pk, status='open', deadline__gt=now, committed__lt=F('offered')).update(committed=F('committed') + 1)
    if not changed:
        raise ValidationError('All offered portions have been reserved.')
    result = Reservation.objects.create(student=user, listing=listing)
    audit(user, 'Portion reserved', result)
    return result


@atomic_operation
def cancel_reservation(user, reservation_id, now=None):
    now = now or timezone.now()
    reservation = Reservation.objects.select_related('listing').get(pk=reservation_id)
    require_role(user, 'student')
    if reservation.student_id != user.pk:
        raise PermissionDenied
    if reservation.status != 'reserved' or reservation.listing.deadline <= now or reservation.listing.status in ['withdrawn', 'closed']:
        raise ValidationError('This reservation can no longer be cancelled.')
    reservation.status = 'cancelled'
    reservation.cancelled_at = now
    reservation.save()
    SurplusListing.objects.filter(pk=reservation.listing_id).update(committed=F('committed') - 1)
    audit(user, 'Reservation cancelled', reservation)


@atomic_operation
def withdraw_listing(user, listing_id, reason):
    require_role(user, 'kitchen', 'admin')
    listing = SurplusListing.objects.get(pk=listing_id)
    if listing.status in ['withdrawn', 'closed'] or not reason.strip():
        raise ValidationError('An active listing and a withdrawal reason are required.')
    expire_reservations()  # Preserve past-deadline no-shows before invalidating live pickups.
    listing.reservations.filter(status='reserved').update(status='withdrawn')
    listing.committed = listing.reservations.filter(status='collected').count()
    listing.status, listing.withdrawal_reason = 'withdrawn', reason
    listing.save()
    audit(user, 'Listing withdrawn; outstanding reservations invalidated', listing, reason=reason)


def pickup_problem(reservation, now=None):
    now = now or timezone.now()
    if reservation.status == 'collected':
        return 'Already collected.'
    if reservation.listing.status == 'withdrawn' or reservation.status == 'withdrawn':
        return 'This listing has been withdrawn.'
    if reservation.status == 'cancelled':
        return 'This reservation was cancelled.'
    if reservation.status == 'expired' or reservation.listing.deadline <= now:
        return 'This reservation has expired.'
    if reservation.status != 'reserved' or reservation.listing.status not in ['open', 'paused'] or reservation.listing.meal.state != 'surplus_collection':
        return 'Collection is closed.'
    if now < reservation.listing.pickup_start:
        return 'The pickup window is not yet open.'
    return ''


@atomic_operation
def lookup_pickup(user, code, now=None):
    """Persisted per-staff limit, shared by lookup and confirmation, across sessions."""
    require_role(user, 'kitchen', 'admin')
    now = now or timezone.now()
    attempt, _ = PickupAttempt.objects.get_or_create(staff=user)
    if now - attempt.window_start >= timedelta(minutes=5):
        attempt.failures, attempt.window_start = 0, now
    if attempt.failures >= 10:
        attempt.save()
        return None, 'Too many invalid attempts. Try again after five minutes.'
    result = Reservation.objects.select_related('student', 'listing__meal').filter(code=code.strip().upper()).first()
    if result is None:
        attempt.failures += 1
    attempt.save()
    return result, '' if result else 'Unknown pickup code. Check the code and try again.'


@atomic_operation
def collect(user, code, now=None):
    require_role(user, 'kitchen', 'admin')
    reservation = Reservation.objects.select_related('listing__meal').filter(code=code.strip().upper()).first()
    if not reservation:
        raise ValidationError('Unknown pickup code.')
    problem = pickup_problem(reservation, now)
    if problem:
        raise ValidationError(problem)
    reservation.status = 'collected'
    reservation.collected_at = now or timezone.now()
    reservation.collecting_staff = user
    reservation.save()
    audit(user, 'Surplus collected', reservation)
    return reservation


@atomic_operation
def expire_reservations(now=None):
    now = now or timezone.now()
    listing_ids = Reservation.objects.filter(status='reserved', listing__deadline__lte=now).values_list('listing_id', flat=True).distinct()
    count = 0
    for listing_id in list(listing_ids):
        n = Reservation.objects.filter(listing_id=listing_id, status='reserved').update(status='expired')
        SurplusListing.objects.filter(pk=listing_id).update(committed=F('committed') - n)
        count += n
    return count


@atomic_operation
def close_meal(user, meal_id, discarded, retained, reason='', now=None):
    require_role(user, 'kitchen', 'admin')
    now = now or timezone.now()
    meal = MealService.objects.get(pk=meal_id)
    if meal.state not in ['regular_service', 'surplus_collection'] or not meal.serving_recorded:
        raise ValidationError('Only a completed regular service can be reconciled.')
    if meal.service_end > now:
        raise ValidationError('Wait until regular service has ended.')
    expire_reservations(now)
    listing = SurplusListing.objects.filter(meal=meal).first()
    if listing and listing.reservations.filter(status='reserved').exists():
        raise ValidationError('Resolve outstanding reservations: wait for expiry or withdraw with a reason.')
    discarded, retained = number(discarded), number(retained)
    if meal.prepared != meal.served + meal.collected + discarded + retained:
        raise ValidationError(f'Outcomes must reconcile. {meal.prepared - meal.served - meal.collected} portions remain to record as discarded or retained/transferred.')
    meal.discarded, meal.retained, meal.state = discarded, retained, 'closed'
    meal.save()
    if listing and listing.status != 'withdrawn':
        listing.status = 'closed'
        listing.save()
    audit(user, 'Meal reconciled and closed', meal, {'discarded': discarded, 'retained': retained}, reason)


@atomic_operation
def cancel_meal(user, meal_id, reason):
    require_role(user, 'kitchen', 'admin')
    meal = MealService.objects.get(pk=meal_id)
    if meal.state not in ['planned', 'preparing'] or meal.prepared or not reason.strip():
        raise ValidationError('Only an unprepared meal can be cancelled, with a reason. Reconcile prepared food instead.')
    meal.state = 'cancelled'
    meal.save()
    audit(user, 'Meal cancelled', meal, reason=reason)


@atomic_operation
def change_role(actor, user_id, role):
    require_role(actor, 'admin')
    if role not in dict(Profile.ROLES):
        raise ValidationError('Choose a valid role.')
    user = User.objects.get(pk=user_id)
    if user.pk == actor.pk and role != 'admin':
        raise ValidationError('You cannot remove your own administrator access.')
    old = user.profile.role
    user.profile.role = role
    user.profile.save()
    audit(actor, 'Role changed', user, {'before': old, 'after': role})


def report(meals, now=None):
    now = now or timezone.now()
    rows, errors, constrained = [], [], 0
    totals = dict(prepared=0, served=0, offered=0, collected=0, discarded=0, retained=0, awaiting=0, expired=0, cancelled=0, completed_collected=0)
    for meal in meals.exclude(state='cancelled'):
        listing = SurplusListing.objects.filter(meal=meal).first()
        collected = meal.collected
        expired = cancelled = completed = 0
        if listing:
            cancelled = listing.reservations.filter(status='cancelled').count()
            if listing.deadline <= now:
                expired = listing.reservations.filter(status__in=['expired', 'reserved']).count()
                completed = collected
        error = None
        constrained_record = meal.sold_out or (meal.preparation_recorded and meal.served >= meal.prepared and meal.prepared > 0) or (hasattr(meal, 'snapshot') and meal.snapshot.data.get('capacity_constrained', False))
        if meal.state == 'closed' and hasattr(meal, 'snapshot'):
            # Exclude sold-out observations from MAE, flag them separately.
            if constrained_record:
                constrained += 1
            else:
                error = abs(meal.snapshot.data['estimated_demand'] - meal.served)
                errors.append(error)
        values = dict(prepared=meal.prepared, served=meal.served, offered=listing.offered if listing else 0, collected=collected, discarded=meal.discarded, retained=meal.retained, awaiting=meal.awaiting, expired=expired, cancelled=cancelled, completed_collected=completed)
        for key, value in values.items():
            totals[key] += value
        rows.append({'meal': meal, **values, 'error': error, 'constrained': constrained_record})
    def percentage(n, d):
        return round(n / d * 100, 1) if d else 0
    return {'rows': rows, 'totals': totals, 'discard_rate': percentage(totals['discarded'], totals['prepared']), 'collection_rate': percentage(totals['completed_collected'], totals['completed_collected'] + totals['expired']), 'mae': round(sum(errors) / len(errors), 2) if errors else None, 'evaluated': len(errors), 'constrained_count': constrained, 'synthetic': any(r['meal'].synthetic for r in rows)}


def csv_safe(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')) or value.startswith(('\t', '\r', '\n')) else value
