import secrets
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models import Q, F
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone


def pickup_code():
    return secrets.token_hex(8).upper()


class Profile(models.Model):
    ROLES = [('student', 'Student'), ('kitchen', 'Kitchen staff'), ('admin', 'Administrator')]
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=10, choices=ROLES, default='student')
    campus_identifier = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)


class CampusSettings(models.Model):
    name = models.CharField(max_length=120, default='Greenfield Campus')
    timezone = models.CharField(max_length=60, default='Asia/Bishkek')
    attendance_rate = models.DecimalField(max_digits=4, decimal_places=3, default='.90', validators=[MinValueValidator(0), MaxValueValidator(1)])
    unsure_rate = models.DecimalField(max_digits=4, decimal_places=3, default='.50', validators=[MinValueValidator(0), MaxValueValidator(1)])
    no_response_rate = models.DecimalField(max_digits=4, decimal_places=3, default='.30', validators=[MinValueValidator(0), MaxValueValidator(1)])
    buffer = models.DecimalField(max_digits=4, decimal_places=3, default='.05', validators=[MinValueValidator(0), MaxValueValidator(1)])

    @classmethod
    def current(cls):
        return cls.objects.get_or_create(pk=1)[0]

    def clean(self):
        if self.pk not in (None, 1):
            raise ValidationError('Only one campus is supported.')
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:
            raise ValidationError({'timezone': 'Enter a valid IANA timezone.'})


class MealService(models.Model):
    STATES = [(s, s.replace('_', ' ').title()) for s in ['planned', 'preparing', 'regular_service', 'surplus_collection', 'closed', 'cancelled']]
    date = models.DateField()
    meal_type = models.CharField(max_length=10, choices=[('breakfast', 'Breakfast'), ('lunch', 'Lunch'), ('dinner', 'Dinner')])
    title = models.CharField(max_length=140)
    description = models.TextField()
    allergens = models.TextField(blank=True)
    dietary_tags = models.CharField(max_length=180, blank=True)
    capacity = models.PositiveIntegerField(default=100)
    cutoff = models.DateTimeField()
    service_start = models.DateTimeField()
    service_end = models.DateTimeField()
    location = models.CharField(max_length=140, default='Campus kitchen · Collection counter')
    event_notes = models.TextField(blank=True)
    state = models.CharField(max_length=25, choices=STATES, default='planned')
    prepared = models.PositiveIntegerField(default=0)
    served = models.PositiveIntegerField(default=0)
    preparation_recorded = models.BooleanField(default=False)
    serving_recorded = models.BooleanField(default=False)
    discarded = models.PositiveIntegerField(default=0)
    retained = models.PositiveIntegerField(default=0)
    sold_out = models.BooleanField(default=False)
    menu_changed = models.BooleanField(default=False)
    synthetic = models.BooleanField(default=False)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_meals')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', 'meal_type']
        constraints = [models.UniqueConstraint(fields=['date', 'meal_type'], name='one_meal_per_slot'), models.CheckConstraint(condition=Q(cutoff__lt=F('service_start')), name='cutoff_before_start'), models.CheckConstraint(condition=Q(service_end__gt=F('service_start')), name='end_after_start'), models.CheckConstraint(condition=Q(served__lte=F('prepared')), name='served_within_prepared'), models.CheckConstraint(condition=Q(prepared__lte=F('capacity')), name='prepared_within_capacity')]

    def clean(self):
        if self.cutoff and self.service_start and self.cutoff >= self.service_start:
            raise ValidationError('Intention cutoff must be before regular service.')
        if self.service_end and self.service_start and self.service_end <= self.service_start:
            raise ValidationError('Service end must follow its start.')
        if self.served > self.prepared or self.prepared > self.capacity:
            raise ValidationError('Servings ≤ prepared ≤ capacity is required.')

    @property
    def allergen_information(self):
        return self.allergens or 'Information not provided—ask kitchen staff'

    @property
    def collected(self):
        return Reservation.objects.filter(listing__meal=self, status='collected').count()

    @property
    def awaiting(self):
        return self.prepared - self.served - self.collected - self.discarded - self.retained

    def __str__(self):
        return f'{self.title} · {self.date} {self.meal_type}'


