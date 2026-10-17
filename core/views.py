import csv
from functools import wraps
from datetime import timedelta
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import IntegrityError
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import MealService, MealIntention, SurplusListing, Reservation, CampusSettings, AuditEvent, Profile
from .forms import RegistrationForm, MealForm, ListingForm, TotalsForm, CloseForm, SettingsForm, DateFilterForm
from . import services as svc


def roles(*allowed):
    def decorator(fn):
        @login_required
        @wraps(fn)
        def wrapped(request, *args, **kwargs):
            svc.require_role(request.user, *allowed)
            return fn(request, *args, **kwargs)
        return wrapped
    return decorator


def error_message(exc):
    return ' '.join(exc.messages) if isinstance(exc, ValidationError) else 'This record conflicts with an existing meal. Check the date and type.'


def landing(request):
    return render(request, 'landing.html')


def register(request):
    form = RegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()  # Profile signal always creates a student; role input is ignored.
        login(request, user)
        return redirect('home')
    return render(request, 'form.html', {'form': form, 'title': 'Join the campus table', 'intro': 'Create a student account. Small plans help the kitchen prepare with care.', 'button': 'Create student account'})


@login_required
def home(request):
    if request.user.profile.role == 'admin':
        return redirect('reports')
    if request.user.profile.role == 'kitchen':
        return redirect('kitchen')
    now = timezone.now()
    meals = MealService.objects.filter(service_end__gte=now).exclude(state='cancelled').order_by('service_start')[:6]
    for meal in meals:
        meal.my_response = meal.intentions.filter(student=request.user).first()
    reservations = Reservation.objects.filter(student=request.user, status='reserved', listing__deadline__gt=now).exclude(listing__status__in=['closed', 'withdrawn']).select_related('listing__meal')
    listings = SurplusListing.objects.filter(status='open', deadline__gt=now, meal__state='surplus_collection').select_related('meal')[:4]
    return render(request, 'student_home.html', {'meals': meals, 'reservations': reservations, 'listings': listings})


@login_required
def meals(request):
    upcoming = MealService.objects.filter(service_end__gte=timezone.now()).exclude(state='cancelled').order_by('service_start')
    for item in upcoming:
        item.my_response = item.intentions.filter(student=request.user).first()
    return render(request, 'meals.html', {'meals': upcoming})


@login_required
def meal_detail(request, pk):
    meal = get_object_or_404(MealService, pk=pk)
    intention = meal.intentions.filter(student=request.user).first()
    return render(request, 'meal_detail.html', {'meal': meal, 'intention': intention, 'responses': MealIntention.RESPONSES, 'can_respond': request.user.profile.role == 'student' and meal.state == 'planned' and timezone.now() < meal.cutoff})


@roles('student')
@require_POST
def intention(request, pk):
    get_object_or_404(MealService, pk=pk)
    try:
        svc.set_intention(request.user, pk, request.POST.get('response'))
        messages.success(request, 'Your meal plan is saved. You can update it until the cutoff.')
    except ValidationError as exc:
        messages.error(request, error_message(exc))
    return redirect('meal', pk=pk)


@login_required
def surplus(request):
    listings = SurplusListing.objects.filter(status__in=['open', 'paused'], deadline__gt=timezone.now(), meal__state='surplus_collection').select_related('meal').order_by('deadline')
    return render(request, 'surplus.html', {'listings': listings})


@login_required
def listing_detail(request, pk):
    listing = get_object_or_404(SurplusListing.objects.select_related('meal'), pk=pk)
    return render(request, 'listing.html', {'listing': listing})


@roles('student')
@require_POST
def reserve(request, pk):
    get_object_or_404(SurplusListing, pk=pk)
    try:
        reservation = svc.reserve(request.user, pk)
        messages.success(request, 'One portion is reserved for you. Bring your pickup code during the window.')
        return redirect('reservation', pk=reservation.pk)
    except ValidationError as exc:
        messages.error(request, error_message(exc))
    return redirect('listing', pk=pk)


@roles('student')
def reservations(request):
    return render(request, 'reservations.html', {'reservations': Reservation.objects.filter(student=request.user).select_related('listing__meal').order_by('-created_at')})


@login_required
def reservation_detail(request, pk):
    reservation = get_object_or_404(Reservation.objects.select_related('listing__meal', 'student'), pk=pk)
    if request.user.pk != reservation.student_id and request.user.profile.role not in ['kitchen', 'admin']:
        raise PermissionDenied
    return render(request, 'reservation.html', {'reservation': reservation})


