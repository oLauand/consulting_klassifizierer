"""Local demonstration server. Python standard library only.

Beim Start richtet der Server im Hintergrund alles Noetige ein (Ollama
installieren, Modell herunterladen, Modell laden) und meldet den Fortschritt
unter /api/setup. Die Oberflaeche zeigt diesen Fortschritt als Ladebalken an.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import BoundedSemaphore

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / '.runtime'
OLLAMA = 'http://127.0.0.1:11434'
MODEL = 'tev1'
MIN_OLLAMA = (0, 35, 0)
OLLAMA_SETUP_URL = 'https://ollama.com/download/OllamaSetup.exe'
REGISTRY = 'https://registry.ollama.ai'
APP_VERSION = 4
BUSY = BoundedSemaphore(1)
IS_WINDOWS = os.name == 'nt'
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW


def log(message):
    line = time.strftime('%Y-%m-%d %H:%M:%S ') + str(message)
    print(line, flush=True)
    try:
        RUNTIME.mkdir(exist_ok=True)
        with open(RUNTIME / 'setup.log', 'a', encoding='utf-8') as handle:
            handle.write(line + '\n')
    except OSError:
        pass


def ollama(path, body=None, timeout=5):
    data = None if body is None else json.dumps(body).encode('utf-8')
    req = urllib.request.Request(OLLAMA + path, data=data, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def model_tag(model):
    return model if ':' in model else model + ':latest'


def parse_version(text):
    match = re.match(r'\s*v?(\d+)\.(\d+)\.(\d+)', str(text or ''))
    return tuple(int(x) for x in match.groups()) if match else None


def ollama_version():
    try:
        return parse_version(ollama('/api/version', timeout=3).get('version'))
    except (OSError, ValueError):
        return None


def find_ollama():
    found = shutil.which('ollama')
    if found:
        return found
    candidates = []
    if os.environ.get('LOCALAPPDATA'):
        candidates.append(Path(os.environ['LOCALAPPDATA']) / 'Programs' / 'Ollama' / 'ollama.exe')
    for var in ('ProgramFiles', 'ProgramW6432'):
        if os.environ.get(var):
            candidates.append(Path(os.environ[var]) / 'Ollama' / 'ollama.exe')
    return next((str(c) for c in candidates if c.is_file()), None)


class Setup:
    """Fuehrt die Einrichtung in einem Hintergrund-Thread aus und merkt sich den Stand."""

    STEPS = [
        ('ollama', 'KI-Laufzeit (Ollama) bereitstellen'),
        ('model', 'Modell herunterladen'),
        ('load', 'Modell laden'),
    ]

    def __init__(self):
        self.lock = threading.Lock()
        self.thread = None
        self.state = {'phase': 'starting', 'step': 'ollama', 'ready': False, 'error': None,
                      'title': 'Einrichtung startet …', 'detail': '', 'progress': None,
                      'done_bytes': 0, 'total_bytes': 0, 'speed': 0, 'eta': None,
                      'started': time.time(), 'model': MODEL,
                      'steps': [{'id': i, 'label': l, 'status': 'pending'} for i, l in self.STEPS]}
        self._speed = None
        self._last = None

    # ---------- Zustand ----------
    def snapshot(self):
        with self.lock:
            data = json.loads(json.dumps(self.state))
        data['elapsed'] = round(time.time() - data['started'], 1)
        return data

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def step(self, step_id, title, detail=''):
        with self.lock:
            for item in self.state['steps']:
                if item['id'] == step_id:
                    item['status'] = 'active'
            self.state.update(step=step_id, phase=step_id, title=title, detail=detail, progress=None,
                              done_bytes=0, total_bytes=0, speed=0, eta=None)
        self._speed = None
        self._last = None
        log(f'[{step_id}] {title} {detail}'.strip())

    def finish_step(self, step_id, label=None):
        with self.lock:
            for item in self.state['steps']:
                if item['id'] == step_id:
                    item['status'] = 'done'
                    if label:
                        item['label'] = label

    def progress(self, done, total):
        """Fortschritt in Bytes melden; berechnet Geschwindigkeit und Restzeit."""
        now = time.time()
        if self._last is None:
            self._last = (now, done)
        else:
            dt = now - self._last[0]
            if dt >= 1.0:
                rate = max(0.0, (done - self._last[1]) / dt)
                self._speed = rate if self._speed is None else 0.75 * self._speed + 0.25 * rate
                self._last = (now, done)
        speed = self._speed or 0
        eta = (total - done) / speed if speed > 1024 and total > done else None
        self.update(done_bytes=done, total_bytes=total, progress=(done / total if total else None),
                    speed=round(speed), eta=None if eta is None else round(eta))

    # ---------- Ablauf ----------
    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.state.update(error=None, ready=False, started=time.time())
            for item in self.state['steps']:
                if item['status'] != 'done':
                    item['status'] = 'pending'
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    def run(self):
        try:
            self.ensure_ollama()
            self.ensure_model()
            self.load_model()
            self.update(phase='ready', ready=True, title='Bereit', detail='', progress=1)
            log('Einrichtung abgeschlossen.')
        except Exception as error:  # noqa: BLE001 - Fehler werden in der Oberflaeche angezeigt
            message = str(error) or error.__class__.__name__
            log(f'FEHLER: {message!r}')
            with self.lock:
                for item in self.state['steps']:
                    if item['status'] == 'active':
                        item['status'] = 'error'
                self.state.update(phase='error', error=message, title='Einrichtung unterbrochen')

    # ---------- Schritt 1: Ollama ----------
    def ensure_ollama(self):
        self.step('ollama', 'KI-Laufzeit wird geprüft …')
        version = ollama_version()
        exe = find_ollama()
        log(f'Ollama-Dienst: {version}, Programm: {exe}')
        if version is None and exe:
            self.start_ollama(exe)
            version = ollama_version()
        if version is None or version < MIN_OLLAMA:
            if not IS_WINDOWS:
                raise RuntimeError('Ollama fehlt oder ist zu alt. Bitte Ollama ab Version 0.35 installieren: https://ollama.com/download')
            self.install_ollama(update=version is not None)
            exe = find_ollama()
            if not exe:
                raise RuntimeError('Ollama wurde installiert, aber nicht gefunden. Bitte den Rechner neu starten und die Demo erneut öffnen.')
            if ollama_version() is None:
                self.start_ollama(exe)
            version = ollama_version()
            if version is None:
                raise RuntimeError('Ollama startet nicht. Details stehen in .runtime\\ollama-error.log.')
            if version < MIN_OLLAMA:
                raise RuntimeError('Es läuft noch eine alte Ollama-Version. Bitte Ollama unten rechts in der Taskleiste beenden (Rechtsklick > Quit) und hier „Erneut versuchen“ klicken.')
        self.finish_step('ollama', 'KI-Laufzeit bereit (Ollama ' + '.'.join(map(str, version)) + ')')

    def start_ollama(self, exe):
        self.update(title='KI-Laufzeit wird gestartet …', detail='', progress=None)
        log(f'Starte {exe} serve')
        RUNTIME.mkdir(exist_ok=True)
        out = open(RUNTIME / 'ollama-output.log', 'ab')
        err = open(RUNTIME / 'ollama-error.log', 'ab')
        subprocess.Popen([exe, 'serve'], stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                         creationflags=NO_WINDOW | (0x200 if IS_WINDOWS else 0), close_fds=True)
        deadline = time.time() + 60
        while time.time() < deadline:
            if ollama_version():
                return
            time.sleep(0.5)

    def install_ollama(self, update):
        verb = 'aktualisiert' if update else 'installiert'
        self.step('ollama', 'KI-Laufzeit (Ollama) wird heruntergeladen …',
                  f'Ollama wird einmalig {verb}. Das ist der größte Download neben dem Modell.')
        RUNTIME.mkdir(exist_ok=True)
        target = RUNTIME / 'OllamaSetup.exe'
        download_file(OLLAMA_SETUP_URL, target, self.progress)

        self.update(title='Download wird geprüft …', detail='Digitale Signatur von Ollama Inc. wird kontrolliert.', progress=None, eta=None, speed=0)
        check = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
             '$s = Get-AuthenticodeSignature -LiteralPath $env:TEV1_SETUP; '
             'if ($s.Status -eq "Valid" -and $s.SignerCertificate.Subject -match "O=Ollama") { "OK" } else { "BAD " + $s.Status + " " + $s.SignerCertificate.Subject }'],
            capture_output=True, text=True, timeout=120, creationflags=NO_WINDOW,
            env=dict(os.environ, TEV1_SETUP=str(target)))
        log(f'Signaturpruefung: {check.stdout.strip()} {check.stderr.strip()[:300]}')
        if not check.stdout.strip().startswith('OK'):
            target.unlink(missing_ok=True)
            raise RuntimeError('Der Ollama-Download ist unvollständig oder nicht korrekt signiert. Bitte „Erneut versuchen“ klicken.')

        self.update(title=f'Ollama wird {verb} …', detail='Das dauert meist 1–3 Minuten. Es sind keine Administratorrechte nötig.', progress=None)
        result = subprocess.run([str(target), '/VERYSILENT', '/NORESTART', '/SUPPRESSMSGBOXES', '/SP-'],
                                timeout=1800, creationflags=NO_WINDOW)
        log(f'Installer-Exitcode: {result.returncode}')
        if result.returncode != 0:
            raise RuntimeError(f'Die Ollama-Installation ist fehlgeschlagen (Code {result.returncode}). Bitte „Erneut versuchen“ klicken oder Ollama von https://ollama.com/download installieren.')
        target.unlink(missing_ok=True)
        # Die Ollama-App startet nach der Installation meist selbst; kurz darauf warten.
        self.update(title='KI-Laufzeit wird gestartet …', detail='', progress=None)
        deadline = time.time() + 20
        while time.time() < deadline and not ollama_version():
            time.sleep(1)

    # ---------- Schritt 2: Modell ----------
    def ensure_model(self):
        self.step('model', 'Modell wird geprüft …')
        tags = ollama('/api/tags', timeout=10).get('models', [])
        if any(m.get('name') == model_tag(MODEL) for m in tags):
            self.finish_step('model', f'Modell {MODEL} vorhanden')
            return
        expected = registry_size(MODEL)
        log(f'Erwartete Modellgroesse: {expected}')
        self.update(title=f'Modell {MODEL} wird heruntergeladen …',
                    detail='Einmaliger Download. Danach funktioniert die Demo auch ohne Internet.')
        for attempt in range(1, 6):
            try:
                self.pull(expected)
                break
            except (OSError, TimeoutError, urllib.error.URLError) as error:
                log(f'Pull-Versuch {attempt} abgebrochen: {error!r}')
                if attempt == 5:
                    raise RuntimeError('Der Modell-Download ist mehrfach abgebrochen. Bitte Internetverbindung und freien Speicher prüfen und „Erneut versuchen“ klicken. Bereits geladene Teile bleiben erhalten.')
                self.update(detail=f'Verbindung unterbrochen – Download wird fortgesetzt (Versuch {attempt + 1}) …')
                time.sleep(3)
        self.finish_step('model', f'Modell {MODEL} heruntergeladen')

    def pull(self, expected):
        req = urllib.request.Request(OLLAMA + '/api/pull', data=json.dumps({'model': MODEL, 'stream': True}).encode(),
                                     headers={'Content-Type': 'application/json'})
        layers = {}
        with urllib.request.urlopen(req, timeout=180) as response:
            for raw in response:
                if not raw.strip():
                    continue
                msg = json.loads(raw)
                if msg.get('error'):
                    raise RuntimeError('Ollama meldet beim Download: ' + str(msg['error'])[:300])
                status = msg.get('status', '')
                if msg.get('digest') and msg.get('total'):
                    layers[msg['digest']] = (msg.get('completed', 0) or 0, msg['total'])
                    done = sum(c for c, _ in layers.values())
                    total = max(expected or 0, sum(t for _, t in layers.values()))
                    self.progress(min(done, total), total)
                elif status in ('verifying sha256 digest', 'writing manifest'):
                    self.update(detail='Download wird geprüft und abgeschlossen …')
                if status == 'success':
                    return
        raise OSError('Download-Stream endete ohne Abschluss.')

    # ---------- Schritt 3: Laden ----------
    def load_model(self):
        self.step('load', 'Modell wird in den Arbeitsspeicher geladen …',
                  'Auf Laptops ohne starke Grafikkarte kann das 1–3 Minuten dauern.')
        with BUSY:
            run_model('Hello World', 'Does this text contain a greeting?',
                      {'yes': 'A greeting is present.', 'no': 'No greeting is present.'}, timeout=600)
        self.finish_step('load', 'Modell geladen')


def download_file(url, target, report, workers=6, chunk=16 * 1024 * 1024):
    """Laedt eine grosse Datei in mehreren parallelen Teilen (mit Fortsetzen).

    Jeder Teil landet in einer eigenen Datei und wird erst am Ende zusammengefuegt.
    (Eine grosse Datei vorab anzulegen ist unter Windows mit Virenscanner sehr langsam.)
    """
    head = urllib.request.Request(url, headers={'Range': 'bytes=0-0', 'User-Agent': 'tev1-demo'})
    with urllib.request.urlopen(head, timeout=30) as response:
        content_range = response.headers.get('Content-Range', '')
        etag = response.headers.get('ETag', '')
        total = int(content_range.split('/')[-1]) if '/' in content_range else int(response.headers.get('Content-Length', 0))
        ranged = response.status == 206
    log(f'Download {url}: {total} Bytes, Teil-Downloads: {ranged}')
    target.with_suffix('.part').unlink(missing_ok=True)  # Altlast frueherer Version
    if target.exists() and target.stat().st_size == total:
        report(total, total)
        return
    if not ranged or total < chunk * 2:
        return download_simple(url, target, report)

    parts = target.parent / (target.stem + '-teile')
    parts.mkdir(exist_ok=True)
    info = parts / 'info.json'
    try:
        if json.loads(info.read_text()) != {'total': total, 'etag': etag, 'chunk': chunk}:
            raise ValueError
    except (OSError, ValueError):
        for old in parts.glob('*.bin'):
            old.unlink()
        info.write_text(json.dumps({'total': total, 'etag': etag, 'chunk': chunk}))
    count = (total + chunk - 1) // chunk
    sizes = [min(chunk, total - i * chunk) for i in range(count)]
    files = [parts / f'{i:05d}.bin' for i in range(count)]
    finished = [f.exists() and f.stat().st_size == sizes[i] for i, f in enumerate(files)]
    lock = threading.Lock()
    progress = {'bytes': sum(sizes[i] for i in range(count) if finished[i])}
    todo = [i for i in range(count) if not finished[i]]
    errors = []

    def worker():
        while not errors:
            with lock:
                if not todo:
                    return
                index = todo.pop(0)
            start = index * chunk
            end = start + sizes[index] - 1
            temp = files[index].with_suffix('.tmp')
            for attempt in range(4):
                got = 0
                try:
                    req = urllib.request.Request(url, headers={'Range': f'bytes={start}-{end}', 'User-Agent': 'tev1-demo'})
                    with urllib.request.urlopen(req, timeout=60) as response, open(temp, 'wb') as handle:
                        if response.status != 206:
                            raise OSError('Server unterstuetzt keine Teil-Downloads.')
                        while True:
                            block = response.read(256 * 1024)
                            if not block:
                                break
                            handle.write(block)
                            got += len(block)
                            with lock:
                                progress['bytes'] += len(block)
                    if got != sizes[index]:
                        raise OSError(f'Teil {index} unvollstaendig ({got}/{sizes[index]})')
                    os.replace(temp, files[index])
                    break
                except (OSError, urllib.error.URLError) as error:
                    with lock:
                        progress['bytes'] -= got
                    log(f'Teil {index}, Versuch {attempt + 1}: {error!r}')
                    if attempt == 3:
                        errors.append(error)
                        return
                    time.sleep(2 + attempt * 3)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(workers)]
    for thread in threads:
        thread.start()
    report(progress['bytes'], total)
    while any(t.is_alive() for t in threads):
        with lock:
            current = progress['bytes']
        report(current, total)
        time.sleep(0.5)
    if errors:
        raise RuntimeError('Download von Ollama abgebrochen. Bitte Internetverbindung pruefen und „Erneut versuchen“ klicken. Der Download wird dann fortgesetzt.')
    report(total, total)
    joined = target.with_suffix('.join')
    with open(joined, 'wb') as out:
        for part in files:
            with open(part, 'rb') as handle:
                shutil.copyfileobj(handle, out, 8 * 1024 * 1024)
    if joined.stat().st_size != total:
        joined.unlink(missing_ok=True)
        raise RuntimeError('Der Ollama-Download ist unvollstaendig. Bitte „Erneut versuchen“ klicken.')
    os.replace(joined, target)
    shutil.rmtree(parts, ignore_errors=True)


def download_simple(url, target, report):
    part = target.with_suffix('.part')
    req = urllib.request.Request(url, headers={'User-Agent': 'tev1-demo'})
    with urllib.request.urlopen(req, timeout=60) as response, open(part, 'wb') as handle:
        total = int(response.headers.get('Content-Length', 0))
        done = 0
        last = 0
        while True:
            block = response.read(256 * 1024)
            if not block:
                break
            handle.write(block)
            done += len(block)
            if time.time() - last > 0.5:
                report(done, total or done)
                last = time.time()
    os.replace(part, target)
    report(done, done)


def registry_size(model):
    """Gesamtgroesse des Modells laut Ollama-Registry (fuer genaue Prozentangaben)."""
    name, _, tag = model.partition(':')
    url = f'{REGISTRY}/v2/library/{name}/manifests/{tag or "latest"}'
    try:
        req = urllib.request.Request(url, headers={'Accept': 'application/vnd.docker.distribution.manifest.v2+json'})
        with urllib.request.urlopen(req, timeout=15) as response:
            manifest = json.load(response)
        return sum(int(x.get('size', 0)) for x in manifest.get('layers', [])) + int(manifest.get('config', {}).get('size', 0))
    except (OSError, ValueError):
        return None


def run_model(state, question, options, timeout):
    return ollama('/v1/systemone', {
        'model': MODEL,
        'state': state.strip(),
        'questions': {'classification': {'type': 'choice', 'instructions': question.strip(), 'criteria': options}},
        'keep_alive': '30m',
    }, timeout=timeout)


SETUP = Setup()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if '/api/setup' not in (args[0] if args else ''):
            super().log_message(fmt, *args)

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
            return self.send_json(200, {'app': 'tev1-consulting-demo', 'version': APP_VERSION, 'directory': str(ROOT), 'model': MODEL})
        if self.path == '/api/setup':
            return self.send_json(200, SETUP.snapshot())
        if self.path == '/api/status':
            try:
                version = ollama('/api/version').get('version', '')
                tags = ollama('/api/tags').get('models', [])
                target = model_tag(MODEL)
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
        if self.path not in ('/api/classify', '/api/warmup', '/api/setup/retry'):
            return self.send_json(404, {'error': 'Schnittstelle nicht gefunden.'})
        # Only the local page may issue browser POSTs. No CORS exception needed.
        origin = self.headers.get('Origin')
        if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'):
            return self.send_json(403, {'error': 'Bitte die lokale Demo-Seite verwenden.'})
        if self.path == '/api/setup/retry':
            SETUP.start()
            return self.send_json(200, SETUP.snapshot())
        if not SETUP.snapshot()['ready']:
            return self.send_json(503, {'error': 'Die Demo wird noch eingerichtet. Bitte warten, bis der Ladebalken fertig ist.'})
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
                result = run_model(state, question, options, timeout=600 if self.path == '/api/warmup' else 180)
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
                message = 'Tev1 fehlt oder Ollama ist zu alt. Bitte die Demo neu starten, sie richtet beides automatisch ein.'
            else:
                message = 'Ollama konnte die Anfrage nicht verarbeiten. ' + str(detail)[:300]
            self.send_json(502, {'error': message})
        except (OSError, TimeoutError):
            self.send_json(503, {'error': 'Ollama ist nicht erreichbar oder das Modell benötigt zu lange. Bitte die Demo neu starten.'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--model', choices=('tev1', 'tev1:0.8b'), default='tev1')
    args = parser.parse_args()
    MODEL = args.model
    SETUP.state['model'] = MODEL
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Tev1 Demo: http://127.0.0.1:{args.port}', flush=True)
    log(f'Demo-Server v{APP_VERSION} gestartet (Port {args.port}, Modell {MODEL}, Python {sys.version.split()[0]})')
    SETUP.start()
    server.serve_forever()
