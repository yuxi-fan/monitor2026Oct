"""Durable GitHub branch state, or atomic local state for CLI use."""
import base64
import json
import os
from pathlib import Path

import requests


def validate(data):
    if not isinstance(data, dict):
        raise ValueError('Invalid stock state')
    for key, stores in data.items():
        if not isinstance(key, str) or not isinstance(stores, dict):
            raise ValueError('Invalid stock state')
        if any(not isinstance(k, str) or v not in ('available', 'unavailable') for k, v in stores.items()):
            raise ValueError('Invalid stock state')
    return data


class LocalState:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        return validate(json.loads(self.path.read_text(encoding='utf-8'))) if self.path.exists() else {}

    def save(self, data):
        validate(data)
        temporary = self.path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
        os.replace(temporary, self.path)


class GitHubState:
    def __init__(self, repository, token, branch='monitor-state', session=None):
        if not token:
            raise ValueError('Missing STATE_TOKEN')
        self.base = f'https://api.github.com/repos/{repository}'
        self.branch = branch
        self.session = session or requests.Session()
        self.headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                        'X-GitHub-Api-Version': '2022-11-28'}
        self.sha = None

    def request(self, method, path, **kwargs):
        # Writes have no automatic retry: ambiguous success must fail visibly.
        return self.session.request(method, self.base + path, headers=self.headers,
                                    timeout=(10, 30), **kwargs)

    def load(self):
        # Distinguish a missing file on a valid branch from a bad token/repo/branch.
        branch = self.request('GET', f'/branches/{self.branch}')
        branch.raise_for_status()
        response = self.request('GET', '/contents/stock-state.json', params={'ref': self.branch})
        if response.status_code == 404:
            self.sha = None
            return {}
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get('encoding') != 'base64' or not data.get('sha'):
            raise ValueError('Invalid GitHub state response')
        self.sha = data['sha']
        return validate(json.loads(base64.b64decode(data['content']).decode('utf-8')))

    def save(self, data):
        validate(data)
        payload = {'message': 'Update pickup stock state', 'branch': self.branch,
                   'content': base64.b64encode(json.dumps(data, sort_keys=True, indent=2).encode()).decode()}
        if self.sha:
            payload['sha'] = self.sha
        response = self.request('PUT', '/contents/stock-state.json', json=payload)
        response.raise_for_status()  # SHA comparison rejects conflicting writes (409).
