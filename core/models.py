from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.utils.timezone import now
import uuid

# --- 1. THE RESTORED CUSTOM USER MODEL ---
class CustomUser(AbstractUser):
    # This satisfies settings.AUTH_USER_MODEL without breaking your existing Neon DB
    pass

# --- 2. CLIENT PROFILE ---
class ClientProfile(models.Model):
    # Notice this now points to settings.AUTH_USER_MODEL instead of the default User
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    business_name = models.CharField(max_length=255, blank=True, null=True)
    
    # Engine Configurations
    sender_email = models.EmailField(blank=True, null=True)
    gmail_app_password = models.CharField(max_length=255, blank=True, null=True)
    google_sheet_link = models.URLField(blank=True, null=True)
    
    col_name = models.CharField(max_length=50, default="Name")
    col_email = models.CharField(max_length=50, default="Email")
    col_query = models.CharField(max_length=50, default="Inquiry")
    col_status = models.CharField(max_length=50, default="Status")
    
    # Live Status
    is_engine_active = models.BooleanField(default=False)
    emails_sent_count = models.IntegerField(default=0)
    last_scanned = models.DateTimeField(blank=True, null=True)
    
    # Daily Tracking & Invites (SaaS Features)
    emails_sent_today = models.IntegerField(default=0)
    last_sent_date = models.DateField(auto_now_add=True)
    invite_token = models.UUIDField(default=uuid.uuid4, editable=False, null=True)
    is_onboarded = models.BooleanField(default=False)

    def __str__(self):
        return self.business_name or f"Client Workspace {self.id}"

    def reset_daily_count_if_needed(self):
        if self.last_sent_date != now().date():
            self.emails_sent_today = 0
            self.last_sent_date = now().date()
            self.save()

class EmailTemplate(models.Model):
    client = models.ForeignKey(ClientProfile, on_delete=models.CASCADE, related_name="templates")
    project_id = models.CharField(max_length=100, blank=True, null=True)
    subject = models.CharField(max_length=255)
    html_body = models.TextField()

    def __str__(self):
        return self.subject

# --- 3. NEW SAAS ENTERPRISE FEATURES ---

class SystemSettings(models.Model):
    is_engine_paused = models.BooleanField(default=False, help_text="Check this to instantly stop all background emails globally.")
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.pk = 1 # Forces Singleton pattern (only 1 row ever exists)
        super(SystemSettings, self).save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, created = cls.objects.get_or_create(pk=1)
        return obj

class EmailLog(models.Model):
    STATUS_CHOICES = (
        ('SUCCESS', 'Success'),
        ('FAILED', 'Failed'),
    )
    client = models.ForeignKey(ClientProfile, on_delete=models.CASCADE, related_name='email_logs')
    recipient_email = models.EmailField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    error_message = models.TextField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']