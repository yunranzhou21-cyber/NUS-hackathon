"""On-demand public Federal Register text; PDF is a separate explicit operation."""
import hashlib
import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from applicability import evaluate, parse_block, parse_record

DOC_NUMBER = re.compile(r'[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+')
HOSTS = {'www.federalregister.gov', 'federalregister.gov', 'www.govinfo.gov', 'www.gpo.gov'}
VERSION = 1


class FetchError(ValueError):
    pass


def checked_url(url):
    p = urlsplit(url)
    if (p.scheme != 'https' or p.hostname not in HOSTS or p.username or p.password
            or p.port not in (None, 443)):
        raise FetchError('UNSUPPORTED_SOURCE_URL')
    return url


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        checked_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, limit):
    req = Request(checked_url(url), headers={'User-Agent': 'TraceAD/1.0 (public-document research)'})
    with build_opener(SafeRedirect()).open(req, timeout=20) as response:
        checked_url(response.url)
        if int(response.headers.get('Content-Length', '0')) > limit:
            raise FetchError('DOCUMENT_TOO_LARGE')
        body = response.read(limit + 1)
    if len(body) > limit:
        raise FetchError('DOCUMENT_TOO_LARGE')
    return body


def document_number(record):
    url = record['source'].get('url')
    if not url:
        return None
    try:
        checked_url(url)
    except (FetchError, ValueError):
        return None
    p = urlsplit(url)
    match = re.match(r'^/documents/\d{4}/\d{2}/\d{2}/([^/]+)(?:/|$)', p.path)
    if not match or not DOC_NUMBER.fullmatch(match[1]):
        return None
    return match[1]


def parse_xml(body):
    """Create a stable text artifact with explicit printed-page transitions."""
    if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
        raise FetchError('UNSUPPORTED_XML_DECLARATION')
    root = ET.fromstring(body)
    if root.tag.rsplit('}', 1)[-1] not in ('PRORULE', 'RULE', 'NOTICE', 'PRESDOCU', 'FRDOC', 'DOCUMENT'):
        raise FetchError('UNEXPECTED_XML_DOCUMENT')
    parts, blocks, transitions = [], [], []
    position, section = 0, 'Document'

    def emit(text):
        nonlocal position
        parts.append(text)
        position += len(text)

    def walk(element):
        nonlocal section
        tag = element.tag.rsplit('}', 1)[-1]
        if tag in ('PRTPAGE', 'PGBRK'):
            page = element.get('P') or element.get('N')
            if page and page.isdigit():
                transitions.append({'offset': position, 'page': int(page)})
            return
        is_block = tag in ('HD', 'P', 'ROW', 'SECTNO', 'SUBJECT')
        if is_block or tag == 'ENT':
            emit('\n')
        label = re.sub(r'\s+', ' ', ''.join(element.itertext())).strip()
        is_heading = tag == 'HD' or (tag == 'P' and re.fullmatch(r'\([a-z]\) [A-Za-z /-]{2,65}', label))
        if is_heading:
            section = label.rstrip(':')
        start, own_section = position, section
        if element.text:
            emit(re.sub(r'\s+', ' ', element.text))
        for child in element:
            walk(child)
            if child.tail:
                emit(re.sub(r'\s+', ' ', child.tail))
        end = position
        if is_block and end > start:
            blocks.append({'start': start, 'end': end, 'section': own_section,
                           'heading': bool(is_heading)})
    walk(root)
    text = ''.join(parts)
    if len(text.strip()) < 40:
        raise FetchError('EMPTY_FULL_TEXT')
    return {'text': text, 'blocks': sorted(blocks, key=lambda b: b['start']),
            'page_transitions': transitions, 'xml_root': root.tag.rsplit('}', 1)[-1]}


def evidence(doc, record, start, end, section):
    transitions = doc['page_transitions']
    current = next((p['page'] for p in reversed(transitions) if p['offset'] <= start), None)
    pages = ([current] if current else []) + [p['page'] for p in transitions if start < p['offset'] < end]
    pages = list(dict.fromkeys(pages))
    meta = doc['metadata']
    return {'record_id': record['record_id'], 'field': 'full_text', 'start': start, 'end': end,
            'quote': doc['text'][start:end], 'section': section, 'source': {
                'docket': record['normalized']['docket'], 'document_number': doc['document_number'],
                'citation': meta.get('citation'), 'publication_date': meta.get('publication_date'),
                'url': meta.get('html_url'), 'xml_url': meta.get('full_text_xml_url'),
                'pdf_url': meta.get('pdf_url'), 'pages': pages, 'section': section,
                'published_page_range': [meta.get('start_page'), meta.get('end_page')],
                'page_precision': 'xml_printed_page_markers' if pages else 'document_range_only_not_exact_claim_page',
                'xml_sha256': doc['xml_sha256'], 'text_sha256': doc['text_sha256'],
                'fetched_at': doc['fetched_at']}}


