from django.urls import path

from . import views



urlpatterns = [

    # --- 1. PUBLIC MARKETING WEBSITE ---

    # The main space-themed landing page (yourwebsite.com/)

    path('', views.landing_page, name='landing_page'),

   

    # Dynamic policy pages (Privacy Policy, Terms, etc.) editable from admin

    path('policy/<slug:slug>/', views.policy_page, name='policy_page'),

   

   

    # --- 2. YOUR EXISTING WORKSPACE & ENGINE URLS ---

    # Core app routing for logged-in clients

    path('dashboard-router/', views.dashboard_router, name='dashboard_router'),

   

    path('portal/', views.client_portal, name='client_portal'),

    path('api/live-stats/', views.live_client_stats, name='live_client_stats'),

   

    # Super Admin / Management Console

    path('master-console/', views.super_admin_dashboard, name='super_admin_dashboard'),

    path('master-console/force-sync/<int:client_id>/', views.force_sync_engine, name='force_sync_engine'),

    path('master-console/toggle-killswitch/', views.toggle_global_killswitch, name='toggle_killswitch'),

   

    # Drill-down view for individual client insights

    path('master-console/client/<int:client_id>/', views.client_insights, name='client_insights'),

   

    path('admin-auth/', views.custom_admin_login, name='custom_admin_login'),

] 

