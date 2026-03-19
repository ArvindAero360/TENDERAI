"""
extractor/views.py
──────────────────
All Flask routes converted to Django views.

Flask route → Django view mapping
  /                  → IndexView
  /get-token         → GetTokenView
  /post-token        → PostTokenView
  /test              → TestConnectionView
  /extract           → ExtractView
  /run_extraction    → RunExtractionView
  /create_bid        → CreateBidView
  /proxy_post        → ProxyPostView
"""
import json
import os
import tempfile
import traceback

import requests as req_lib
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from .models import Token
from .services import (
    # text extractors
    extract_text_from_pdf,
    extract_text_from_docx,
    extract_text_from_txt,
    # python-only extractors
    extract_prebid_full,
    _extract_emd_amount,
    _extract_epbg_percentage,
    extract_raw_address,
    extract_location_from_atc,
    extract_location_from_text,
    extract_tender_id_from_text,
    _extract_turnover_from_doc,
    _format_turnover_criteria,
    # post-LLM helpers
    extract_qty_from_text,
    extract_state_from_doc,
    get_state_from_location,
    _normalize_state,
    _clean_location,
    _is_valid_place,
    parse_location_from_address,
    _venue_to_city,
    INDIA_STATE_NAMES,
    _STATE_DISPLAY,
    # value helpers
    _str, _notnull, _yn, _bool, _int, _dec, _fix_date,
    # LLM helpers
    EXTRACTION_PROMPT,
    clean_json,
    get_loaded_model,
    _geocode_state_via_osm,
)

from openai import OpenAI
import re as _re


# ── Shared OpenAI/LMStudio client ────────────────────────────────────────────
def _get_client():
    return OpenAI(base_url=settings.LMSTUDIO_BASE_URL, api_key='lm-studio')


# ── helpers ──────────────────────────────────────────────────────────────────
_UT_CITY_NAMES = {'chandigarh', 'delhi', 'new delhi', 'puducherry', 'pondicherry',
                  'leh', 'kargil'}


