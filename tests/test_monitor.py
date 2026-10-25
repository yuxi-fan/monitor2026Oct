import base64
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
import monitor
from state_store import GitHubState, LocalState

PART = monitor.DEFAULT_PART
KEY = PART + ':92602'


def store(number='R1', name='Irvine Spectrum Center', status='unavailable'):
    return {'storeNumber': number, 'storeName': name,
            'partsAvailability': {PART: {'pickupDisplay': status, 'pickupSearchQuote': 'Today'}}}


def response(data=None, code=200):
    result = Mock(status_code=code)
    result.json.return_value = data
    if code >= 400:
        result.raise_for_status.side_effect = requests.HTTPError('HTTP failure')
    return result


class MemoryState:
    def __init__(self, data=None):
        self.data = data or {}
        self.save = Mock(side_effect=self._save)

    def load(self):
        return copy.deepcopy(self.data)

    def _save(self, data):
        self.data = copy.deepcopy(data)


class MonitorTests(unittest.TestCase):
    def session(self, stores):
        session = Mock()
        session.get.return_value = response({'body': {'stores': stores}})
        return session

    def run_check(self, stores, state=None, dry=False):
        state = state or MemoryState()
        session = self.session(stores)
        with patch('monitor.notify') as notify:
            monitor.run_once(PART, '92602', dry, session=session, state=state)
        return state, notify, session

    def test_available_first_run_alerts_and_uses_verified_api(self):
        state, notify, session = self.run_check([store(status='available')])
        notify.assert_called_once()
        self.assertEqual(state.data, {KEY: {'R1': 'available'}})
        args, kwargs = session.get.call_args
        self.assertEqual(args[0], monitor.API)
        self.assertEqual(kwargs['params'], {'pl': 'true', 'parts.0': PART, 'location': '92602'})
        self.assertEqual(kwargs['timeout'], (10, 30))

    def test_unavailable_never_emails(self):
        state, notify, _ = self.run_check([store()])
        notify.assert_not_called()
        self.assertEqual(state.data[KEY]['R1'], 'unavailable')

    def test_deduplication_and_restock(self):
        state = MemoryState()
        for status, alerts in [('available', 1), ('available', 0), ('unavailable', 0), ('available', 1)]:
            _, notify, _ = self.run_check([store(status=status)], state)
            self.assertEqual(notify.call_count, alerts)

    def test_unknown_and_absent_stores_preserve_previous(self):
        state = MemoryState({KEY: {'R1': 'available', 'R2': 'available', 'R3': 'unavailable'}})
        _, notify, _ = self.run_check([store(status='unexpected'), store('R3')], state)
        self.assertEqual(state.data[KEY]['R1'], 'available')
        self.assertEqual(state.data[KEY]['R2'], 'available')
        notify.assert_not_called()
        state.save.assert_not_called()

    def test_partial_missing_sku_is_unknown(self):
        missing = store()
        missing['partsAvailability'] = {'OTHER': {'pickupDisplay': 'available'}}
        state = MemoryState({KEY: {'R1': 'available'}})
        self.run_check([missing, store('R2')], state)
        self.assertEqual(state.data[KEY]['R1'], 'available')

    def test_all_missing_sku_fails_without_writing(self):
        missing = store()
        missing['partsAvailability'] = {}
        state = MemoryState({KEY: {'R1': 'available'}})
        with self.assertRaises(ValueError):
            self.run_check([missing], state)
        state.save.assert_not_called()

    def test_all_twelve_stores_are_checked(self):
        stores = [store(f'R{i}', f'Store {i}', 'available' if i == 11 else 'unavailable') for i in range(12)]
        state, notify, _ = self.run_check(stores)
        self.assertEqual(len(state.data[KEY]), 12)
        self.assertEqual(list(notify.call_args.args[0]), ['R11'])

    def test_dry_run_never_emails_or_saves(self):
        state, notify, _ = self.run_check([store(status='available')], dry=True)
        notify.assert_not_called()
        state.save.assert_not_called()
        self.assertEqual(state.data, {})

    def test_email_failure_does_not_advance_state(self):
        state = MemoryState({KEY: {'R1': 'unavailable'}})
        with patch('monitor.notify', side_effect=ValueError), self.assertRaises(ValueError):
            monitor.run_once(PART, '92602', session=self.session([store(status='available')]), state=state)
        state.save.assert_not_called()
        self.assertEqual(state.data[KEY]['R1'], 'unavailable')

    def test_api_errors_do_not_advance_state(self):
        for code in (429, 541, 500, 403):
            with self.subTest(code=code):
                state = MemoryState({KEY: {'R1': 'available'}})
                session = Mock()
                session.get.return_value = response(code=code)
                with patch('monitor.notify') as notify, self.assertRaises((ValueError, requests.HTTPError)):
                    monitor.run_once(PART, '92602', session=session, state=state)
                state.save.assert_not_called()
                notify.assert_not_called()

    def test_invalid_json_timeout_and_malformed_shapes(self):
        cases = [response(None), response({}), response({'body': {'stores': []}}),
                 response({'body': {'stores': [None]}}), response({'body': {'stores': [store(), store()]}})]
        invalid = response()
        invalid.json.side_effect = ValueError('invalid JSON')
        cases.append(invalid)
        for result in cases:
            state = MemoryState()
            session = Mock()
            session.get.return_value = result
            with self.assertRaises(ValueError):
                monitor.run_once(PART, '92602', session=session, state=state)
            state.save.assert_not_called()
        session.get.side_effect = requests.Timeout
        with self.assertRaises(requests.Timeout):
            monitor.run_once(PART, '92602', session=session, state=state)
        state.save.assert_not_called()

    def test_email_priority_product_links(self):
        names = ['Other Store', 'Mission Viejo', 'Fashion Island', 'South Coast Plaza', 'Irvine Spectrum Center']
        stores = {str(i): {'name': n, 'quote': 'Today', 'status': 'available'} for i, n in enumerate(names)}
        with patch('monitor.send_email') as send:
            monitor.notify(stores, PART, '92602')
        subject, body = send.call_args.args
        self.assertIn(monitor.PRODUCT, subject)
        self.assertIn(monitor.PRODUCT, body)
        self.assertIn('https://www.apple.com/shop/buy-iphone', body)
        positions = [body.index(n) for n in reversed(names)]
        self.assertEqual(positions, sorted(positions))

    def test_email_test_bypasses_inventory_and_state(self):
        with patch('sys.argv', ['monitor.py', '--test-email']), patch('monitor.send_email') as send, patch('monitor.run_once') as run:
            self.assertEqual(monitor.main(), 0)
        send.assert_called_once()
        self.assertFalse(send.call_args.kwargs['dry_run'])
        run.assert_not_called()

    def test_email_test_dry_run(self):
        with patch('sys.argv', ['monitor.py', '--test-email', '--dry-run']), patch('notifications.requests.Session') as session, patch('monitor.run_once') as run:
            self.assertEqual(monitor.main(), 0)
        session.assert_not_called()
        run.assert_not_called()

    def test_email_test_failure_exit(self):
        with patch('sys.argv', ['monitor.py', '--test-email']), patch('monitor.send_email', side_effect=ValueError):
            self.assertEqual(monitor.main(), 1)

    def test_retry_policy(self):
        with monitor.make_session() as session:
            retries = session.get_adapter(monitor.API).max_retries
            self.assertEqual(retries.total, 3)
            self.assertGreater(retries.backoff_factor, 0)
            self.assertNotIn(429, retries.status_forcelist)
            self.assertNotIn(541, retries.status_forcelist)
            self.assertFalse(retries.respect_retry_after_header)

    def test_once_failure_has_nonzero_exit(self):
        with patch('sys.argv', ['monitor.py', '--once']), patch('monitor.run_once', side_effect=ValueError):
            self.assertEqual(monitor.main(), 1)


