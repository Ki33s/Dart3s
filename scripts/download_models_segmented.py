"""Download official Comfy-Org mirror assets in verified resumable ranges."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CHUNK = 8 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def metadata(url):
    try:
        response = urllib.request.build_opener(NoRedirect).open(
            urllib.request.Request(url, method='HEAD'), timeout=60)
        headers = response.headers
    except urllib.error.HTTPError as exc:
        if exc.code not in (301, 302, 303, 307, 308):
            raise
        headers = exc.headers
    return int(headers['x-linked-size']), headers['x-linked-etag'].strip('"')


def download(model):
    size, digest = metadata(model['url'])
    if len(digest) != 64:
        raise ValueError('Expected an LFS SHA256')
    dest = ROOT / 'models' / model['directory'] / model['name']
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        with dest.open('rb') as stream:
            if dest.stat().st_size == size and hashlib.file_digest(stream, 'sha256').hexdigest() == digest:
                print(f'OK existing {dest.name}', flush=True)
                return dict(model, size=size, sha256=digest)
        raise RuntimeError(f'Existing file failed verification: {dest}')
    repo, filename = model['url'].split('huggingface.co/')[1].split('/resolve/main/')
    url = f'https://modelscope.cn/models/{repo}/resolve/master/{filename}'
    segments = ROOT / 'downloads' / 'segments' / digest
    segments.mkdir(parents=True, exist_ok=True)

    def part(index):
        start = index * CHUNK
        end = min(size, start + CHUNK) - 1
        length = end - start + 1
        target = segments / f'{index:06}.bin'
        if target.exists() and target.stat().st_size == length:
            return length
        last = None
        for attempt in range(6):
            try:
                req = urllib.request.Request(url, headers={'Range': f'bytes={start}-{end}'})
                with urllib.request.urlopen(req, timeout=120) as response:
                    if response.status != 206 or response.headers.get('Content-Range') != f'bytes {start}-{end}/{size}':
                        raise RuntimeError(f'Invalid range response: {response.status}, {response.headers.get("Content-Range")}')
                    tmp = target.with_suffix('.tmp')
                    with tmp.open('wb') as output:
                        while block := response.read(1024 * 1024):
                            output.write(block)
                if tmp.stat().st_size != length:
                    raise RuntimeError('Truncated range')
                tmp.replace(target)
                return length
            except Exception as exc:
                last = exc
                time.sleep(min(10, attempt + 1))
        raise RuntimeError(f'{dest.name} range {index}: {last}')

    count = (size + CHUNK - 1) // CHUNK
    completed = 0
    started = time.monotonic()
    print(f'Downloading {dest.name} ({size / 1e9:.2f} GB), {count} ranges', flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
        futures = [pool.submit(part, i) for i in range(count)]
        for future in concurrent.futures.as_completed(futures):
            completed += future.result()
            progress = {'file': dest.name, 'downloaded': completed, 'total': size,
                        'seconds': round(time.monotonic() - started)}
            (ROOT / 'logs/models.progress.json').write_text(json.dumps(progress))
            if completed == size or completed // (100 * 1024 * 1024) != (completed - CHUNK) // (100 * 1024 * 1024):
                print(json.dumps(progress), flush=True)
    partial = dest.with_suffix('.assembling')
    hasher = hashlib.sha256()
    with partial.open('wb') as output:
        for i in range(count):
            with (segments / f'{i:06}.bin').open('rb') as stream:
                while data := stream.read(8 * 1024 * 1024):
                    hasher.update(data)
                    output.write(data)
    if partial.stat().st_size != size or hasher.hexdigest() != digest:
        raise RuntimeError(f'SHA256 mismatch for {dest.name}; retained ranges for diagnosis')
    partial.replace(dest)
    print(f'OK SHA256 {dest.name}', flush=True)
    return dict(model, size=size, sha256=digest)


if __name__ == '__main__':
    models = json.loads((ROOT / 'downloads/models.manifest.json').read_text(encoding='utf-8-sig'))
    results = []
    for model in models:
        results.append(download(model))
        (ROOT / 'downloads/models.verified.json').write_text(json.dumps(results, indent=2))
