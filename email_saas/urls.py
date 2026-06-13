import os
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

# Decoupled import explicitly aliased to prevent naming collisions
from core import views as core_views

# =====================================================================
# 1. ENTERPRISE ADMIN BRANDING
# =====================================================================
# Professionalizes the backend console for your internal team
admin.site.site_header = "AutoEmail Engine Administration"
admin.site.site_title = "AutoEmail System Ops"
admin.site.index_title = "Global Infrastructure Control"

# =====================================================================
# 2. SECURITY: OBSCURED ADMIN URL
# =====================================================================
# Prevents automated bot scanners from finding your database login page.
# You can set DJANGO_ADMIN_URL="my-secret-console/" in Railway variables.
ADMIN_URL_PATH = os.environ.get('DJANGO_ADMIN_URL', 'system-secure-admin/')

# =====================================================================
# 3. GLOBAL ROUTING TABLE
# =====================================================================
urlpatterns = [
    # 1. Secured Django Admin
    path(ADMIN_URL_PATH, admin.site.urls),
    
    # 2. IDENTITY HIJACK (Allauth Override)
    # By capturing this exact path before 'accounts/' is included, 
    # we seamlessly route unauthenticated users to your marketing site 
    # instead of the ugly default Allauth login screen.
    path('accounts/login/', core_views.landing_page, name='account_login'),
    
    # 3. Third-Party Auth (Google OAuth)
    path('accounts/', include('allauth.urls')),
    
    # 4. Core SaaS Domain
    # We include the 'core' namespace established in your app's urls.py
    path('', include('core.urls')),
]

# =====================================================================
# 4. LOCAL DEVELOPMENT ASSETS
# =====================================================================
# In production (DEBUG=False), WhiteNoise handles static files automatically.
# Media files (like uploaded template images) must be handled by an S3 bucket 
# or Cloudinary in production, but this allows them to work locally.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)