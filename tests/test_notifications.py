import os
import unittest
from unittest.mock import Mock, patch

import requests
from notifications import NotificationError, send_email

CONFIG = {'RESEND_API_KEY': 'secret-never-log', 'RESEND_FROM_EMAIL': 'Stock <stock@example.com>',
          'ALERT_EMAIL': 'recipient@example.com'}


class ResendTests(unittest.TestCase):
    def test_payload_and_api(self):
        with patch.dict(os.environ, CONFIG), patch('notifications.requests.Session') as factory:
            session = factory.return_value.__enter__.return_value
            session.post.return_value = Mock(status_code=200)
            session.post.return_value.json.return_value = {'id': 'email-id'}
            self.assertEqual(send_email('Subject', 'Body'), 'email-id')
        args, kwargs = session.post.call_args
        self.assertEqual(args, ('https://api.resend.com/emails',))
        self.assertEqual(kwargs['json'], {'from': CONFIG['RESEND_FROM_EMAIL'], 'to': [CONFIG['ALERT_EMAIL']], 'subject': 'Subject', 'text': 'Body'})
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer ' + CONFIG['RESEND_API_KEY'])
        self.assertEqual(kwargs['timeout'], (10, 30))
        self.assertFalse(kwargs['allow_redirects'])

    def test_dry_run_without_credentials_never_contacts_resend(self):
        with patch.dict(os.environ, {}, clear=True), patch('notifications.requests.Session') as factory:
            send_email('Subject', 'Body', dry_run=True)
        factory.assert_not_called()

    def test_missing_config_never_sends(self):
        with patch.dict(os.environ, {}, clear=True), patch('notifications.requests.Session') as factory:
            with self.assertRaises(NotificationError):
                send_email('Subject', 'Body')
        factory.assert_not_called()

    def test_http_errors_do_not_leak_response(self):
        for status in (301, 400, 401, 403, 422, 429):
            with self.subTest(status=status), patch.dict(os.environ, CONFIG), patch('notifications.requests.Session') as factory:
                session = factory.return_value.__enter__.return_value
                session.post.return_value = Mock(status_code=status, text='secret-never-log')
                with self.assertLogs(level='ERROR') as captured, self.assertRaises(NotificationError):
                    send_email('Subject', 'Body')
                self.assertNotIn('secret-never-log', '\n'.join(captured.output))
                session.post.assert_called_once()

    def test_network_and_server_retries_share_idempotency_key(self):
        for failure in (requests.Timeout('secret-never-log'), Mock(status_code=503)):
            with patch.dict(os.environ, CONFIG), patch('notifications.requests.Session') as factory, patch('notifications.time.sleep') as sleep:
                session = factory.return_value.__enter__.return_value
                success = Mock(status_code=200)
                success.json.return_value = {'id': 'email-id'}
                session.post.side_effect = [failure, success]
                self.assertEqual(send_email('Subject', 'Body'), 'email-id')
                self.assertEqual(session.post.call_count, 2)
                self.assertEqual(session.post.call_args_list[0].kwargs['headers']['Idempotency-Key'],
                                 session.post.call_args_list[1].kwargs['headers']['Idempotency-Key'])
                sleep.assert_called_once_with(2)

    def test_network_exhaustion_is_sanitized(self):
        with patch.dict(os.environ, CONFIG), patch('notifications.requests.Session') as factory, patch('notifications.time.sleep'):
            session = factory.return_value.__enter__.return_value
            session.post.side_effect = requests.Timeout('secret-never-log')
            with self.assertLogs() as logs, self.assertRaises(NotificationError) as error:
                send_email('Subject', 'Body')
            self.assertEqual(session.post.call_count, 3)
            self.assertNotIn('secret-never-log', str(error.exception) + str(logs.output))

    def test_invalid_json_and_missing_confirmation_fail(self):
        for result in ({}, [], {'id': ''}, ValueError('secret-never-log')):
            with patch.dict(os.environ, CONFIG), patch('notifications.requests.Session') as factory:
                session = factory.return_value.__enter__.return_value
                session.post.return_value = Mock(status_code=200)
                if isinstance(result, Exception):
                    session.post.return_value.json.side_effect = result
                else:
                    session.post.return_value.json.return_value = result
                with self.assertRaises(NotificationError):
                    send_email('Subject', 'Body')


if __name__ == '__main__':
    unittest.main()
