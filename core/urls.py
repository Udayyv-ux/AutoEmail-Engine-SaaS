from django.urls import path
from . import views

urlpatterns = [
    # ==========================================
    # 1. CORE ROUTER
    # ==========================================
    path('dashboard-router/', views.dashboard_router, name='dashboard_router'),
    
    # ==========================================
    # 2. CLIENT WORKSPACE
    # ==========================================
    path('portal/', views.client_portal, name='client_portal'),
    path('api/live-stats/', views.live_client_stats, name='live_client_stats'),
    
    # ==========================================
    # 3. ENTERPRISE OPS ADMIN
    # ==========================================
    path('ops-login/', views.custom_admin_login, name='custom_admin_login'),
    path('ops-dashboard/', views.super_admin_dashboard, name='super_admin_dashboard'),
    path('ops-killswitch/', views.toggle_global_killswitch, name='toggle_global_killswitch'),
    
    # Notice we are using UUIDs here to prevent IDOR attacks!
    path('ops-client/<uuid:public_id>/sync/', views.force_sync_engine, name='force_sync_engine'),
    path('ops-client/<uuid:public_id>/insights/', views.client_insights, name='client_insights'),
    
    # ==========================================
    # 4. PUBLIC PAGES
    # ==========================================
    path('policy/<slug:slug>/', views.policy_page, name='policy_page'),
]