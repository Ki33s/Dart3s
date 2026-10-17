"""Persistent image order; the UI dispatches exactly one existing Jobs worker."""
from pathlib import Path
from uuid import uuid4

from .gui_jobs import read_json, write_json
from .risk_version import RISK_VERSION


class ImageQueue:
    def __init__(self, path):
        self.path = Path(path)
        self.items = []
        for item in read_json(self.path).get('items', []):
            if not isinstance(item, dict) or not isinstance(item.get('image'), dict):
                continue
            if not item.get('id') or not item['image'].get('path'):
                continue
            if item.get('status') == 'running':
                item['status'] = 'paused'
            if item.get('risk', {}).get('version') != RISK_VERSION:
                item.pop('risk', None)
            self.items.append(item)

    def save(self):
        write_json(self.path, {'version': 1, 'items': self.items})

    def add(self, image):
        item = {'id': uuid4().hex, 'image': dict(image), 'status': 'waiting'}
        self.items.append(item)
        self.save()
        return item

    def next(self):
        return next((item for item in self.items if item['status'] == 'waiting'), None)

    def get(self, item_id):
        return next((item for item in self.items if item['id'] == item_id), None)

    def mark(self, item_id, status, **fields):
        item = self.get(item_id)
        if item:
            item.update(status=status, **fields)
            self.save()

    def remove(self, item_id):
        self.items = [item for item in self.items
                      if item['id'] != item_id or item['status'] in ('running', 'paused')]
        self.save()

    def move(self, item_id, offset):
        item = self.get(item_id)
        if not item or item['status'] != 'waiting':
            return
        index = self.items.index(item)
        target = index + offset
        if 0 <= target < len(self.items) and self.items[target]['status'] == 'waiting':
            self.items[index], self.items[target] = self.items[target], self.items[index]
            self.save()