def _build_flat(x, full_doc_text,
                prebid_dt_raw, prebid_venue,
                py_emd_fee, py_emd_pct,
                py_raw_addr, py_location,
                py_tender_id, py_turnover_field):
    """
    Mirrors the field-assembly block at the bottom of Flask /extract.
    Returns (flat_dict, ev_computed_bool).
    """
    # ── Tender ID
    extracted_tender_id = py_tender_id or _clean_location(x.get('tender_id')) or ''

    # ── State
    raw_ai_state    = _clean_location(x.get('state'))
    extracted_state = _normalize_state(raw_ai_state) if raw_ai_state else None

    # ── Location
    ai_location        = _clean_location(x.get('location')) or ''
    extracted_location = py_raw_addr or py_location or ai_location or ''

    if extracted_location and len(extracted_location) > 40:
        _, parsed_st = parse_location_from_address(extracted_location)
        if parsed_st and not extracted_state:
            extracted_state = parsed_st

    if not extracted_location and prebid_venue:
        loc_from_venue = _venue_to_city(prebid_venue)
        if loc_from_venue and _is_valid_place(loc_from_venue):
            extracted_location = loc_from_venue

    if extracted_location:
        loc_low = extracted_location.lower().strip()
        for sname in sorted(INDIA_STATE_NAMES, key=len, reverse=True):
            if loc_low == sname:
                if not extracted_state:
                    extracted_state = _STATE_DISPLAY.get(sname, sname.title())
                if loc_low not in _UT_CITY_NAMES:
                    extracted_location = ''
                break

    if not extracted_state and extracted_location:
        extracted_state = get_state_from_location(extracted_location) or ''
    if not extracted_state:
        extracted_state = extract_state_from_doc(full_doc_text) or ''

    # ── Dates
    raw_pub   = _str(x.get('published_date'))
    fixed_pub = _fix_date(raw_pub)
    if fixed_pub and 'T' in fixed_pub:
        fixed_pub = fixed_pub.split('T')[0]

    raw_deadline = _str(x.get('submission_deadline'))
    if not raw_deadline:
        m_dl = _re.search(
            r'(?:Bid\s+End\s+Date|बिड\s+बंद|submission\s+deadline)[^\d]*'
            r'(\d{2}-\d{2}-\d{4}\s+\d{2}:\d{2}:\d{2})',
            full_doc_text, _re.I)
        if m_dl:
            raw_deadline = m_dl.group(1)
    fixed_deadline = _fix_date(raw_deadline)

    raw_corr_dt     = _str(x.get('corrigendum_date'))
    fixed_corr_date = raw_corr_dt
    if fixed_corr_date and _re.match(r'\d{2}-\d{2}-\d{4}', fixed_corr_date):
        mc = _re.match(r'(\d{2})-(\d{2})-(\d{4})', fixed_corr_date)
        if mc:
            fixed_corr_date = f'{mc.group(3)}-{mc.group(2)}-{mc.group(1)}'

    if prebid_dt_raw:
        fixed_prebid = _fix_date(prebid_dt_raw)
    else:
        fixed_prebid = _fix_date(_str(x.get('prebid_datetime')))

    # ── EMD / ePBG
    emd_fee_raw = py_emd_fee or _dec(x.get('emd_fee'))
    emd_pct_raw = py_emd_pct or _dec(x.get('emd_percentage'))

    # ── Estimated value
    estimated_value_raw = _dec(x.get('estimated_value'))
    ev_computed = False
    if emd_fee_raw and emd_pct_raw:
        try:
            pct = float(emd_pct_raw)
            if pct > 0:
                estimated_value_raw = '{:.2f}'.format(float(emd_fee_raw) * 100.0 / pct)
                ev_computed = True
        except Exception as ev_err:
            print(f'[extract] EV compute error: {ev_err}')

    # ── Prebid mandatory
    prebid_mandatory = _bool(x.get('prebid_mandatory'))
    if not prebid_mandatory and fixed_prebid:
        prebid_mandatory = True

    # ── RA enabled
    ra_enabled_val = _yn(x.get('ra_enabled'))
    if ra_enabled_val == 'No':
        if _re.search(r'(?i)bid\s+to\s+ra\s+enabled\s+Yes', full_doc_text):
            ra_enabled_val = 'Yes'

    # ── Category
    raw_cat = (_str(x.get('category')) or '').lower()
    if raw_cat in ('product', 'goods', 'supply', 'procurement', 'hardware'):
        final_category = 'Product'
    elif raw_cat in ('service', 'services', 'works', 'work', 'consultancy', 'survey', 'mapping'):
        final_category = 'Service'
    else:
        final_category = (_str(x.get('category')) or '').title() or ''

    # ── Tender authority
    ai_auth = _str(x.get('tender_authority')) or ''
    if not ai_auth or len(ai_auth) < 8:
        parts = []
        for label in [r'Organisation\s+Name[^\n:]+[:\|]\s*([^\n|]+)',
                      r'Department\s+Name[^\n:]+[:\|]\s*([^\n|]+)',
                      r'Ministry[^\n:]+[:\|]\s*([^\n|]+)']:
            m3 = _re.search(label, full_doc_text, _re.I)
            if m3:
                val = m3.group(1).strip().strip('*').strip()
                if val and len(val) > 2 and val not in parts:
                    parts.append(val)
        if parts:
            ai_auth = ', '.join(parts)

    # ── EMD exemption
    emd_exemption_val = _yn(x.get('emd_exemption'))
    if emd_exemption_val == 'No':
        if (_re.search(r'(?i)emd\s+exemption', full_doc_text) and
                _re.search(r'(?i)msme|udyam|mse', full_doc_text)):
            emd_exemption_val = 'Yes'

    flat = {
        'tender_id':            _notnull(extracted_tender_id),
        'title':                _notnull(x.get('title') or extracted_tender_id),
        'tender_authority':     ai_auth or _str(x.get('tender_authority')) or '',
        'category':             final_category,
        'item_description':     _notnull(x.get('item_description')),
        'published_date':       fixed_pub or _str(x.get('published_date')),
        'submission_deadline':  fixed_deadline,
        'date_of_submission':   None,
        'qty':                  extract_qty_from_text(full_doc_text, x.get('qty')),
        'ra_no':                _int(x.get('ra_no')),
        'state':                _clean_location(extracted_state) or '',
        'location':             _clean_location(extracted_location) or '',
        'evaluation_criteria':  _str(x.get('evaluation_criteria')),
        'estimated_value':      estimated_value_raw,
        'tender_budget':        _dec(x.get('tender_budget')),
        'turnover_criteria':    py_turnover_field,
        'emd_fee':              emd_fee_raw,
        'epbg_fee':             _dec(x.get('epbg_fee')),
        'emd_percentage':       emd_pct_raw,
        'emd_exemption':        emd_exemption_val,
        'tender_fee':           _dec(x.get('tender_fee')),
        'tender_fee_exemption': _yn(x.get('tender_fee_exemption')),
        'prebid_mandatory':     prebid_mandatory,
        'prebid_datetime':      fixed_prebid,
        'ra_enabled':           ra_enabled_val,
        'corrigendum':          _yn(x.get('corrigendum')),
        'corrigendum_date':     fixed_corr_date,
        'documents_link':       _notnull(x.get('documents_link')),
        'attachments':          _notnull(x.get('attachments')),
        'attachments_name':     _notnull(x.get('attachments_name')),
        'remarks':              _str(x.get('remarks')),
        'edited_by':            '',
        'edited_datetime':      None,
        'user_name':            'Tender AI',
        'user_id':              '',
        'status':               '-',
        'is_draft':             False,
        'clarification':        '',
        'doability':            None,
        'technically_doable':   None,
        'l1_quoted_price':      None,
        'l1_qualifier':         None,
        'quoted_price':         None,
        'remarks1':             None,
        'remarks2':             None,
        'remarks3':             None,
        'remarks4':             None,
        'time_spent_hours':     '0.00',
        'customization':        None,
        'estimated_date':       None,
        'product_availability': None,
        'spec_customization':   None,
        'tech_doability':       None,
        'total_process_time':   None,
        'doability_estimation': None,
        'consultant':           None,
        'deadline_possibility': None,
        'estimated_date2':      None,
        'move_forward':         None,
        'total_process_time2':  None,
        'technically_verify':   None,
    }
    return flat, ev_computed


