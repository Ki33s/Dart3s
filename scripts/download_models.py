"""Download official template assets, resumably, and verify HF SHA256."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def download(model):
    dest = ROOT / 'models' / model['directory'] / model['name']
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = model['url']
    request = urllib.request.Request(url, method='HEAD')
    # HF exposes the LFS content hash before redirecting to its storage.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        response = opener.open(request, timeout=60)
        headers = response.headers
    except urllib.error.HTTPError as exc:
        if exc.code not in (301, 302, 303, 307, 308):
            raise
        headers = exc.headers
    expected_hash = headers.get('x-linked-etag', '').strip('"')
    expected_size = int(headers.get('x-linked-size', '0'))
    partial = dest.with_suffix(dest.suffix + '.part')
    log = ROOT / 'logs' / (model['name'] + '.download.log')
    if not dest.exists():
        with log.open('w', encoding='utf-8') as out:
            subprocess.run(['curl.exe', '-L', '--fail', '--retry', '5',
                            '--continue-at', '-', '--output', str(partial), url],
                           stdout=out, stderr=out, check=True)
        if expected_size and partial.stat().st_size != expected_size:
            raise RuntimeError(f'Size mismatch: {dest.name}')
        if len(expected_hash) == 64:
            digest = hashlib.file_digest(partial.open('rb'), 'sha256').hexdigest()
            if digest != expected_hash:
                raise RuntimeError(f'SHA256 mismatch: {dest.name}')
        partial.replace(dest)
    else:
        if expected_size and dest.stat().st_size != expected_size:
            raise RuntimeError(f'Size mismatch: {dest.name}')
        if len(expected_hash) == 64:
            with dest.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected_hash:
                    raise RuntimeError(f'SHA256 mismatch: {dest.name}')
    print(f'OK {dest.name}: {dest.stat().st_size} bytes', flush=True)
    return dict(model, size=dest.stat().st_size, sha256=expected_hash)


if __name__ == '__main__':
    models = json.loads((ROOT / 'downloads/models.manifest.json').read_text(encoding='utf-8-sig'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(download, models))
    (ROOT / 'downloads/models.verified.json').write_text(json.dumps(results, indent=2))
