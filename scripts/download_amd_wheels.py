"""Fetch pinned Windows gfx1032 nightly wheels from AMD's official index."""
import hashlib
import html.parser
import json
from pathlib import Path
import subprocess
import sys
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
INDEX = 'https://nightly.repo.amd.com/rocm/whl-next/'
ROCM = '10.2.0a20261013'
PACKAGES = {
    'rocm-sdk-core': ROCM,
    'rocm-sdk-libraries': ROCM,
    'rocm-sdk-device-gfx1032': ROCM,
    'torch': '2.13.0+rocm' + ROCM,
    'amd-torch-device-gfx1032': '2.13.0+rocm' + ROCM,
    'torchvision': '0.28.0+rocm' + ROCM,
    'amd-torchvision-device-gfx1032': '0.28.0+rocm' + ROCM,
    'torchaudio': '2.11.0.3+rocm' + ROCM,
}


class Links(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.extend(v for k, v in attrs if k == 'href')


verified = []
for package, version in PACKAGES.items():
    with urllib.request.urlopen(INDEX + package + '/', timeout=120) as response:
        page_url = response.geturl()
        page = response.read().decode('utf-8')
    links = Links()
    links.feed(page)
    prefix = package.replace('-', '_') + '-' + version + '-'
    matches = [href for href in links.links
               if urllib.parse.unquote(href.split('/')[-1]).startswith(prefix)
               and ('cp312-cp312-win_amd64.whl' in href or 'py3-none-win_amd64.whl' in href)]
    if len(matches) != 1:
        raise RuntimeError(f'Expected one pinned Windows wheel for {package}, found {len(matches)}')
    url = urllib.parse.urljoin(page_url, matches[0])
    filename = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit('/', 1)[-1])
    dest = ROOT / 'downloads/amd-wheels' / filename
    with urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=120) as response:
        size = int(response.headers['Content-Length'])
        etag = response.headers.get('ETag')
    args = [sys.executable, str(ROOT / 'scripts/download_ranges.py'), url, str(dest),
            '--size', str(size), '--workers', '32']
    subprocess.run(args, check=True)
    with dest.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    verified.append({'package': package, 'version': version, 'url': url, 'size': size,
                     'sha256': digest, 'server_etag': etag})
    (ROOT / 'downloads/amd-wheels.manifest.json').write_text(json.dumps(verified, indent=2))
