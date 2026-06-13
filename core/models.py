import uuid
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _

# =====================================================================
# --- BASE ARCHITECTURE ---
# =====================================================================

class EnterpriseBaseModel(models.Model):
    """
    Abstract base class providing standard audit fields and secure UUIDs.
    All new models should inherit from this.
    """
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

# =====================================================================
# --- 1. CORE AUTHENTICATION ---
# =====================================================================

class CustomUser(AbstractUser):
    # Maintained as-is to prevent breaking existing Neon DB schemas.
    pass


# =====================================================================
# --- 2. MULTI-TENANCY / CLIENT DOMAIN ---
# =====================================================================

class ClientProfile(EnterpriseBaseModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    business_name = models.CharField(max_length=255, blank=True, null=True)
    
    # --- Engine Configurations ---
    sender_email = models.EmailField(blank=True, null=True)
    # ENTERPRISE SECURITY WARNING: 
    # In a true production environment, wrap this with `django-cryptography` 
    # e.g., `gmail_app_password = encrypt(models.CharField(...))`
    gmail_app_password = models.CharField(max_length=255, blank=True, null=True)
    google_sheet_link = models.URLField(max_length=500, blank=True, null=True)
    
    # --- Data Mapping ---
    col_name = models.CharField(max_length=50, default="Name")
    col_email = models.CharField(max_length=50, default="Email")
    col_query = models.CharField(max_length=50, default="Inquiry")
    col_status = models.CharField(max_length=50, default="Status")
    
    # --- Engine State & Metrics ---
    is_engine_active = models.BooleanField(default=False, db_index=True)
    emails_sent_count = models.IntegerField(default=0)
    last_scanned = models.DateTimeField(blank=True, null=True)
    
    # --- SaaS Onboarding & Tracking ---
    emails_sent_today = models.IntegerField(default=0)
    last_sent_date = models.DateField(auto_now_add=True)
    invite_token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    is_onboarded = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("Client Profile")
        verbose_name_plural = _("Client Profiles")
        indexes = [
            models.Index(fields=['is_engine_active', 'last_scanned']),
        ]

    def __str__(self):
        return self.business_name or f"Client Workspace {self.user.username}"

    def reset_daily_count_if_needed(self):
        if self.last_sent_date != now().date():
            self.emails_sent_today = 0
            self.last_sent_date = now().date()
            self.save(update_fields=['emails_sent_today', 'last_sent_date'])


class EmailTemplate(EnterpriseBaseModel):
    client = models.ForeignKey(ClientProfile, on_delete=models.CASCADE, related_name="templates")
    project_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    subject = models.CharField(max_length=255)
    html_body = models.TextField()
    image = models.ImageField(upload_to='campaign_images/', blank=True, null=True)
    
    # Enterprise Soft-Deletion Strategy
    is_active = models.BooleanField(default=True, help_text="Set to False instead of deleting to preserve log integrity.")

    class Meta:
        verbose_name = _("Email Template")
        verbose_name_plural = _("Email Templates")
        unique_together = ('client', 'project_id') # Prevents duplicate project IDs per client

    def __str__(self):
        return f"{self.client.business_name} | {self.subject}"


# =====================================================================
# --- 3. OBSERVABILITY & SYSTEM CONFIG ---
# =====================================================================

class SystemSettings(models.Model):
    """Singleton model for global kill-switches and configurations."""
    is_engine_paused = models.BooleanField(default=False, help_text="Instantly stop all background emails globally.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("System Setting")
        verbose_name_plural = _("System Settings")

    def save(self, *args, **kwargs):
        self.pk = 1 # Forces Singleton pattern
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class EmailLog(EnterpriseBaseModel):
    """
    Immutable audit trail. Records in this table should never be updated or deleted.
    """
    class DeliveryStatus(models.TextChoices):
        QUEUED = 'QUEUED', _('Queued')
        PROCESSING = 'PROCESSING', _('Processing')
        SUCCESS = 'SUCCESS', _('Success')
        FAILED = 'FAILED', _('Failed')
        BOUNCED = 'BOUNCED', _('Bounced')

    client = models.ForeignKey(ClientProfile, on_delete=models.CASCADE, related_name='email_logs')
    template = models.ForeignKey(EmailTemplate, on_delete=models.SET_NULL, null=True, blank=True)
    
    recipient_email = models.EmailField(db_index=True)
    status = models.CharField(max_length=15, choices=DeliveryStatus.choices, default=DeliveryStatus.QUEUED, db_index=True)
    error_message = models.TextField(blank=True, null=True)
    
    # Correlation ID ties this log back to the specific row/request in the Google Sheet
    correlation_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['client', 'status', '-created_at']), # Compound index for the analytics dashboard
        ]

    def __str__(self):
        return f"{self.recipient_email} - {self.status}"


# =====================================================================
# --- 4. CMS & MARKETING (FRONTEND) ---
# =====================================================================

class SiteSetting(EnterpriseBaseModel):
    site_name = models.CharField(max_length=100, default="AutoEmail SaaS")
    hero_title = models.CharField(max_length=200, default="Next-Gen AI Automation")
    hero_subtitle = models.TextField(default="Save hours of manual work with intelligent routing.")
    space_background_url = models.URLField(max_length=500, blank=True)

    def __str__(self):
        return self.site_name


class PricingPlan(EnterpriseBaseModel):
    name = models.CharField(max_length=100, default="Setup Tier")
    price = models.CharField(max_length=50, default="₹35,000")
    features = models.TextField(help_text="List features separated by commas (e.g., AI Routing, API setup, Support)")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['price']

    def __str__(self):
        return f"{self.name} - {self.price}"

    def get_features_list(self):
        if self.features:
            return [feature.strip() for feature in self.features.split(',') if feature.strip()]
        return []


class Policy(EnterpriseBaseModel):
    title = models.CharField(max_length=200, default="Privacy Policy")
    slug = models.SlugField(unique=True, help_text="e.g., privacy-policy")
    content = models.TextField()

    class Meta:
        verbose_name_plural = _("Policies")

    def __str__(self):
        return self.title