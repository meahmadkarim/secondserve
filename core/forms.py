from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import MealService, CampusSettings

class RegistrationForm(UserCreationForm):
    first_name = forms.CharField(label='Display name', max_length=100)
    class Meta:
        model = User
        fields = ['username', 'first_name', 'password1', 'password2']

class MealForm(forms.ModelForm):
    change_reason = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}), help_text='Required for menu changes after students have responded.')
    class Meta:
        model = MealService
        fields = ['date', 'meal_type', 'title', 'description', 'allergens', 'dietary_tags', 'capacity', 'cutoff', 'service_start', 'service_end', 'location', 'event_notes']
        widgets = {**{k: forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}) for k in ['cutoff', 'service_start', 'service_end']}, 'date': forms.DateInput(attrs={'type': 'date'}), **{k: forms.Textarea(attrs={'rows': 3}) for k in ['description', 'allergens', 'event_notes']}}
        labels = {'capacity': 'Planned capacity (portions)', 'allergens': 'Kitchen-provided ingredients / allergen notes', 'cutoff': 'Intention cutoff', 'service_start': 'Regular service begins', 'service_end': 'Regular service ends'}

class ListingForm(forms.Form):
    offered = forms.IntegerField(min_value=0, label='Total portions offered (not additional portions)')
    pickup_start = forms.DateTimeField(widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}))
    deadline = forms.DateTimeField(widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}))
    location = forms.CharField(max_length=140)
    status = forms.ChoiceField(choices=[('draft', 'Draft'), ('open', 'Open'), ('paused', 'Paused')])
    handling_confirmed = forms.BooleanField(label='I confirm these are suitable unserved portions under campus handling procedures. No food returned from plates.', required=True)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}))

class TotalsForm(forms.Form):
    prepared = forms.IntegerField(min_value=0, label='Cumulative total prepared')
    served = forms.IntegerField(min_value=0, label='Cumulative total served normally')
    sold_out = forms.BooleanField(required=False, label='Sold out / unmet demand observed')
    adjustment = forms.BooleanField(required=False, label='Explicit correction of frozen totals')
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}), label='Reason for correction')

class CloseForm(forms.Form):
    discarded = forms.IntegerField(min_value=0, label='Total portions discarded')
    retained = forms.IntegerField(min_value=0, label='Total portions retained / transferred')
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}), label='Outcome / destination notes')

class SettingsForm(forms.ModelForm):
    class Meta:
        model = CampusSettings
        fields = ['name', 'timezone', 'attendance_rate', 'unsure_rate', 'no_response_rate', 'buffer']
        help_texts = {'buffer': 'Fraction between 0 and 1. 0.05 means 5%. Rates are demonstration assumptions.'}

class DateFilterForm(forms.Form):
    start = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    end = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    def clean(self):
        data = super().clean()
        if data.get('start') and data.get('end') and data['start'] > data['end']:
            raise forms.ValidationError('Start must not follow end.')
        return data
