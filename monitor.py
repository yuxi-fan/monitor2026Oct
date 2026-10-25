"""Monitor Apple US pickup availability and email on newly available stores."""
import argparse
import copy
import logging
import os
import time
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from state_store import GitHubState, LocalState
from notifications import send_email

API = 'https://www.apple.com/shop/retail/pickup-message'
DEFAULT_PART = 'MK1H4LL/A'
PRODUCT = 'iPhone Duo, Black, 512GB, unlocked'
PRODUCTS = {
    DEFAULT_PART: PRODUCT,
    'MK1L4LL/A': 'iPhone Duo, Night Sky (Black), 1TB, unlocked',
    'MK1G4LL/A': 'iPhone Duo, Star White, 512GB, unlocked',
    'MK1J4LL/A': 'iPhone Duo, Star White, 1TB, unlocked',
}
PRIORITY = ('irvine spectrum center', 'south coast plaza', 'fashion island', 'mission viejo')


def make_session():
    session = requests.Session()
    # Stop on rate limits/anti-bot responses; wait for the next scheduled run.
    retry = Retry(total=3, connect=3, read=3, status=3, backoff_factor=2,
                  status_forcelist=(500, 502, 503, 504), allowed_methods=('GET',),
                  respect_retry_after_header=False)
    session.mount('https://', HTTPAdapter(max_retries=retry))
    session.headers.update({'Accept': 'application/json',
                            'User-Agent': 'personal-apple-pickup-monitor/2.0'})
    return session


def check(part, zipcode, session):
    response = session.get(API, params={'pl': 'true', 'parts.0': part, 'location': zipcode},
                           timeout=(10, 30))
    if response.status_code in (429, 541):
        raise ValueError(f'Apple HTTP {response.status_code}; wait for the next scheduled run')
    response.raise_for_status()
    data = response.json()
    stores = data.get('body', {}).get('stores') if isinstance(data, dict) and isinstance(data.get('body'), dict) else None
    if not isinstance(stores, list) or not stores:
        raise ValueError('No valid body.stores; state unchanged')
    results = {}
    for store in stores:
        if not isinstance(store, dict):
            raise ValueError('Invalid store entry; state unchanged')
        key, name = store.get('storeNumber'), store.get('storeName')
        if not isinstance(key, str) or not key or not isinstance(name, str) or not name or key in results:
            raise ValueError('Missing/duplicate store identity; state unchanged')
        parts = store.get('partsAvailability')
        sku = parts.get(part) if isinstance(parts, dict) else None
        raw = sku.get('pickupDisplay') if isinstance(sku, dict) else None
        status = raw if raw in ('available', 'unavailable') else None
        quote = sku.get('pickupSearchQuote', '') if isinstance(sku, dict) else ''
        results[key] = {'name': name, 'status': status,
                        'quote': quote if isinstance(quote, str) else ''}
        logging.info('%s: %s', name, status or 'unknown (previous state retained)')
    if all(s['status'] is None for s in results.values()):
        raise ValueError('Expected SKU/status absent from every store; state unchanged')
    logging.info('Checked all %d returned stores', len(results))
    return results


def notify(new, part, zipcode):
    product = PRODUCT if part == DEFAULT_PART else f'Apple part {part}'
    subject = f'Apple pickup available: {product} ({len(new)} stores)'
    lines = [product, f'Part: {part}', f'Search ZIP: {zipcode}', '', 'Newly available stores (priority stores first):']
    def order(store):
        name = store['name'].casefold()
        rank = next((i for i, p in enumerate(PRIORITY) if p in name), len(PRIORITY))
        return rank, name
    for store in sorted(new.values(), key=order):
        lines.append(f"- {store['name']}: available. {store['quote']}")
    lines.extend(['', 'Confirm pickup with Apple:', 'https://www.apple.com/shop/buy-iphone',
                  API + '?' + urlencode({'pl': 'true', 'parts.0': part, 'location': zipcode}),
                  '', 'Inventory can change. This monitor does not purchase or reserve anything.'])
    send_email(subject, '\n'.join(lines))


