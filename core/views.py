from email.utils import formataddr
from email.mime.image import MIMEImage
import os
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.contrib.auth import get_user_model, authenticate, login as django_login
from django.http import JsonResponse
from django.utils import timezone
from django.db.models.functions import TruncDate
from django.db.models import Count
from datetime import timedelta
import json
import requests
import csv
from io import StringIO
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from .models import ClientProfile, EmailTemplate, SystemSettings, EmailLog, SiteSetting, PricingPlan, Policy

# ==========================================
# 0. FRONTEND WEBSITE VIEWS
# ==========================================
def landing_page(request):
    site_data = SiteSetting.objects.first() 
    plans = PricingPlan.objects.all()
    
    return render(request, 'account/login.html', {
        'site_settings': site_data, 
        'plans': plans
    })

def policy_page(request, slug):
    policy = get_object_or_404(Policy, slug=slug)
    settings = SiteSetting.objects.first()
    return render(request, 'account/policy.html', {
        'policy': policy,
        'settings': settings
    })

# ==========================================
# 1. CORE ROUTER
# ==========================================
def dashboard_router(request):
    if not request.user.is_authenticated:
        return redirect('/accounts/login/')
    
    client, created = ClientProfile.objects.get_or_create(user=request.user)
    if created or not client.business_name:
        client.business_name = f"{request.user.username}'s Workspace"
        client.save()

    if request.user.is_superuser:
        return redirect('super_admin_dashboard')
        
    return redirect('client_portal')

def check_admin(user):
    return user.is_superuser

# ==========================================
# 2. INTEGRATED AUTOMATION ENGINE
# ==========================================
# Notice: 'request' has been added as a parameter to generate the live URL
def run_client_engine(client_id, request):
    if SystemSettings.load().is_engine_paused:
        return "GLOBAL KILL SWITCH IS ACTIVE. Engine halted."

    try:
        client = ClientProfile.objects.get(id=client_id)
    except ClientProfile.DoesNotExist:
        return "Client node missing."
    
    if not client.is_engine_active:
        return "Workspace Engine is OFF."

    sheet_url = client.google_sheet_link
    if not sheet_url or "/d/" not in sheet_url:
        return "Invalid Google Sheet Link."
        
    sheet_id = sheet_url.split('/d/')[1].split('/')[0]
    csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"

    try:
        response = requests.get(csv_url)
        if response.status_code != 200:
            return "Cannot read sheet. Ensure share setting is 'Anyone with the link'."
        response.encoding = 'utf-8'
        csv_data = list(csv.DictReader(StringIO(response.text)))
    except Exception as e:
        return f"Sheet error: {str(e)}"

    template = client.templates.first()
    if not template:
        return "No email templates exist."

    try:
        smtp_server = smtplib.SMTP('smtp.gmail.com', 587)
        smtp_server.starttls()
        smtp_server.login(client.sender_email, client.gmail_app_password)
    except Exception as e:
        return f"SMTP Auth Failed: {str(e)[:100]}"

    sent_count = 0
    for row in csv_data:
        recipient = row.get(client.col_email, '').strip()
        name = row.get(client.col_name, 'There').strip()
        
        if not recipient or '@' not in recipient:
            continue

        if EmailLog.objects.filter(client=client, recipient_email=recipient, status='SUCCESS').exists():
            continue

        try:
            # 1. Base Multipart related (tells Gmail an image is attached)
            msg = MIMEMultipart('related') 
            
            # Formats it beautifully as: "Your Business Name" <youremail@gmail.com>
            msg['From'] = formataddr((client.business_name, client.sender_email))
            msg['To'] = recipient
            msg['Subject'] = template.subject
            
            # 2. Alternative payload (Mandatory for Gmail to not strip the image)
            msg_alternative = MIMEMultipart('alternative')
            msg.attach(msg_alternative)
            
            body_content = template.html_body if template.html_body else f"Hello {name},\n\nWe received your inquiry.\n\nBest,\n{client.business_name}"
            body_content = body_content.replace('{{Name}}', name)
            
            # 3. DYNAMIC INLINE IMAGE INJECTION
            if template.image and os.path.exists(template.image.path):
                image_html = '<div style="text-align: center; margin-bottom: 20px;"><img src="cid:banner_img" style="max-width: 100%; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.1);"></div>'
                
                if template.html_body:
                    body_content = image_html + body_content
                else:
                    body_content = image_html + body_content.replace('\n', '<br>')
                    template.html_body = "True"
                    
                # Attach the HTML body to the ALTERNATIVE block
                msg_alternative.attach(MIMEText(body_content, 'html' if template.html_body else 'plain'))
                
                # Attach the physical image to the MAIN block
                try:
                    with open(template.image.path, 'rb') as img_file:
                        img_mime = MIMEImage(img_file.read())
                        img_mime.add_header('Content-ID', '<banner_img>')
                        img_mime.add_header('Content-Disposition', 'inline')
                        msg.attach(img_mime)
                except Exception as img_e:
                    print(f"Image read error: {img_e}")
            else:
                # If no image, just attach the text
                msg_alternative.attach(MIMEText(body_content, 'html' if template.html_body else 'plain'))
            
            smtp_server.send_message(msg)
            
            EmailLog.objects.create(client=client, recipient_email=recipient, status='SUCCESS')
            sent_count += 1
        except Exception as e:
            EmailLog.objects.create(client=client, recipient_email=recipient, status='FAILED', error_message=str(e)[:250])
    smtp_server.quit()
    client.emails_sent_count += sent_count
    client.last_scanned = timezone.now()
    client.save()

    return f"Scan Complete. {sent_count} new emails delivered."