def compare_document(doc, record, configuration, historical=False):
    """Only a complete, explicitly bounded Applicability section is executable."""
    parsed = parse_record(record)
    parsed.update(rule=None, applicability_blocks=[], parse_status='UNSUPPORTED',
                  reasons=['FULL_TEXT_APPLICABILITY_UNRESOLVED'])
    action = doc['metadata'].get('action', '').lower()
    if 'withdraw' in action:
        parsed['document_kind'] = 'withdrawal_notice'
    elif 'extension' in action:
        parsed['document_kind'] = 'comment_period_extension'
    elif 'correction' in action:
        parsed['document_kind'] = 'correction_notice'
    elif doc['metadata'].get('type') != 'Proposed Rule':
        parsed['document_kind'] = 'needs_review'
    headings = [b for b in doc['blocks'] if b['heading']]
    applicable = [i for i, b in enumerate(headings)
                  if re.fullmatch(r'(?:\([a-z0-9]+\)\s*)?Applicability\s*:?', doc['text'][b['start']:b['end']].strip(), re.I)]
    if len(applicable) == 1:
        i = applicable[0]
        heading = headings[i]
        start = heading['end']
        end = headings[i + 1]['start'] if i + 1 < len(headings) else len(doc['text'])
        ev = evidence(doc, record, start, end, heading['section'])
        block = {'body': ev['quote'], 'evidence': ev, 'boundary_found': i + 1 < len(headings)}
        parsed['applicability_blocks'] = [block]
        rule, reason = parse_block(block)
        if rule:
            parsed.update(rule=rule, parse_status='SUPPORTED_TEXT_GRAMMAR', reasons=[])
        else:
            parsed['reasons'] = [reason]
    result = evaluate(parsed, configuration, historical)
    result['basis'] = 'FETCHED_FULL_TEXT'
    return result


