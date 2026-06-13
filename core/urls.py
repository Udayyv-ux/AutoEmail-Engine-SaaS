from django.urls import path
from . import views

# ENTERPRISE BEST PRACTICE: App namespacing
# This prevents URL name collisions if you add more apps to the project later.
app_name = 'core'

urlpatterns = [
    # =====================================================================
    # --- 1. PUBLIC MARKETING WEBSITE ---
    # =====================================================================
    path('', views.landing_page, name='landing_page'),
    path('policy/<slug:slug>/', views.policy_page, name='policy_page'),

    # =====================================================================
    # --- 2. CLIENT WORKSPACE (TENANT APP) ---
    # =====================================================================
    path('dashboard-router/', views.dashboard_router, name='dashboard_router'),
    path('portal/', views.client_portal, name='client_portal'),

    # =====================================================================
    # --- 3. INTERNAL APIs (Versioned) ---
    # =====================================================================
    # APIs must be versioned (v1) so you can update them later without breaking mobile apps or webhooks
    path('api/v1/client/live-stats/', views.live_client_stats, name='live_client_stats'),

    # =====================================================================
    # --- 4. SYSTEM OPERATIONS (Master Console) ---
    # =====================================================================
    # Obscured the URL path slightly from 'master-console' and 'admin-auth' to deter basic bot scraping
    path('system-ops/auth/', views.custom_admin_login, name='custom_admin_login'),
    path('system-ops/dashboard/', views.super_admin_dashboard, name='super_admin_dashboard'),
    path('system-ops/killswitch/toggle/', views.toggle_global_killswitch, name='toggle_killswitch'),

    # SECURITY UPGRADE: Replaced <int:client_id> with <uuid:public_id> to eliminate IDOR vulnerabilities.
    path('system-ops/client/<uuid:public_id>/insights/', views.client_insights, name='client_insights'),
    path('system-ops/client/<uuid:public_id>/force-sync/', views.force_sync_engine, name='force_sync_engine'),
]