# ════════════════════════════════════════════════════════════════════════════
# Views
# ════════════════════════════════════════════════════════════════════════════

@method_decorator(csrf_exempt, name='dispatch')
class IndexView(View):
    """GET / — renders the extractor UI."""
    def get(self, request):
        return render(request, 'extractor/index.html')


@method_decorator(csrf_exempt, name='dispatch')
class GetTokenView(View):
    """GET /get-token — return the most recent token from DB."""
    def get(self, request):
        try:
            token = Token.objects.order_by('-created_at').first()
            if token:
                return JsonResponse({
                    'id':         token.id,
                    'token':      token.token,
                    'created_at': token.created_at.isoformat() if token.created_at else None,
                })
            return JsonResponse({'message': 'Token not found'}, status=404)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class PostTokenView(View):
    """POST /post-token — save a new token to DB."""
    def post(self, request):
        try:
            data  = json.loads(request.body)
            token = data.get('token')
            if not token:
                return JsonResponse({'error': 'Token is required'}, status=400)
            t = Token.objects.create(token=token)
            return JsonResponse({
                'message':      'Token inserted successfully',
                'token':        token,
                'redirect_url': 'http://localhost:5000/',
            })
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class TestConnectionView(View):
    """GET /test — check LMStudio and OSM geocoding."""
    def get(self, request):
        result = {
            'lmstudio_url': settings.LMSTUDIO_BASE_URL,
            'status':       'unknown',
            'model':        None,
            'error':        None,
        }
        try:
            resp = req_lib.get(f'{settings.LMSTUDIO_BASE_URL}/models', timeout=5)
            result['http_status'] = resp.status_code
            models = resp.json().get('data', [])
            result['available_models'] = [m['id'] for m in models]
            if models:
                result['model']  = models[0]['id']
                result['status'] = 'ok'
            else:
                result['status'] = 'connected_but_no_model_loaded'
        except Exception as e:
            result['status'] = 'unreachable'
            result['error']  = str(e)

        probe = _geocode_state_via_osm('Connaught Place, New Delhi')
        result['geocoding'] = (
            f"ok — OSM Nominatim test resolved to '{probe}'"
            if probe
            else 'OSM Nominatim returned no result — check network connectivity'
        )
        return JsonResponse(result)