@roles('student')
@require_POST
def cancel(request, pk):
    get_object_or_404(Reservation, pk=pk)
    try:
        svc.cancel_reservation(request.user, pk)
        messages.success(request, 'Reservation cancelled. Thank you for letting the kitchen know.')
    except ValidationError as exc:
        messages.error(request, error_message(exc))
    return redirect('reservation', pk=pk)


@login_required
def profile(request):
    return render(request, 'profile.html', {'intentions_count': MealIntention.objects.filter(student=request.user).count(), 'collected_count': Reservation.objects.filter(student=request.user, status='collected').count(), 'cancelled_count': Reservation.objects.filter(student=request.user, status='cancelled').count()})


@roles('kitchen', 'admin')
def kitchen(request):
    active = MealService.objects.exclude(state__in=['closed', 'cancelled']).order_by('service_start')
    for meal in active:
        meal.forecast = meal.snapshot.data if hasattr(meal, 'snapshot') else svc.forecast(meal)
    return render(request, 'kitchen.html', {'meals': active})


@roles('kitchen', 'admin')
def meal_edit(request, pk=None):
    meal = get_object_or_404(MealService, pk=pk) if pk else None
    # ModelForm operates on a separate instance; service reloads authoritative state.
    form = MealForm(request.POST or None, instance=meal)
    if request.method == 'POST' and form.is_valid():
        cleaned = {k: v for k, v in form.cleaned_data.items() if k != 'change_reason'}
        try:
            meal = svc.save_meal(request.user, cleaned, pk, form.cleaned_data['change_reason'])
            messages.success(request, 'Meal service saved.')
            return redirect('operations', pk=meal.pk)
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, error_message(exc))
    return render(request, 'form.html', {'form': form, 'title': 'Edit meal service' if pk else 'Plan a meal service', 'intro': 'All times use the campus timezone. Menu changes after responses need a reason and display a notice.', 'button': 'Save meal'})


@roles('kitchen', 'admin')
def operations(request, pk):
    meal = get_object_or_404(MealService, pk=pk)
    data = meal.snapshot.data if hasattr(meal, 'snapshot') else svc.forecast(meal)
    return render(request, 'operations.html', {'meal': meal, 'forecast': data, 'totals_form': TotalsForm(initial={'prepared': meal.prepared, 'served': meal.served, 'sold_out': meal.sold_out}), 'active_count': Reservation.objects.filter(listing__meal=meal, status='reserved', listing__deadline__gt=timezone.now()).count()})


@roles('kitchen', 'admin')
@require_POST
def meal_action(request, pk, action):
    get_object_or_404(MealService, pk=pk)
    try:
        if action == 'prepare':
            svc.start_preparation(request.user, pk, request.POST.get('override'), request.POST.get('reason', ''))
        elif action == 'regular':
            svc.start_regular_service(request.user, pk)
        elif action == 'totals':
            form = TotalsForm(request.POST)
            if not form.is_valid():
                raise ValidationError('Enter valid nonnegative cumulative totals.')
            svc.record_totals(request.user, pk, **form.cleaned_data)
        elif action == 'cancel':
            svc.cancel_meal(request.user, pk, request.POST.get('reason', ''))
        else:
            return HttpResponseBadRequest('Unknown action')
        messages.success(request, 'Meal service updated.')
    except ValidationError as exc:
        messages.error(request, error_message(exc))
    return redirect('operations', pk=pk)


@roles('kitchen', 'admin')
def listing_edit(request, pk):
    meal = get_object_or_404(MealService, pk=pk)
    listing = SurplusListing.objects.filter(meal=meal).first()
    initial = {key: getattr(listing, key) for key in ListingForm.base_fields} if listing else {'offered': meal.prepared - meal.served, 'pickup_start': max(timezone.now(), meal.service_end), 'deadline': max(timezone.now(), meal.service_end) + timedelta(hours=1), 'location': meal.location, 'status': 'open'}
    form = ListingForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            svc.publish_listing(request.user, pk, form.cleaned_data)
            messages.success(request, 'Surplus listing saved. Kitchen preparation and regular-serving totals are now frozen.')
            return redirect('operations', pk=pk)
        except ValidationError as exc:
            form.add_error(None, error_message(exc))
    return render(request, 'listing_edit.html', {'form': form, 'meal': meal, 'listing': listing})


