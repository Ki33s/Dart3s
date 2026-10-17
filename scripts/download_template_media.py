"""Fetch exact gallery dependencies with PyPI SHA256 verification."""
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

root = Path(__file__).resolve().parents[1]
packages = {'comfyui-workflow-templates-media-image': '0.3.160',
            'comfyui-workflow-templates-media-other': '0.3.229',
            'comfyui-workflow-templates-media-video': '0.3.101'}
for name, version in packages.items():
    with urllib.request.urlopen(f'https://pypi.org/pypi/{name}/{version}/json', timeout=120) as response:
        metadata = json.load(response)
    wheel = next(item for item in metadata['urls'] if item['packagetype'] == 'bdist_wheel')
    dest = root / 'downloads/template-media' / wheel['filename']
    subprocess.run([sys.executable, str(root / 'scripts/download_ranges.py'),
                    wheel['url'], str(dest), '--size', str(wheel['size']),
                    '--sha256', wheel['digests']['sha256'], '--workers', '20'], check=True)
