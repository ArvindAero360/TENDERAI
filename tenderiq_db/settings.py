"""
TenderIQ Django Settings
Converted from Flask app — drop-in replacement.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = 'django-insecure-tenderiq-change-this-in-production-xyz123'

DEBUG = True

ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.staticfiles',
    'corsheaders',
    'extractor',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
]

ROOT_URLCONF = 'tenderiq_django.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
            ],
        },
    },
]

WSGI_APPLICATION = 'tenderiq_django.wsgi.application'

# ── Database (MySQL — mirrors Flask app) ──────────────────────────────────
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME':   'tender_token',
        'USER':   'root',
        'PASSWORD': 'Lendi_03@2003',
        'HOST':   'localhost',
        'PORT':   '3306',
        'OPTIONS': {
            'charset': 'utf8mb4',
        },
    }
}

# ── Static files ─────────────────────────────────────────────────────────
STATIC_URL = '/static/'

# ── File upload limit: 50 MB ─────────────────────────────────────────────
DATA_UPLOAD_MAX_MEMORY_SIZE  = 50 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE  = 50 * 1024 * 1024

# ── CORS ─────────────────────────────────────────────────────────────────
CORS_ALLOW_ALL_ORIGINS = True

# ── App-specific settings ────────────────────────────────────────────────
LMSTUDIO_API_URL  = 'http://127.0.0.1:1234'
LMSTUDIO_BASE_URL = f'{LMSTUDIO_API_URL}/v1'
FALLBACK_MODEL    = 'mistral-nemo-12b-instruct-2407'

DJANGO_API_URL    = 'https://dms.aero360.co.in/api3/api/create/'
REDIRECT_BASE     = 'https://dms.aero360.co.in/app3/allbid/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
