"""Local demonstration server. Python standard library only."""
import json
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import BoundedSemaphore
import argparse

ROOT = Path(__file__).resolve().parent
OLLAMA = 'http://127.0.0.1:11434'
MODEL = 'tev1'
BUSY = BoundedSemaphore(1)


def ollama(path, body=None, timeout=5):
    data = None if body is None else json.dumps(body).encode('utf-8')
    req = urllib.request.Request(OLLAMA + path, data=data, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


class Handler(BaseHTTPRequestHandler):
    def send_json(self, code, data):
        payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == '/api/health':
            return self.send_json(200, {'app': 'tev1-consulting-demo', 'version': 2, 'directory': str(ROOT), 'model': MODEL})
        if self.path == '/api/status':
            try:
                version = ollama('/api/version').get('version', '')
                tags = ollama('/api/tags').get('models', [])
                target = MODEL if ':' in MODEL else MODEL + ':latest'
                present = any(m.get('name') == target for m in tags)
                running = ollama('/api/ps').get('models', [])
                active = next((m for m in running if m.get('name') == target), None)
                return self.send_json(200, {'online': True, 'installed': present, 'loaded': bool(active), 'gpu': bool(active and active.get('size_vram', 0) > 0), 'version': version})
            except (OSError, ValueError):
                return self.send_json(200, {'online': False, 'installed': False, 'loaded': False})
        if self.path not in ('/', '/index.html'):
            return self.send_json(404, {'error': 'Seite nicht gefunden.'})
        payload = (ROOT / 'index.html').read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        if self.path not in ('/api/classify', '/api/warmup'):
            return self.send_json(404, {'error': 'Schnittstelle nicht gefunden.'})
        # Only the local page may issue browser POSTs. No CORS exception needed.
        origin = self.headers.get('Origin')
        if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'):
            return self.send_json(403, {'error': 'Bitte die lokale Demo-Seite verwenden.'})
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 20000:
                return self.send_json(400, {'error': 'Die Anfrage ist leer oder zu lang.'})
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('Ungültige Anfrage.')
            if self.path == '/api/warmup':
                state, question, options = 'Hello World', 'Does this text contain a greeting?', {'yes': 'A greeting is present.', 'no': 'No greeting is present.'}
            else:
                state = body.get('text', '')
                question = body.get('question', '')
                options = body.get('options', {})
                if not isinstance(state, str) or not 1 <= len(state.strip()) <= 3500:
                    raise ValueError('Bitte einen Text mit 1 bis 3.500 Zeichen eingeben.')
                if not isinstance(question, str) or not 1 <= len(question.strip()) <= 400:
                    raise ValueError('Bitte eine Frage mit höchstens 400 Zeichen eingeben.')
                if not isinstance(options, dict) or not 2 <= len(options) <= 8:
                    raise ValueError('Bitte 2 bis 8 unterschiedliche Kategorien angeben.')
                if any(not isinstance(k, str) or not k.strip() or len(k) > 70 or not isinstance(v, str) or not v.strip() or len(v) > 250 for k, v in options.items()):
                    raise ValueError('Kategorien benötigen einen Namen und eine kurze Beschreibung.')
            if not BUSY.acquire(blocking=False):
                return self.send_json(409, {'error': 'Tev1 bearbeitet gerade eine Anfrage. Bitte kurz warten.'})
            try:
                started = time.perf_counter()
                result = ollama('/v1/systemone', {
                    'model': MODEL,
                    'state': state.strip(),
                    'questions': {'classification': {'type': 'choice', 'instructions': question.strip(), 'criteria': options}},
                    'keep_alive': '30m',
                }, timeout=600 if self.path == '/api/warmup' else 180)
                elapsed = round((time.perf_counter() - started) * 1000, 1)
                answer = result['answers']['classification']
                if answer.get('choice') not in options or not isinstance(answer.get('probabilities'), dict):
                    raise ValueError('Das Modell hat keine passende Antwort geliefert.')
                self.send_json(200, {'model': result.get('model', MODEL), 'answer': answer, 'elapsed_ms': elapsed, 'usage': result.get('usage', {})})
            finally:
                BUSY.release()
        except (ValueError, TypeError, KeyError) as error:
            self.send_json(400, {'error': str(error) or 'Ungültige Anfrage.'})
        except urllib.error.HTTPError as error:
            try:
                detail = json.load(error).get('error', '')
            except (ValueError, OSError):
                detail = ''
            if error.code == 404:
                message = 'Tev1 fehlt oder Ollama ist zu alt. Bitte ollama pull tev1 ausführen und Ollama aktualisieren.'
            else:
                message = 'Ollama konnte die Anfrage nicht verarbeiten. ' + str(detail)[:300]
            self.send_json(502, {'error': message})
        except (OSError, TimeoutError):
            self.send_json(503, {'error': 'Ollama ist nicht erreichbar oder das Modell benötigt zu lange. Bitte Ollama starten und erneut versuchen.'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--model', choices=('tev1', 'tev1:0.8b'), default='tev1')
    args = parser.parse_args()
    MODEL = args.model
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Tev1 Demo: http://127.0.0.1:{args.port}', flush=True)
    server.serve_forever()