# ==========================================
# 3. SUPER ADMIN VIEWS
# ==========================================
@user_passes_test(check_admin)
def super_admin_dashboard(request):
    site_settings, created = SiteSetting.objects.get_or_create(id=1)
    plans = PricingPlan.objects.all()
    policies = Policy.objects.all()

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
            plan_id = request.POST.get("plan_id")
            PricingPlan.objects.filter(id=plan_id).delete()
            messages.success(request, "Pricing tier terminated.")
            
        elif form_type == "save_policy":
            Policy.objects.update_or_create(
                slug=request.POST.get("slug"),
                defaults={
                    'title': request.POST.get("title"),
                    'content': request.POST.get("content")
                }
            )
            messages.success(request, "Legal policy updated successfully.")

        return redirect('super_admin_dashboard')

    clients = ClientProfile.objects.all()
    total_count = clients.count()
    active_count = clients.filter(is_engine_active=True).count()
    
    return render(request, 'core/super_admin.html', {
        'clients': clients,
        'total_count': total_count,
        'active_count': active_count,
        'system_settings': SystemSettings.load(),
        'recent_errors': EmailLog.objects.filter(status='FAILED').order_by('-timestamp')[:10],
        'site_settings': site_settings,
        'plans': plans,
        'policies': policies,
    })

@user_passes_test(check_admin)
def toggle_global_killswitch(request):
    settings_obj = SystemSettings.load()
    settings_obj.is_engine_paused = not settings_obj.is_engine_paused
    settings_obj.save()
    status = "PAUSED" if settings_obj.is_engine_paused else "RESUMED"
    messages.success(request, f"GLOBAL OVERRIDE: All engines are now {status}.")
    return redirect('super_admin_dashboard')

@user_passes_test(check_admin)
def force_sync_engine(request, client_id):
    # Added 'request' here so it can pass the context to the engine
    result = run_client_engine(client_id, request)
    messages.info(request, f"Engine Response: {result}")
    return redirect('super_admin_dashboard')

