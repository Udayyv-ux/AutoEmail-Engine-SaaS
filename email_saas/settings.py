import os
from pathlib import Path
import dj_database_url

# =====================================================================
# 1. BASE CONFIGURATION & ENVIRONMENT
# =====================================================================
BASE_DIR = Path(__file__).resolve().parent.parent

# Security Warning: Fail fast if the secret key is missing in production.
SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-master-key-replace-in-production')

# Cast DEBUG string from environment to boolean securely
DEBUG = os.environ.get('DEBUG', 'True').lower() in ('true', '1', 't')

# Railway assigns dynamic domains. It's best to pull ALLOWED_HOSTS from the environment, 
# but fallback to '*' if needed during initial deployment.
_env_hosts = os.environ.get('ALLOWED_HOSTS', '*')
ALLOWED_HOSTS = [host.strip() for host in _env_hosts.split(',')]

# =====================================================================
# 2. APPLICATION DEFINITION
# =====================================================================
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Internal Domain Apps
    'core',
    
    # Third-Party Apps
    'django.contrib.sites',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # WhiteNoise MUST be exactly here (under SecurityMiddleware) to serve static files
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',
]

ROOT_URLCONF = 'email_saas.urls'
WSGI_APPLICATION = 'email_saas.wsgi.application'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

# =====================================================================
# 3. DATABASE (Dynamic Railway Connection)
# =====================================================================
# Uses Neon DB default, but allows Railway to inject DATABASE_URL automatically
DATABASES = {
    'default': dj_database_url.config(
        default='postgresql://neondb_owner:npg_Scm2JeZ4ohgA@ep-curly-river-apf4z7eb-pooler.c-7.us-east-1.aws.neon.tech/neondb?sslmode=require',
        conn_max_age=0,  # 0 is required for serverless databases like Neon to prevent SSL drops
        conn_health_checks=True
    )
}

# =====================================================================
# 4. STATIC & MEDIA FILES (Django 4.2+ Configuration)
# =====================================================================
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# Consolidated STORAGES dictionary (Django 4.2+)
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        # Compresses files and appends MD5 hashes to URLs for aggressive caching
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# =====================================================================
# 5. SECURITY & PROXY HEADERS (For Railway/Cloudflare)
# =====================================================================
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True
CSRF_TRUSTED_ORIGINS = [
    'https://*.app.github.dev', 
    'https://*.githubpreview.dev', 
    'http://localhost:8000', 
    'http://127.0.0.1:8000',
    'https://*.up.railway.app'
]

# Strict security settings trigger ONLY when DEBUG is False (Production)
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_HSTS_SECONDS = 31536000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# =====================================================================
# 6. ALLAUTH / SAAS CONFIGURATION
# =====================================================================
AUTH_USER_MODEL = 'core.CustomUser'
SITE_ID = 1

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# Strict Allauth SaaS settings
SOCIALACCOUNT_STORE_TOKENS = True 
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True
ACCOUNT_EMAIL_VERIFICATION = "none"
SOCIALACCOUNT_LOGIN_ON_GET = True
ACCOUNT_LOGIN_METHODS = {'email'}

# --- THE ALLAUTH FIX ---
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_USER_MODEL_USERNAME_FIELD = 'username'
ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*', 'password2*']
# -----------------------

LOGIN_REDIRECT_URL = '/dashboard-router/' 
LOGOUT_REDIRECT_URL = '/accounts/login/'
ACCOUNT_LOGOUT_ON_GET = True

SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'APP': {
            'client_id': os.environ.get('GOOGLE_CLIENT_ID', ''),
            'secret': os.environ.get('GOOGLE_CLIENT_SECRET', ''),
            'key': ''
        },
        'SCOPE': [
            'profile',
            'email',
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive.readonly',
            'https://www.googleapis.com/auth/gmail.send',
        ],
        'AUTH_PARAMS': {
            'access_type': 'offline',
            'prompt': 'consent',
        }
    }
}

# =====================================================================
# 7. INTERNATIONALIZATION
# =====================================================================
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# =====================================================================
# 8. ENTERPRISE LOGGING OBSERVABILITY
# =====================================================================
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'enterprise': {
            'format': '%(asctime)s [%(levelname)s] [%(name)s] %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
    },
    'handlers': {
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'enterprise',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': os.environ.get('DJANGO_LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
        'core': {  # Targets the 'core' app (where your services.py lives)
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}