class MealIntention(models.Model):
    RESPONSES = [('attending', 'I plan to attend'), ('not_attending', 'I will not attend'), ('unsure', 'Not sure')]
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    meal = models.ForeignKey(MealService, on_delete=models.PROTECT, related_name='intentions')
    response = models.CharField(max_length=15, choices=RESPONSES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['student', 'meal'], name='one_intention')]


class ForecastSnapshot(models.Model):
    meal = models.OneToOneField(MealService, on_delete=models.PROTECT, related_name='snapshot')
    data = models.JSONField()
    override = models.PositiveIntegerField(null=True, blank=True)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class SurplusListing(models.Model):
    STATES = [(s, s.title()) for s in ['draft', 'open', 'paused', 'closed', 'withdrawn']]
    meal = models.OneToOneField(MealService, on_delete=models.PROTECT, related_name='listing')
    offered = models.PositiveIntegerField()
    # Denormalised committed inventory, updated atomically with reservation changes.
    committed = models.PositiveIntegerField(default=0)
    pickup_start = models.DateTimeField()
    deadline = models.DateTimeField(db_index=True)
    location = models.CharField(max_length=140)
    status = models.CharField(max_length=12, choices=STATES, default='draft')
    handling_confirmed = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    withdrawal_reason = models.TextField(blank=True)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(committed__lte=F('offered')), name='inventory_within_offer'), models.CheckConstraint(condition=Q(deadline__gt=F('pickup_start')), name='valid_pickup_window')]

    def clean(self):
        if self.deadline and self.pickup_start and self.deadline <= self.pickup_start:
            raise ValidationError('Pickup deadline must follow its start.')
        if self.meal_id and self.offered > self.meal.prepared - self.meal.served:
            raise ValidationError('Offer exceeds unserved portions.')
        if self.offered < self.committed:
            raise ValidationError('Offer cannot be below reserved plus collected portions.')
        if self.status == 'open' and not self.handling_confirmed:
            raise ValidationError('Authorised staff must confirm handling suitability.')

    @property
    def available(self):
        if self.status != 'open' or timezone.now() >= self.deadline or self.meal.state != 'surplus_collection':
            return 0
        return self.offered - self.committed

    @property
    def display_status(self):
        return 'Expired' if self.deadline <= timezone.now() and self.status in ['open', 'paused'] else self.get_status_display()


class Reservation(models.Model):
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    listing = models.ForeignKey(SurplusListing, on_delete=models.PROTECT, related_name='reservations')
    quantity = models.PositiveSmallIntegerField(default=1, editable=False)
    code = models.CharField(max_length=32, unique=True, default=pickup_code, editable=False)
    status = models.CharField(max_length=12, choices=[(s, s.title()) for s in ['reserved', 'collected', 'cancelled', 'expired', 'withdrawn']], default='reserved', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    collected_at = models.DateTimeField(null=True, blank=True)
    collecting_staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='collections')
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(quantity=1), name='one_portion'), models.UniqueConstraint(fields=['student', 'listing'], condition=Q(status__in=['reserved', 'collected']), name='one_live_portion')]

    @property
    def effective_status(self):
        if self.status == 'reserved':
            if self.listing.status == 'withdrawn':
                return 'withdrawn'
            if self.listing.deadline <= timezone.now():
                return 'expired'
        return self.status


class AuditEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    action = models.CharField(max_length=100)
    object_type = models.CharField(max_length=50)
    object_id = models.PositiveIntegerField()
    changes = models.JSONField(default=dict)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']


class PickupAttempt(models.Model):
    staff = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    failures = models.PositiveIntegerField(default=0)
    window_start = models.DateTimeField(default=timezone.now)
