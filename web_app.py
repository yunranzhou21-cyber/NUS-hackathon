"""Loopback-only demo server. Run: python web_app.py"""
import argparse
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from query_understanding import (clear_runtime, get_provider, interpret,
                                 provider_for as openai_provider_for, set_runtime,
                                 status as ai_status, test_connection,
                                 validate_settings)
from search import SearchEngine, build_index

ROOT = Path(__file__).resolve().parent
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/favicon.svg': ('favicon.svg', 'image/svg+xml'),
          '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
          '/style.css': ('style.css', 'text/css; charset=utf-8')}


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, mime='application/json; charset=utf-8'):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def json(self, status, value):
        self.send(status, json.dumps(value, ensure_ascii=False).encode('utf-8'))

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/health':
            self.json(200, {'status': 'ok', 'scope': 'LOCAL_DEMO'})
        elif path == '/api/status':
            info = ai_status()
            # get_provider is the single source of truth, so tests can patch it.
            info['configured'] = get_provider() is not None
            self.json(200, {'ai_configured': info['configured'], **info})
        elif path in ASSETS:
            filename, mime = ASSETS[path]
            self.send(200, (ROOT / 'web' / filename).read_bytes(), mime)
        else:
            self.json(404, {'error': 'NOT_FOUND'})

    def origin_ok(self):
        # No CORS; reject browser requests initiated from another origin.
        origin = self.headers.get('Origin')
        allowed = {f'http://127.0.0.1:{self.server.server_port}',
                   f'http://localhost:{self.server.server_port}'}
        return not origin or origin in allowed

    def ai_config(self):
        if not self.origin_ok():
            return self.json(403, {'error': 'ORIGIN_NOT_ALLOWED'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 2048:
                return self.json(413, {'error': 'INVALID_BODY_SIZE'})
            if self.headers.get_content_type() != 'application/json':
                return self.json(415, {'error': 'JSON_REQUIRED'})
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict) or value.get('action') not in ('connect', 'disconnect'):
                raise ValueError()
        except (ValueError, UnicodeError):
            return self.json(400, {'error': 'INVALID_REQUEST'})
        if value['action'] == 'disconnect':
            clear_runtime()
            info = ai_status()
            return self.json(200, {**info, 'ai_configured': info['configured']})
        settings, error = validate_settings(
            value.get('api_key'), value.get('model'), value.get('base_url'))
        if error:
            return self.json(400, {'error': error})
        ok, reason = test_connection(openai_provider_for(settings))
        if not ok:
            # A key that failed its test is never kept.
            return self.json(200, {**ai_status(), 'test': 'failed', 'reason': reason})
        set_runtime(settings)
        info = ai_status()
        self.json(200, {**info, 'ai_configured': info['configured'], 'test': 'ok'})

    def do_POST(self):
        if self.path == '/api/ai-config':
            return self.ai_config()
        if self.path != '/api/search':
            return self.json(404, {'error': 'NOT_FOUND'})
        if not self.origin_ok():
            return self.json(403, {'error': 'ORIGIN_NOT_ALLOWED'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 8192:
                return self.json(413, {'error': 'INVALID_BODY_SIZE'})
            if self.headers.get_content_type() != 'application/json':
                return self.json(415, {'error': 'JSON_REQUIRED'})
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError()
            query = value.get('query')
            config = value.get('configuration', {})
            limit = value.get('limit', 5)
            history = value.get('include_history', False)
            if (not isinstance(query, str) or len(query) > 1000
                    or not isinstance(config, dict)
                    or set(config) - {'make', 'model', 'serial_number', 'line_number'}
                    or any(not isinstance(v, str) or len(v) > 150 for v in config.values())
                    or type(limit) is not int or not 1 <= limit <= 20
                    or type(history) is not bool):
                raise ValueError()
        except (ValueError, UnicodeError):
            return self.json(400, {'error': 'INVALID_REQUEST'})
        use_ai = value.get('use_ai', False)
        if type(use_ai) is not bool:
            return self.json(400, {'error': 'INVALID_REQUEST'})
        engine = None
        try:
            engine = SearchEngine(str(ROOT / 'data/processed/records.jsonl'),
                                  str(ROOT / 'data/search/search.sqlite3'))
            provider = get_provider() if use_ai else None
            interpretation = interpret(query, provider, engine.makes)
            if use_ai and provider is None:
                interpretation['notice'] = 'AI_NOT_CONFIGURED_USING_LOCAL_RULES'
            config = dict(config)
            for key, found in interpretation['fields'].items():
                typed = config.get(key, '').strip()
                if typed and typed.lower() != found.lower():
                    interpretation['conflict'] = key
                    return self.json(200, {
                        'status': 'NEEDS_CLARIFICATION', 'query': query, 'results': [],
                        'reason_codes': ['QUERY_CONFIGURATION_CONFLICT'],
                        'interpretation': interpretation, 'retrieval_ms': 0,
                        'scope': 'SUPPLIED_CSV_ONLY', 'operational_answer': 'REFUSE',
                        'legal_applicability': 'UNVERIFIED', 'not_exhaustive': True})
                if not typed:
                    config[key] = found
            if interpretation['intent'] == 'find_history':
                history = True
            result = engine.search(query, config, limit, history)
            result['interpretation'] = interpretation
            self.json(200, result)
        except (ValueError, sqlite3.Error, OSError):
            self.json(503, {'error': 'INDEX_UNAVAILABLE',
                            'message': 'Run python search.py build, then retry.'})
        finally:
            if engine:
                engine.close()

    def log_message(self, format, *args):
        # Do not log submitted configurations or query bodies.
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    database = ROOT / 'data/search/search.sqlite3'
    if not database.exists():
        build_index(str(ROOT / 'data/processed/records.jsonl'), str(database))
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Local demo: http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
