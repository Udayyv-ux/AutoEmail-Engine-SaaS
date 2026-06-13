import json
from datetime import timedelta

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.contrib.auth import authenticate, login as django_login
from django.http import JsonResponse
from django.utils import timezone
from django.db.models.functions import TruncDate
from django.db.models import Count

from .models import ClientProfile, EmailTemplate, SystemSettings, EmailLog, SiteSetting, PricingPlan, Policy
from .services import EngineService 

# ==========================================
# 0. FRONTEND WEBSITE VIEWS
# ==========================================

def landing_page(request):
    site_data = SiteSetting.objects.first()
    plans = PricingPlan.objects.filter(is_active=True)
    return render(request, 'account/login.html', {
        'site_settings': site_data,
        'plans': plans
    })

def policy_page(request, slug):
    policy = get_object_or_404(Policy, slug=slug)
    site_settings = SiteSetting.objects.first()
    return render(request, 'account/policy.html', {
        'policy': policy,
        'settings': site_settings
    })

# ==========================================
# 1. CORE ROUTER
# ==========================================

def dashboard_router(request):
    if not request.user.is_authenticated:
        return redirect('account_login') # This is usually handled by AllAuth, keep as is

    client, created = ClientProfile.objects.get_or_create(user=request.user)
    if created or not client.business_name:
        client.business_name = f"{request.user.username}'s Workspace"
        client.save()

    if request.user.is_superuser:
        return redirect('core:super_admin_dashboard')

    return redirect('core:client_portal')

def check_admin(user):
    return user.is_superuser

# ==========================================
# 2. SUPER ADMIN VIEWS
# ==========================================

@user_passes_test(check_admin, login_url='core:custom_admin_login')
def super_admin_dashboard(request):
    site_settings, _ = SiteSetting.objects.get_or_create(id=1)
    
    if request.method == "POST":
        form_type = request.POST.get("form_type")

        if form_type == "update_hero":
            site_settings.site_name = request.POST.get("site_name", site_settings.site_name)
            site_settings.hero_title = request.POST.get("hero_title", site_settings.hero_title)
            site_settings.hero_subtitle = request.POST.get("hero_subtitle", site_settings.hero_subtitle)
            site_settings.save()
            messages.success(request, "Website text updated globally.")

        elif form_type == "add_plan":
            PricingPlan.objects.create(
                name=request.POST.get("plan_name"),
                price=request.POST.get("plan_price"),
                features=request.POST.get("plan_features")
            )
            messages.success(request, "New pricing tier injected.")

        elif form_type == "delete_plan":
            PricingPlan.objects.filter(public_id=request.POST.get("plan_id")).update(is_active=False)
            messages.success(request, "Pricing tier archived.")

        elif form_type == "save_policy":
            Policy.objects.update_or_create(
                slug=request.POST.get("slug"),
                defaults={
                    'title': request.POST.get("title"),
                    'content': request.POST.get("content")
                }
            )
            messages.success(request, "Legal policy updated successfully.")

        return redirect('core:super_admin_dashboard')

    clients = ClientProfile.objects.select_related('user').all()
    return render(request, 'core/super_admin.html', {
        'clients': clients,
        'total_count': clients.count(),
        'active_count': clients.filter(is_engine_active=True).count(),
        'system_settings': SystemSettings.load(),
        'recent_errors': EmailLog.objects.filter(status=EmailLog.DeliveryStatus.FAILED).order_by('-created_at')[:10],
        'site_settings': site_settings,
        'plans': PricingPlan.objects.filter(is_active=True),
        'policies': Policy.objects.all(),
    })


@user_passes_test(check_admin, login_url='core:custom_admin_login')
def toggle_global_killswitch(request):
    settings_obj = SystemSettings.load()
    settings_obj.is_engine_paused = not settings_obj.is_engine_paused
    settings_obj.save()
    status = "PAUSED" if settings_obj.is_engine_paused else "RESUMED"
    messages.success(request, f"GLOBAL OVERRIDE: All engines are now {status}.")
    return redirect('core:super_admin_dashboard')


@user_passes_test(check_admin, login_url='core:custom_admin_login')
def force_sync_engine(request, public_id):
    client = get_object_or_404(ClientProfile, public_id=public_id)
    result = EngineService.process_client(client)
    messages.info(request, f"Engine Response: {result}")
    return redirect('core:super_admin_dashboard')