@method_decorator(csrf_exempt, name='dispatch')
class ExtractView(View):
    """POST /extract — upload a tender document, get back extracted JSON."""

    ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.doc', '.txt'}
    HEAD_TAIL_SIZES    = [(12000, 6000), (7000, 3000), (4000, 2000), (2000, 1000)]

    def post(self, request):
        raw = None
        tmp_path = None
        try:
            if 'file' not in request.FILES:
                return JsonResponse({'error': 'No file uploaded'}, status=400)

            file = request.FILES['file']
            if not file.name:
                return JsonResponse({'error': 'No file selected'}, status=400)

            ext = os.path.splitext(file.name.lower())[1]
            if ext not in self.ALLOWED_EXTENSIONS:
                return JsonResponse(
                    {'error': 'Unsupported file type. Allowed: PDF, DOCX, TXT'}, status=400)

            # Save to temp file
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                for chunk in file.chunks():
                    tmp.write(chunk)
                tmp_path = tmp.name

            # Extract text
            if ext == '.pdf':
                doc_text = extract_text_from_pdf(tmp_path)
            elif ext in ('.docx', '.doc'):
                doc_text = extract_text_from_docx(tmp_path)
            else:
                doc_text = extract_text_from_txt(tmp_path)

            if not doc_text.strip():
                return JsonResponse(
                    {'error': 'No text found. File may be a scanned image PDF.'}, status=400)

            full_doc_text = doc_text

            # ── Python-only extractors (run BEFORE LLM) ──────────────────────
            prebid_dt_raw, prebid_venue = extract_prebid_full(full_doc_text)
            py_emd_fee   = _extract_emd_amount(full_doc_text)
            py_emd_pct   = _extract_epbg_percentage(full_doc_text)
            py_raw_addr  = extract_raw_address(full_doc_text)
            py_location  = (extract_location_from_atc(full_doc_text)
                            or extract_location_from_text(full_doc_text))
            py_tender_id = extract_tender_id_from_text(full_doc_text)

            turnover_dict     = _extract_turnover_from_doc(full_doc_text)
            py_turnover_field = _format_turnover_criteria(turnover_dict)
            print(f"[extract] turnover → bidder={turnover_dict['bidder']!r} "
                  f"oem={turnover_dict['oem']!r}  field={py_turnover_field!r}")

            # ── LLM call ──────────────────────────────────────────────────────
            model_id = get_loaded_model(settings.LMSTUDIO_BASE_URL,
                                        settings.FALLBACK_MODEL)
            if not model_id:
                return JsonResponse(
                    {'error': 'LMStudio unreachable or no model loaded. Visit /test to diagnose.'},
                    status=503)

            client     = _get_client()
            raw        = None
            last_error = None

            for attempt, (head, tail) in enumerate(self.HEAD_TAIL_SIZES, start=1):
                if len(full_doc_text) > head + tail:
                    doc_text_ai = (
                        full_doc_text[:head]
                        + f'\n\n[...MIDDLE TRUNCATED: first {head} + last {tail} chars shown...]\n\n'
                        + full_doc_text[-tail:]
                    )
                else:
                    doc_text_ai = full_doc_text

                print(f'[extract] attempt={attempt}  model={model_id}  '
                      f'total={len(full_doc_text)}  llm={len(doc_text_ai)}')
                try:
                    completion = client.chat.completions.create(
                        model=model_id,
                        max_tokens=1024,
                        temperature=0.1,
                        messages=[
                            {'role': 'system',
                             'content': 'Output ONLY valid JSON, no markdown, no explanation.'},
                            {'role': 'user',
                             'content': EXTRACTION_PROMPT + '\n\n--- DOCUMENT TEXT ---\n' + doc_text_ai},
                        ]
                    )
                    raw = completion.choices[0].message.content
                    print(f'[extract] attempt={attempt} OK  raw[:300]: {raw[:300]}')
                    break
                except Exception as llm_err:
                    last_error = str(llm_err)
                    print(f'[extract] attempt={attempt} FAILED: {last_error}')
                    if attempt < len(self.HEAD_TAIL_SIZES):
                        continue
                    raise

            if raw is None:
                return JsonResponse(
                    {'error': f'LMStudio error after {len(self.HEAD_TAIL_SIZES)} attempts: {last_error}'},
                    status=503)

            x = json.loads(clean_json(raw))

            flat, ev_computed = _build_flat(
                x, full_doc_text,
                prebid_dt_raw, prebid_venue,
                py_emd_fee, py_emd_pct,
                py_raw_addr, py_location,
                py_tender_id, py_turnover_field,
            )
            return JsonResponse({'success': True, 'data': flat, 'ev_computed': ev_computed})

        except json.JSONDecodeError as e:
            print(f'[extract] JSON parse failed: {e}\nRaw:\n{raw}')
            return JsonResponse(
                {'error': f'Bad JSON from model: {e}',
                 'raw_response': (raw or '')[:2000]}, status=500)
        except Exception as e:
            tb = traceback.format_exc()
            print(f'[extract] ERROR:\n{tb}')
            return JsonResponse({'error': str(e), 'traceback': tb}, status=500)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)