@roles('kitchen', 'admin')
@require_POST
def withdraw(request, pk):
    get_object_or_404(SurplusListing, pk=pk)
    try:
        svc.withdraw_listing(request.user, pk, request.POST.get('reason', ''))
        messages.success(request, 'Listing withdrawn. Outstanding reservations are invalidated.')
    except ValidationError as exc:
        messages.error(request, error_message(exc))
    return redirect('listing', pk=pk)


@roles('kitchen', 'admin')
def pickup(request):
    reservation = None
    problem = ''
    code = ''
    if request.method == 'POST':
        code = request.POST.get('code', '')[:64]
        try:
            reservation, problem = svc.lookup_pickup(request.user, code)
            if reservation:
                problem = svc.pickup_problem(reservation)
                if request.POST.get('confirm') == 'yes':
                    reservation = svc.collect(request.user, code)
                    messages.success(request, 'Collection recorded. One surplus portion handed over.')
                    problem = 'Already collected.'
        except ValidationError as exc:
            problem = error_message(exc)
    return render(request, 'pickup.html', {'reservation': reservation, 'problem': problem, 'code': code})


@roles('kitchen', 'admin')
def reconcile(request, pk):
    meal = get_object_or_404(MealService, pk=pk)
    form = CloseForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            svc.close_meal(request.user, pk, **form.cleaned_data)
            messages.success(request, 'All portions reconciled. Meal service closed.')
            return redirect('operations', pk=pk)
        except ValidationError as exc:
            form.add_error(None, error_message(exc))
    return render(request, 'reconcile.html', {'meal': meal, 'form': form})


@roles('kitchen', 'admin')
def reports(request):
    form = DateFilterForm(request.GET)
    selected = MealService.objects.all()
    if form.is_valid():
        if form.cleaned_data.get('start'):
            selected = selected.filter(date__gte=form.cleaned_data['start'])
        if form.cleaned_data.get('end'):
            selected = selected.filter(date__lte=form.cleaned_data['end'])
    else:
        selected = selected.none()
    result = svc.report(selected)
    if request.GET.get('format') == 'csv':
        svc.require_role(request.user, 'admin')
        if not form.is_valid():
            return HttpResponseBadRequest('Invalid date range')
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="secondserve-report.csv"'
        writer = csv.writer(response)
        writer.writerow(['Date', 'Meal', 'State', 'Dataset', 'Prepared', 'Regular served', 'Surplus offered', 'Surplus collected', 'Discarded', 'Retained/transferred', 'Awaiting outcome', 'Unbuffered forecast error', 'Constrained observation'])
        for row in result['rows']:
            m = row['meal']
            writer.writerow([m.date, svc.csv_safe(m.title), m.state, 'Synthetic demonstration data' if m.synthetic else 'Pilot data', *[row[k] for k in ['prepared', 'served', 'offered', 'collected', 'discarded', 'retained', 'awaiting']], row['error'] if row['error'] is not None else '', row['constrained']])
        return response
    return render(request, 'reports.html', {'report': result, 'form': form})


@roles('admin')
def users(request):
    if request.method == 'POST':
        try:
            target_id = int(request.POST.get('user_id', ''))
        except (TypeError, ValueError):
            messages.error(request, 'Choose a valid campus user.')
            return redirect('users')
        target = get_object_or_404(User, pk=target_id)
        try:
            svc.change_role(request.user, target.pk, request.POST.get('role'))
            messages.success(request, 'Role updated.')
        except ValidationError as exc:
            messages.error(request, error_message(exc))
        return redirect('users')
    return render(request, 'users.html', {'users': User.objects.select_related('profile').order_by('username'), 'roles': Profile.ROLES})


@roles('admin')
def campus_settings(request):
    campus = CampusSettings.current()
    form = SettingsForm(request.POST or None, instance=campus)
    if request.method == 'POST' and form.is_valid():
        campus = form.save()
        svc.audit(request.user, 'Campus settings updated', campus, {k: str(v) for k, v in form.cleaned_data.items()})
        messages.success(request, 'Settings saved. Existing forecast snapshots are unchanged.')
        return redirect('settings')
    return render(request, 'form.html', {'form': form, 'title': 'Campus settings', 'intro': 'Starting rates are demonstration assumptions, not validated probabilities.', 'button': 'Save settings'})


@roles('admin')
def audit_history(request):
    return render(request, 'audit.html', {'events': AuditEvent.objects.select_related('actor')[:300]})