@user_passes_test(check_admin, login_url='core:custom_admin_login')
def client_insights(request, public_id):
    client = get_object_or_404(ClientProfile, public_id=public_id)

    success_count = EmailLog.objects.filter(client=client, status=EmailLog.DeliveryStatus.SUCCESS).count()
    failed_count = EmailLog.objects.filter(client=client, status=EmailLog.DeliveryStatus.FAILED).count()
    total_attempts = success_count + failed_count

    deliverability = "0%"
    if total_attempts > 0:
        deliverability = f"{int((success_count / total_attempts) * 100)}%"

    return render(request, 'core/master_console/client_detail.html', {
        'client': client,
        'insights': {
            'engine_status': 'Active' if client.is_engine_active else 'Paused',
            'total_emails_sent': client.emails_sent_count,
            'deliverability_rate': deliverability,
            'success_count': success_count,
            'failed_count': failed_count,
        },
        'templates': client.templates.filter(is_active=True),
        'recent_logs': EmailLog.objects.filter(client=client).order_by('-created_at')[:10]
    })


# ==========================================
# 3. CLIENT PORTAL VIEWS
# ==========================================

@login_required
def client_portal(request):
    if request.user.is_superuser:
        return redirect('core:super_admin_dashboard')

    client, _ = ClientProfile.objects.get_or_create(user=request.user)

    if request.method == "POST":
        if "toggle_engine" in request.POST:
            client.is_engine_active = not client.is_engine_active
            client.save(update_fields=['is_engine_active'])
            messages.success(request, "Engine Status Updated!")

        elif "save_config" in request.POST:
            client.google_sheet_link = request.POST.get("google_sheet_link", "")
            client.sender_email = request.POST.get("sender_email", "")
            client.gmail_app_password = request.POST.get("gmail_app_password", "")
            client.col_name = request.POST.get("col_name", "Name")
            client.col_email = request.POST.get("col_email", "Email")
            client.col_query = request.POST.get("col_query", "Inquiry")
            client.col_status = request.POST.get("col_status", "Status")
            client.save()
            messages.success(request, "Configuration Saved!")

        elif "add_template" in request.POST:
            EmailTemplate.objects.create(
                client=client,
                project_id=request.POST.get("project_id", ""),
                subject=request.POST.get("subject", ""),
                html_body=request.POST.get("html_body", ""),
                image=request.FILES.get("image")
            )
            messages.success(request, "Template Deployed!")

        elif "delete_template" in request.POST:
            EmailTemplate.objects.filter(
                id=request.POST.get("template_id"), 
                client=client
            ).update(is_active=False)
            messages.success(request, "Template Archived!")

        return redirect('core:client_portal')

    time_range = request.GET.get('range', '7d')
    now = timezone.now()
    base_logs = client.email_logs.filter(status=EmailLog.DeliveryStatus.SUCCESS)

    if time_range == 'today':
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        filtered_logs = base_logs.filter(created_at__gte=start_date)
    elif time_range == '7d':
        filtered_logs = base_logs.filter(created_at__gte=now - timedelta(days=7))
    elif time_range == '30d':
        filtered_logs = base_logs.filter(created_at__gte=now - timedelta(days=30))
    else:
        filtered_logs = base_logs

    daily_stats = (
        filtered_logs
        .annotate(date=TruncDate('created_at'))
        .values('date')
        .annotate(count=Count('id'))
        .order_by('date')
    )

    chart_labels = [stat['date'].strftime("%b %d") for stat in daily_stats]
    chart_data = [stat['count'] for stat in daily_stats]

    return render(request, 'core/client_portal.html', {
        'client': client,
        'templates': client.templates.filter(is_active=True), 
        'recent_logs': client.email_logs.all()[:15],
        'chart_labels': json.dumps(chart_labels),
        'chart_data': json.dumps(chart_data),
        'total_emails_sent': filtered_logs.count(),
    })


# ==========================================
# 4. LIVE STATS ENDPOINT
# ==========================================

@login_required
def live_client_stats(request):
    try:
        client = request.user.profile 
        return JsonResponse({
            'is_active': client.is_engine_active,
            'emails_sent': client.emails_sent_count,
            'last_scanned': (
                client.last_scanned.strftime("%I:%M:%S %p")
                if client.last_scanned
                else "Awaiting first scan..."
            )
        })
    except Exception:
        return JsonResponse({'is_active': False, 'emails_sent': 0, 'last_scanned': 'Offline'})


# ==========================================
# 5. CUSTOM ADMIN AUTHENTICATION
# ==========================================

def custom_admin_login(request):
    if request.method == "POST":
        user = authenticate(
            request,
            username=request.POST.get('username'),
            password=request.POST.get('password')
        )
        if user is not None and user.is_staff:
            django_login(request, user)
            return redirect('core:super_admin_dashboard')
        else:
            messages.error(request, "Invalid System Administrator credentials.")
            return redirect('core:custom_admin_login')

    return render(request, 'account/super_admin.html')