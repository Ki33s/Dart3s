"""Resume a known release asset with concurrent ranges and a pinned SHA256."""
import argparse
import concurrent.futures
import hashlib
from pathlib import Path
import time
import urllib.request
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument('url')
parser.add_argument('destination', type=Path)
parser.add_argument('--size', required=True, type=int)
parser.add_argument('--sha256')
parser.add_argument('--workers', type=int, default=12)
args = parser.parse_args()
dest = args.destination
dest.parent.mkdir(parents=True, exist_ok=True)
prefix = dest.stat().st_size if dest.exists() else 0
if prefix > args.size:
    raise RuntimeError('Destination is larger than release asset')
chunk = 8 * 1024 * 1024
parts = dest.parent / f'{dest.name}.ranges-{prefix}'
parts.mkdir(parents=True, exist_ok=True)
count = (args.size - prefix + chunk - 1) // chunk


def download(index):
    start = prefix + index * chunk
    end = min(args.size, start + chunk) - 1
    length = end - start + 1
    target = parts / f'{index:06}.bin'
    if target.exists() and target.stat().st_size == length:
        return length
    error = None
    for attempt in range(6):
        try:
            req = urllib.request.Request(args.url, headers={'Range': f'bytes={start}-{end}'})
            with urllib.request.urlopen(req, timeout=120) as response:
                if response.status != 206 or response.headers.get('Content-Range') != f'bytes {start}-{end}/{args.size}':
                    raise RuntimeError(f'Unexpected range: {response.status}, {response.headers.get("Content-Range")}')
                tmp = target.with_suffix('.tmp')
                with tmp.open('wb') as output:
                    while data := response.read(1024 * 1024):
                        output.write(data)
            if tmp.stat().st_size != length:
                raise RuntimeError('Truncated range')
            tmp.replace(target)
            return length
        except Exception as exc:
            error = exc
            time.sleep(min(attempt + 1, 10))
    raise RuntimeError(f'Range {index} failed: {error}')


done = prefix
with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
    for future in concurrent.futures.as_completed([pool.submit(download, i) for i in range(count)]):
        done += future.result()
        print(f'{dest.name}: {done}/{args.size}', flush=True)
with dest.open('ab') as output:
    for index in range(count):
        with (parts / f'{index:06}.bin').open('rb') as stream:
            while data := stream.read(8 * 1024 * 1024):
                output.write(data)
with dest.open('rb') as stream:
    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
if dest.stat().st_size != args.size or (args.sha256 and digest != args.sha256):
    raise RuntimeError(f'Release integrity failed: {digest}')
if not args.sha256:
    if dest.suffix != '.whl':
        raise RuntimeError('A pinned SHA256 is required for non-wheel downloads')
    with zipfile.ZipFile(dest) as wheel:
        bad = wheel.testzip()
    if bad:
        raise RuntimeError(f'Wheel CRC failed: {bad}')
    print(f'OK wheel CRC, calculated SHA256={digest} {dest.name}', flush=True)
else:
    print(f'OK SHA256 {dest.name}', flush=True)
