# TenderIQ — Django Edition
Converted from Flask app.py to a full Django project.

## Project layout

```
tenderiq_django/
├── manage.py
├── requirements.txt
├── tenderiq_django/          ← Django project package
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
└── extractor/                ← App (all logic lives here)
    ├── models.py             ← Token model (mirrors MySQL `tokens` table)
    ├── services.py           ← All extraction logic (pure Python, no Django imports)
    ├── views.py              ← All Flask routes → Django class-based views
    ├── urls.py               ← URL routing
    └── templates/
        └── extractor/
            └── index.html    ← Identical UI to the original Flask template
```

## Flask → Django route mapping

| Flask route       | Django view        | URL name          |
|-------------------|--------------------|-------------------|
| GET  /            | IndexView          | index             |
| GET  /get-token   | GetTokenView       | get_token         |
| POST /post-token  | PostTokenView      | post_token        |
| GET  /test        | TestConnectionView | test_connection   |
| POST /extract     | ExtractView        | extract           |
| POST /run_extraction | RunExtractionView | run_extraction  |
| POST /create_bid  | CreateBidView      | create_bid        |
| POST /proxy_post  | ProxyPostView      | proxy_post        |

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. (If using PyMySQL instead of mysqlclient, add to tenderiq_django/__init__.py:)
#    import pymysql; pymysql.install_as_MySQLdb()

# 3. Update DB credentials in tenderiq_django/settings.py if needed

# 4. Since the tokens table already exists (created by Flask), no migration needed.
#    If starting fresh:
python manage.py migrate

# 5. Run development server (same port as Flask)
python manage.py runserver 0.0.0.0:5000
```

## Production

```bash
gunicorn tenderiq_django.wsgi:application --bind 0.0.0.0:5000 --workers 4
```

## Key differences from Flask

- CSRF is **disabled per-view** via `@csrf_exempt` (mirrors Flask's behaviour).
  Enable it for HTML form views if you add Django auth later.
- `Token` model uses `managed = False` so Django won't touch the existing table.
  Set `managed = True` for a fresh DB and run `python manage.py migrate`.
- All extraction logic is in `extractor/services.py` — identical to Flask, no Django deps.
- Settings for LMStudio URL, Django API URL, and redirect base are in `settings.py`.
