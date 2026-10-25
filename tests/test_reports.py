import unittest
from unittest.mock import Mock, patch
import monitor


class ReportTests(unittest.TestCase):
    def test_unavailable_report_sends_every_time_without_state(self):
        current = {'R1': {'name': 'Irvine Spectrum Center', 'status': 'unavailable', 'quote': ''},
                   'R2': {'name': 'Other', 'status': None, 'quote': ''}}
        with patch('monitor.check', return_value=current), patch('monitor.send_email') as send, patch('monitor.LocalState') as state:
            for _ in range(2):
                monitor.report_once(monitor.DEFAULT_PART, '92602', session=Mock())
        self.assertEqual(send.call_count, 2)
        state.assert_not_called()
        subject, body = send.call_args.args
        self.assertIn('0/2', subject)
        self.assertIn('unavailable', body)
        self.assertIn('Other: unknown', body)
        self.assertIn(monitor.DEFAULT_PART, body)

    def test_report_dry_run_does_not_send(self):
        with patch('monitor.check', return_value={}), patch('monitor.send_email') as send:
            monitor.report_once(monitor.DEFAULT_PART, '92602', True, session=Mock())
        send.assert_not_called()

    def test_cli_report_never_initializes_state(self):
        with patch('sys.argv', ['monitor.py', '--once', '--report']), patch('monitor.report_once') as report, patch('monitor.LocalState') as local, patch('monitor.GitHubState') as github:
            self.assertEqual(monitor.main(), 0)
        report.assert_called_once()
        local.assert_not_called()
        github.assert_not_called()

    def test_failed_api_never_sends_report(self):
        with patch('monitor.check', side_effect=ValueError), patch('monitor.send_email') as send:
            with self.assertRaises(ValueError):
                monitor.report_once(monitor.DEFAULT_PART, '92602', session=Mock())
        send.assert_not_called()


class MultiProductTests(unittest.TestCase):
    def test_two_products_one_email(self):
        current = {'R1': {'name': 'Mission Viejo', 'status': 'available', 'quote': 'Today'}}
        with patch('monitor.check', return_value=current) as check, patch('monitor.send_email') as send:
            monitor.report_once(monitor.DEFAULT_PART, '92602', session=Mock(), additional_parts=['MK1L4LL/A'])
        self.assertEqual([call.args[0] for call in check.call_args_list], ['MK1H4LL/A', 'MK1L4LL/A'])
        send.assert_called_once()
        body = send.call_args.args[1]
        for value in ('MK1H4LL/A', 'MK1L4LL/A', '512GB', '1TB'):
            self.assertIn(value, body)

    def test_second_product_failure_sends_no_partial_report(self):
        with patch('monitor.check', side_effect=[{}, ValueError('API failure')]), patch('monitor.send_email') as send:
            with self.assertRaises(ValueError):
                monitor.report_once(monitor.DEFAULT_PART, '92602', session=Mock(), additional_parts=['MK1L4LL/A'])
        send.assert_not_called()

    def test_multi_product_dry_run_checks_both_sends_nothing(self):
        with patch('monitor.check', return_value={}) as check, patch('monitor.send_email') as send:
            monitor.report_once(monitor.DEFAULT_PART, '92602', True, session=Mock(), additional_parts=['MK1L4LL/A'])
        self.assertEqual(check.call_count, 2)
        send.assert_not_called()