@user_passes_test(check_admin)
def client_insights(request, client_id):
    client = get_object_or_404(ClientProfile, id=client_id)
    
    success_count = EmailLog.objects.filter(client=client, status='SUCCESS').count()
    failed_count = EmailLog.objects.filter(client=client, status='FAILED').count()
    total_attempts = success_count + failed_count
    
    deliverability = "0%"
    if total_attempts > 0:
        deliverability = f"{int((success_count / total_attempts) * 100)}%"

    insights = {
        'engine_status': 'Active' if client.is_engine_active else 'Paused',
        'total_emails_sent': client.emails_sent_count,
        'deliverability_rate': deliverability,
        'success_count': success_count,
        'failed_count': failed_count,
    }
    
    context = {
        'client': client,
        'insights': insights,
        'templates': client.templates.all(),
        'recent_logs': EmailLog.objects.filter(client=client).order_by('-timestamp')[:10]
    }
    
    return render(request, 'core/master_console/client_detail.html', context)

# ==========================================
# 4. CLIENT PORTAL VIEWS
# ==========================================
@login_required
def client_portal(request):
    if request.user.is_superuser:
        return redirect('super_admin_dashboard')
        
    client, _ = ClientProfile.objects.get_or_create(user=request.user)

    if request.method == "POST":
        if "toggle_engine" in request.POST:
            client.is_engine_active = not client.is_engine_active
            client.save()
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
                image=request.FILES.get("image") # <-- Saves the physical image to the database!
            )
            messages.success(request, "Template Deployed!")
            
        elif "delete_template" in request.POST:
            EmailTemplate.objects.filter(id=request.POST.get("template_id"), client=client).delete()
            messages.success(request, "Template Purged!")
            
        return redirect('client_portal')

    time_range = request.GET.get('range', '7d')
    now = timezone.now()
    base_logs = client.email_logs.filter(status='SUCCESS')
    
    if time_range == 'today':
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        filtered_logs = base_logs.filter(timestamp__gte=start_date)
    elif time_range == '7d':
        start_date = now - timedelta(days=7)
        filtered_logs = base_logs.filter(timestamp__gte=start_date)
    elif time_range == '30d':
        start_date = now - timedelta(days=30)
        filtered_logs = base_logs.filter(timestamp__gte=start_date)
    else:
        filtered_logs = base_logs

    daily_stats = filtered_logs.annotate(
        date=TruncDate('timestamp')
    ).values('date').annotate(
        count=Count('id')
    ).order_by('date')
    
    chart_labels = [stat['date'].strftime("%b %d") for stat in daily_stats]
    chart_data = [stat['count'] for stat in daily_stats]
    total_emails_sent = filtered_logs.count()
    recent_logs = client.email_logs.all()[:15]

    return render(request, 'core/client_portal.html', {
        'client': client, 
        'templates': client.templates.all(),
        'recent_logs': recent_logs,
        'chart_labels': json.dumps(chart_labels),
        'chart_data': json.dumps(chart_data),
        'total_emails_sent': total_emails_sent
    })

# ==========================================
# MISSING LIVE STATS FUNCTION
# ==========================================
@login_required
def live_client_stats(request):
    try:
        client = request.user.clientprofile
        return JsonResponse({
            'is_active': client.is_engine_active,
            'emails_sent': client.emails_sent_count,
            'last_scanned': client.last_scanned.strftime("%I:%M:%S %p") if client.last_scanned else "Awaiting first scan..."
        })
    except:
        return JsonResponse({'is_active': False, 'emails_sent': 0, 'last_scanned': 'Offline'})

# ==========================================
# 5. CUSTOM ADMIN AUTHENTICATION
# ==========================================
def custom_admin_login(request):
    if request.method == "POST":
        u = request.POST.get('username')
        p = request.POST.get('password')
        
        user = authenticate(request, username=u, password=p)
        
        if user is not None and user.is_staff:
            django_login(request, user)
            return redirect('super_admin_dashboard') 
        else:
            messages.error(request, "Invalid System Administrator credentials.")
            return redirect('custom_admin_login')
            
    return render(request, 'account/admin_login.html')