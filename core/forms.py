from django import forms
from .models import ClientProfile

TW_INPUT = "mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm px-4 py-2 border outline-none"

class ClientOnboardingForm(forms.ModelForm):
    class Meta:
        model = ClientProfile
        fields = ['business_name', 'sender_email', 'is_engine_active']
        widgets = {
            'business_name': forms.TextInput(attrs={'class': TW_INPUT}),
            'sender_email': forms.EmailInput(attrs={'class': TW_INPUT}),
            'is_engine_active': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-indigo-600 border-gray-300 rounded mt-2'}),
        }