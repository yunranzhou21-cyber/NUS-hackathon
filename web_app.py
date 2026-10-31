"""Loopback-only demo server. Run: python web_app.py"""
import argparse
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

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
        elif path in ASSETS:
            filename, mime = ASSETS[path]
            self.send(200, (ROOT / 'web' / filename).read_bytes(), mime)
        else:
            self.json(404, {'error': 'NOT_FOUND'})

    def do_POST(self):
        if self.path != '/api/search':
            return self.json(404, {'error': 'NOT_FOUND'})
        # No CORS; also reject browser requests initiated from another origin.
        origin = self.headers.get('Origin')
        allowed = {f'http://127.0.0.1:{self.server.server_port}',
                   f'http://localhost:{self.server.server_port}'}
        if origin and origin not in allowed:
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
        engine = None
        try:
            engine = SearchEngine(str(ROOT / 'data/processed/records.jsonl'),
                                  str(ROOT / 'data/search/search.sqlite3'))
            self.json(200, engine.search(query, config, limit, history))
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
