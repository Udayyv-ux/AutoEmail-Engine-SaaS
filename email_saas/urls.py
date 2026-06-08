from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    # The default Django admin panel
    path('admin/', admin.site.urls),
    
    # Routes for Google Authentication (django-allauth)
    path('accounts/', include('allauth.urls')),
    
    # Routes for your Workspace app
    path('', include('core.urls')),
]