@method_decorator(csrf_exempt, name='dispatch')
class RunExtractionView(View):
    """POST /run_extraction — forward already-extracted data to Django API."""
    def post(self, request):
        payload = json.loads(request.body or '{}')
        token   = payload.get('token')
        if not token:
            return JsonResponse({'error': 'No token provided'}, status=401)

        fwd_headers = {
            'Content-Type':  'application/json',
            'Authorization': f'Token {token}',
        }
        extracted_data = payload.get('data')
        if not extracted_data:
            return JsonResponse({'error': 'No extracted data provided'}, status=400)

        try:
            r        = req_lib.post(settings.DJANGO_API_URL, json=extracted_data,
                                    headers=fwd_headers, timeout=30)
            resp_data = r.json()
            bid_id    = resp_data.get('id')
            redirect_url = f'{settings.REDIRECT_BASE}{bid_id}' if bid_id else None
            return JsonResponse({**resp_data, 'redirect_url': redirect_url}, status=r.status_code)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=502)


@method_decorator(csrf_exempt, name='dispatch')
class CreateBidView(View):
    """POST /create_bid — forward flat bid fields to Django API with auth token."""
    def post(self, request):
        payload = json.loads(request.body or '{}')
        token   = payload.pop('token', None)
        if not token:
            return JsonResponse({'error': 'No token provided'}, status=401)

        fwd_headers = {
            'Content-Type':  'application/json',
            'Authorization': f'Token {token}',
        }
        print(f'[create_bid] token={token[:10]}…  fields={list(payload.keys())}')
        try:
            r = req_lib.post(settings.DJANGO_API_URL, json=payload,
                             headers=fwd_headers, timeout=30)
            print(f'[create_bid] ← HTTP {r.status_code}  body={r.text[:200]}')
            try:
                resp_data = r.json()
            except Exception:
                resp_data = {'raw': r.text}
            bid_id       = resp_data.get('id')
            redirect_url = f'{settings.REDIRECT_BASE}{bid_id}' if bid_id else None
            return JsonResponse({**resp_data, 'redirect_url': redirect_url}, status=r.status_code)
        except Exception as e:
            print(f'[create_bid] ERROR: {e}')
            return JsonResponse({'error': str(e)}, status=502)


@method_decorator(csrf_exempt, name='dispatch')
class ProxyPostView(View):
    """POST /proxy_post — proxy frontend submissions to Django backend API."""
    def post(self, request):
        payload = json.loads(request.body or '{}')
        token   = payload.pop('token', None)
        if not token:
            return JsonResponse({'error': 'No token provided'}, status=401)

        fwd_headers = {
            'Content-Type':  'application/json',
            'Authorization': f'Token {token}',
        }
        print(f'\n[proxy_post] → {settings.DJANGO_API_URL}  token={token[:10]}…')
        try:
            r = req_lib.post(settings.DJANGO_API_URL, json=payload,
                             headers=fwd_headers, timeout=30)
            print(f'[proxy_post] ← HTTP {r.status_code}  body={r.text[:200]}')
            try:
                resp_data = r.json()
            except Exception:
                resp_data = {}
            bid_id       = resp_data.get('id')
            redirect_url = f'{settings.REDIRECT_BASE}{bid_id}' if bid_id else None
            return JsonResponse({**resp_data, 'redirect_url': redirect_url}, status=r.status_code)
        except Exception as e:
            print(f'[proxy_post] ERROR: {e}')
            return JsonResponse({'error': str(e)}, status=502)