class StateTests(unittest.TestCase):
    def test_local_roundtrip_and_corruption(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            state = LocalState(path)
            self.assertEqual(state.load(), {})
            state.save({KEY: {'R1': 'available'}})
            self.assertEqual(state.load(), {KEY: {'R1': 'available'}})
            path.write_text('{bad', encoding='utf-8')
            with self.assertRaises(ValueError):
                state.load()

    def test_github_load_and_sha_compare_write(self):
        data = {KEY: {'R1': 'available'}}
        session = Mock()
        encoded = base64.b64encode(json.dumps(data).encode()).decode()
        session.request.side_effect = [response({}), response({'encoding': 'base64', 'sha': 'oldsha', 'content': encoded}), response({})]
        state = GitHubState('owner/repo', 'mock-token', session=session)
        self.assertEqual(state.load(), data)
        self.assertEqual(session.request.call_args.kwargs['params'], {'ref': 'master'})
        self.assertTrue(session.request.call_args_list[0].args[1].endswith('/branches/master'))
        state.save(data)
        payload = session.request.call_args.kwargs['json']
        self.assertEqual(payload['sha'], 'oldsha')
        self.assertEqual(payload['branch'], 'master')
        self.assertEqual(json.loads(base64.b64decode(payload['content'])), data)

    def test_missing_file_only_after_branch_verified(self):
        session = Mock()
        session.request.side_effect = [response({}), response(code=404)]
        self.assertEqual(GitHubState('o/r', 'token', session=session).load(), {})
        self.assertEqual(session.request.call_count, 2)
        session.request.side_effect = [response(code=404)]
        with self.assertRaises(requests.HTTPError):
            GitHubState('o/r', 'token', session=session).load()

    def test_bad_remote_state_and_conflict_fail_closed(self):
        session = Mock()
        session.request.side_effect = [response({}), response({'encoding': 'none'})]
        state = GitHubState('o/r', 'token', session=session)
        with self.assertRaises(ValueError):
            state.load()
        session.request.side_effect = [response(code=409)]
        with self.assertRaises(requests.HTTPError):
            state.save({KEY: {}})

    def test_corrupt_status_rejected(self):
        from state_store import validate
        for data in ([], {'bad': []}, {KEY: {'R1': 'unknown'}}, {KEY: {'R1': {'name': 'legacy'}}}):
            with self.assertRaises(ValueError):
                validate(data)


if __name__ == '__main__':
    unittest.main()

