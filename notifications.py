"""Send notifications with Resend; never log credentials or response bodies."""
import logging
import os
import time
import uuid

import requests

RESEND_API = 'https://api.resend.com/emails'


class NotificationError(ValueError):
    """Safe notification failure; stock state must not advance."""


def send_email(subject, text, *, dry_run=False):
    if dry_run:
        logging.info('DRY RUN: email suppressed')
        return
    names = ('RESEND_API_KEY', 'RESEND_FROM_EMAIL', 'ALERT_EMAIL')
    config = {name: os.getenv(name, '').strip() for name in names}
    if not all(config.values()):
        logging.error('Resend configuration incomplete: set RESEND_API_KEY, RESEND_FROM_EMAIL, ALERT_EMAIL')
        raise NotificationError('Missing email configuration')
    payload = {'from': config['RESEND_FROM_EMAIL'], 'to': [config['ALERT_EMAIL']],
               'subject': subject, 'text': text}
    # Reuse the same key and payload for retries of this particular send.
    headers = {'Authorization': 'Bearer ' + config['RESEND_API_KEY'],
               'Idempotency-Key': 'apple-monitor/' + str(uuid.uuid4())}
    with requests.Session() as session:
        for attempt in range(3):
            try:
                response = session.post(RESEND_API, json=payload, headers=headers,
                                        timeout=(10, 30), allow_redirects=False)
            except requests.RequestException:
                if attempt < 2:
                    logging.warning('Resend network error; retrying with the same idempotency key')
                    time.sleep(2 ** (attempt + 1))
                    continue
                logging.error('Resend network error; send not confirmed')
                raise NotificationError('Email send not confirmed') from None
            if response.status_code in (500, 502, 503, 504) and attempt < 2:
                logging.warning('Resend temporary HTTP %d; retrying', response.status_code)
                time.sleep(2 ** (attempt + 1))
                continue
            if not 200 <= response.status_code < 300:
                logging.error('Resend HTTP %d; inspect Resend dashboard and configuration', response.status_code)
                raise NotificationError('Resend rejected email')
            try:
                data = response.json()
            except ValueError:
                raise NotificationError('Resend returned invalid JSON') from None
            if not isinstance(data, dict) or not isinstance(data.get('id'), str) or not data['id']:
                raise NotificationError('Resend did not return an email ID')
            logging.info('Resend accepted email; check dashboard and recipient inbox for delivery')
            return data['id']