class FullTextStore:
    def __init__(self, records, cache='data/fulltext', fetch=download, ttl=86400, budget=50 * 1024 * 1024):
        self.records = {r['record_id']: r for r in records}
        self.cache, self.fetch, self.ttl, self.budget = Path(cache), fetch, ttl, budget
        self.lock = threading.RLock()
        self.jobs = {}
        self.document_locks = {}
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='fulltext')

    def close(self):
        self.pool.shutdown(wait=True)

    def _path(self, number, suffix):
        if not DOC_NUMBER.fullmatch(number):
            raise FetchError('INVALID_DOCUMENT_NUMBER')
        return self.cache / (number + suffix)

    def _write(self, path, body):
        with self.lock:
            self.cache.mkdir(parents=True, exist_ok=True)
            used = sum(p.stat().st_size for p in self.cache.iterdir() if p.is_file())
            previous = path.stat().st_size if path.exists() else 0
            if used - previous + len(body) > self.budget:
                raise FetchError('CACHE_LIMIT_REACHED')
            tmp = path.with_suffix(path.suffix + '.tmp')
            tmp.write_bytes(body)
            tmp.replace(path)

    def cached(self, number):
        path = self._path(number, '.json')
        try:
            doc = json.loads(path.read_text(encoding='utf-8'))
            if doc['version'] != VERSION or doc['document_number'] != number:
                return None
            if hashlib.sha256(doc['text'].encode()).hexdigest() != doc['text_sha256']:
                return None
            return doc
        except (OSError, ValueError, KeyError):
            return None

    def _load_text(self, number):
        meta = json.loads(self.fetch(f'https://www.federalregister.gov/api/v1/documents/{number}.json', 1024 * 1024))
        if meta.get('document_number') != number:
            raise FetchError('DOCUMENT_IDENTITY_MISMATCH')
        xml_url = checked_url(meta['full_text_xml_url'])
        if not urlsplit(xml_url).path.endswith('/' + number + '.xml'):
            raise FetchError('DOCUMENT_IDENTITY_MISMATCH')
        xml = self.fetch(xml_url, 5 * 1024 * 1024)
        doc = parse_xml(xml)
        doc.update(version=VERSION, metadata=meta, document_number=number,
                   fetched_at=datetime.now(timezone.utc).isoformat(), fetched_epoch=time.time(),
                   xml_sha256=hashlib.sha256(xml).hexdigest(), xml_bytes=len(xml),
                   text_sha256=hashlib.sha256(doc['text'].encode()).hexdigest())
        self._write(self._path(number, '.xml'), xml)
        self._write(self._path(number, '.json'), json.dumps(doc, ensure_ascii=False).encode())

    def _work(self, number, action):
        with self.lock:
            document_lock = self.document_locks.setdefault(number, threading.Lock())
        with document_lock:
            self._work_locked(number, action)

    def _work_locked(self, number, action):
        try:
            doc = self.cached(number)
            if not doc or time.time() - doc['fetched_epoch'] > self.ttl:
                self._load_text(number)
                doc = self.cached(number)
            if action == 'pdf':
                pdf = self.fetch(checked_url(doc['metadata']['pdf_url']), 10 * 1024 * 1024)
                if not pdf.startswith(b'%PDF-'):
                    raise FetchError('INVALID_PDF_RESPONSE')
                self._write(self._path(number, '.pdf'), pdf)
            with self.lock:
                self.jobs[(number, action)] = {'status': 'READY'}
        except Exception as exc:
            code = str(exc) if isinstance(exc, FetchError) else 'SOURCE_FETCH_FAILED'
            with self.lock:
                self.jobs[(number, action)] = {'status': 'FAILED', 'error': code}

    def request(self, record_id, action='status', retry=False):
        if record_id not in self.records:
            raise FetchError('UNKNOWN_RECORD_ID')
        rec = self.records[record_id]
        number = document_number(rec)
        if not number:
            return {'status': 'UNAVAILABLE', 'error': 'NO_RESOLVED_OFFICIAL_LINK'}
        with self.lock:
            doc = self.cached(number)
            fresh = bool(doc and time.time() - doc['fetched_epoch'] <= self.ttl)
            state = {'status': 'READY' if fresh else 'STALE' if doc else 'NOT_CACHED'}
            if action == 'pdf' and fresh and self._path(number, '.pdf').exists():
                state['pdf_url'] = f'/api/fulltext/pdf/{number}'
            elif action != 'status' and not (action == 'text' and fresh):
                state = self.jobs.get((number, action), {})
                if not state or state['status'] == 'READY' or (retry and state['status'] == 'FAILED'):
                    # Bound outstanding work as well as simultaneous downloads.
                    if sum(j['status'] == 'FETCHING' for j in self.jobs.values()) >= 8:
                        return {'status': 'FAILED', 'error': 'FETCH_QUEUE_FULL'}
                    state = {'status': 'FETCHING'}
                    self.jobs[(number, action)] = state
                    self.pool.submit(self._work, number, action)
            elif action == 'status' and not fresh:
                job = self.jobs.get((number, 'text'))
                if job and job['status'] != 'READY':
                    state = job
            return {**state, 'record_id': record_id, 'document_number': number,
                    'cached': fresh, 'pdf_cached': self._path(number, '.pdf').exists(),
                    'fetched_at': doc['fetched_at'] if doc else None}

    def result(self, record_id, configuration, query='', historical=False):
        rec = self.records[record_id]
        number = document_number(rec)
        doc = self.cached(number) if number else None
        if not doc or time.time() - doc['fetched_epoch'] > self.ttl:
            return None
        dockets = doc['metadata'].get('docket_ids') or []
        expected = rec['normalized']['docket'].lower()
        if dockets and not any(re.search(r'(?<![a-z0-9-])' + re.escape(expected) + r'(?![a-z0-9-])', d.lower()) for d in dockets):
            raise FetchError('DOCKET_IDENTITY_MISMATCH')
        terms = set(re.findall(r'[a-z0-9-]{3,}', query.lower()))
        blocks = [b for b in doc['blocks'] if not b['heading']]
        ranked = sorted(blocks, key=lambda b: (
            -int('applicability' in b['section'].lower()),
            -sum(t in doc['text'][b['start']:b['end']].lower() for t in terms), b['start']))[:8]
        passages = [evidence(doc, rec, b['start'], min(b['end'], b['start'] + 2400), b['section']) for b in ranked]
        return {'configuration_check': compare_document(doc, rec, configuration, historical),
                'evidence': passages, 'sections': list(dict.fromkeys(b['section'] for b in doc['blocks'])),
                'citation': doc['metadata'].get('citation'), 'publication_date': doc['metadata'].get('publication_date'),
                'xml_bytes': doc['xml_bytes'], 'xml_sha256': doc['xml_sha256'],
                'note': 'Fetched published text; legal effect and current regulation remain unverified.'}

    def stats(self):
        files = list(self.cache.glob('*')) if self.cache.exists() else []
        docs = [d for p in files if p.suffix == '.json' for d in [self.cached(p.stem)] if d]
        return {'cached_documents': len(docs), 'pdf_documents': sum(p.suffix == '.pdf' for p in files),
                'cache_bytes': sum(p.stat().st_size for p in files if p.is_file()),
                'cache_budget_bytes': self.budget, 'cache_ttl_seconds': self.ttl,
                'printed_pages_observed_in_xml': len({(d['metadata']['publication_date'], p['page'])
                    for d in docs if d['metadata'].get('publication_date') for p in d['page_transitions']}),
                'verified_1000_page_requirement': False}
