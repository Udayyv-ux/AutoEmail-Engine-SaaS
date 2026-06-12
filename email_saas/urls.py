from django.contrib import admin
from django.urls import path, include
from core import views # We need to import the views here to intercept the URL
from django.conf import settings
from django.conf.urls.static import static

# ... your existing urlpatterns ...

# Add this at the very bottom
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

urlpatterns = [
    # 1. The default Django admin panel
    path('admin/', admin.site.urls),
    
    # 2. HIJACK ALLAUTH: 
    # By placing this BEFORE 'accounts/', Django hits this path first.
    # It forces /accounts/login/ to use your custom 'landing_page' view.
    path('accounts/login/', views.landing_page, name='account_login'),
    
    # 3. Routes for Google Authentication
    path('accounts/', include('allauth.urls')),
    
    # 4. Routes for your Workspace app
    path('', include('core.urls')),
]