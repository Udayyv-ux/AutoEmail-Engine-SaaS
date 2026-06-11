from django.contrib import admin
from .models import SiteSetting, PricingPlan, Policy

admin.site.register(SiteSetting)
admin.site.register(PricingPlan)
admin.site.register(Policy)