def report_once(part, zipcode, dry_run=False, *, session, additional_parts=None):
    """Email all requested products together without reading or writing history."""
    parts = list(dict.fromkeys([part, *(additional_parts or [])]))
    snapshots = []
    for sku in parts:
        logging.info('Checking product %s', sku)
        snapshots.append((sku, check(sku, zipcode, session)))
    if dry_run:
        logging.info('DRY RUN: inventory report suppressed; no history accessed')
        return
    lines = [f'Search ZIP: {zipcode}', 'Current inventory (priority stores first):', '']
    total_available = total_stores = 0
    def order(store):
        name = store['name'].casefold()
        return next((i for i, p in enumerate(PRIORITY) if p in name), len(PRIORITY)), name
    for sku, current in snapshots:
        count = sum(s['status'] == 'available' for s in current.values())
        total_available += count
        total_stores += len(current)
        lines.extend([PRODUCTS.get(sku, f'Apple part {sku}'), f'Part: {sku}',
                      f'{count} of {len(current)} stores available'])
        for store in sorted(current.values(), key=order):
            lines.append(f"- {store['name']}: {store['status'] or 'unknown'}. {store['quote']}")
        lines.append('')
    lines.extend(['https://www.apple.com/shop/buy-iphone',
                  'Snapshot only; nothing is purchased or reserved.'])
    send_email(f'Apple pickup report: {total_available}/{total_stores} product-store checks available', '\n'.join(lines))
    logging.info('Inventory report accepted for %d products; no history read or written', len(snapshots))


def run_once(part, zipcode, dry_run=False, *, session=None, state=None):
    state = state or LocalState('state.json')
    logging.info('Loading previous stock state')
    previous = state.load()
    if session is None:
        with make_session() as owned:
            current = check(part, zipcode, owned)
    else:
        current = check(part, zipcode, session)
    key = f'{part}:{zipcode}'
    old = previous.get(key, {})
    new = {k: v for k, v in current.items()
           if v['status'] == 'available' and old.get(k) != 'available'}
    updated = copy.deepcopy(previous)
    updated.setdefault(key, {})
    for k, v in current.items():
        if v['status'] is not None:
            updated[key][k] = v['status']
    if dry_run:
        logging.info('DRY RUN: would alert for %d stores; no email or state write', len(new))
        return
    if new:
        logging.info('Sending availability alert through Resend')
        notify(new, part, zipcode)
        logging.info('Sent alert for %d newly available stores', len(new))
    if updated != previous:
        logging.info('Saving stock state')
        state.save(updated)
        logging.info('Saved stock state')
    else:
        logging.info('Stock state unchanged; no alert')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--part', default=os.getenv('APPLE_PART', DEFAULT_PART))
    parser.add_argument('--additional-part', action='append', default=[], help='Additional SKU in the same report email; repeatable, requires --report')
    parser.add_argument('--zip', default=os.getenv('APPLE_ZIP', '92602'))
    parser.add_argument('--interval', type=int, default=600)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--report', action='store_true', help='Email every current store status without accessing history')
    parser.add_argument('--test-email', action='store_true', help='Send a delivery test without querying Apple or touching stock state')
    parser.add_argument('--state-backend', choices=('local', 'github'), default='local')
    parser.add_argument('--state-file', default='state.json')
    args = parser.parse_args()
    if args.additional_part and not args.report:
        parser.error('--additional-part requires --report')
    if args.interval < 300:
        parser.error('Minimum interval is 300 seconds')
    if args.test_email:
        try:
            send_email('Apple stock monitor: Resend delivery test',
                       'This is a delivery test only. It does not report stock availability and does not change stock state.',
                       dry_run=args.dry_run)
        except ValueError:
            logging.error('Email test failed; check Resend configuration and dashboard')
            return 1
        return 0
    while True:
        try:
            with make_session() as session:
                if args.report:
                    report_once(args.part, args.zip, args.dry_run, session=session, additional_parts=args.additional_part)
                else:
                    state = GitHubState(os.environ['GITHUB_REPOSITORY'], os.environ['STATE_TOKEN']) if args.state_backend == 'github' else LocalState(args.state_file)
                    run_once(args.part, args.zip, args.dry_run, session=session, state=state)
        except (requests.RequestException, ValueError, KeyError, OSError):
            # Exception strings can contain credentials or email API details.
            logging.error('Check failed. Check Apple API access and Resend settings; history modes also require valid state access.')
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    raise SystemExit(main())


