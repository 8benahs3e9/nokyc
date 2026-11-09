#!/usr/bin/env python3
# RoboSats federated-era module (diagnostic edition).
# Discovery: federation.json from GitHub (canonical) with client-onion
# fallback. Book schema: flat list with price/satoshis_now/min_amount/
# max_amount/payment_method/type(0=BUY,1=SELL). Note: raw.githubusercontent
# serves text/plain, so JSON parsing must not depend on content-type.
# Transport: SIGALRM watchdog + per-coordinator status reporting.

import configparser
import signal
import requests

config = configparser.ConfigParser()
config.read('nokycconfig.ini')

CLIENT_URL = config.get('ROBOSATS', 'base_url', fallback='').rstrip('/')
TOR_PORT = config.get('DEFAULT', 'TOR_PORT', fallback='9050')
BOOK_TIMEOUT = int(config.get('ROBOSATS', 'book_timeout', fallback='30'))

CURRENCY_IDS = {'usd': 1, 'eur': 2}   # verified; extend after checking

class Stalled(Exception):
    pass

def _alarm(signum, frame):
    raise Stalled('watchdog fired')

signal.signal(signal.SIGALRM, _alarm)

class Robosats:

    @staticmethod
    def _get_json(session, url, params=None, limit=None):
        """GET with a hard wall-clock ceiling.
        Returns (status, payload). Status is 'ok' or a human-readable
        failure description; JSON parsing is attempted regardless of
        content-type (raw.githubusercontent.com serves JSON as text/plain).
        """
        limit = limit or BOOK_TIMEOUT
        signal.setitimer(signal.ITIMER_REAL, limit)
        try:
            r = session.get(url, params=params, timeout=(8, limit),
                            headers={'Accept': 'application/json'})
            try:
                return ('ok', r.json())
            except ValueError:
                ctype = r.headers.get('content-type', '')
                return (f'http {r.status_code} ct={ctype[:40]!r} '
                        f'(non-JSON body)', None)
        except Stalled:
            return (f'timeout >{limit}s (watchdog)', None)
        except requests.exceptions.ConnectTimeout:
            return ('connect timeout', None)
        except requests.exceptions.ReadTimeout:
            return ('read timeout', None)
        except requests.exceptions.ProxyError:
            return ('tor socks error', None)
        except requests.RequestException as e:
            reason = ''
            resp = getattr(e, 'response', None)
            if resp is not None:
                reason = f' http {resp.status_code}'
            return (f'net: {type(e).__name__}{reason}', None)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)

    @staticmethod
    def _discover_coordinators(session):
        feeds = ['https://raw.githubusercontent.com/RoboSats/robosats/'
                 'main/frontend/static/federation.json']
        if CLIENT_URL:
            feeds.append(f"{CLIENT_URL}/static/federation.json")

        for feed in feeds:
            status, payload = Robosats._get_json(session, feed, limit=25)
            if status != 'ok' or not isinstance(payload, dict):
                print(f'[Robosats] discovery feed failed ({feed}): {status}')
                continue
            coordinators = []
            for key, entry in payload.items():
                if not isinstance(entry, dict):
                    continue
                mainnet = entry.get('mainnet')
                onion = mainnet.get('onion') if isinstance(mainnet, dict) \
                    else mainnet
                if not (isinstance(onion, str) and '.onion' in onion):
                    continue
                base = onion if onion.startswith('http') \
                    else f"http://{onion}"
                coordinators.append((base.rstrip('/'),
                                     str(entry.get('longAlias') or key)))
            if coordinators:
                print(f'[Robosats] discovered {len(coordinators)} '
                      f'coordinators via {feed}')
                return coordinators
        return []

    @staticmethod
    def getOffers(fiat, direction, session, refprice):
        id_candidates = []
        if fiat in CURRENCY_IDS:
            id_candidates.append(CURRENCY_IDS[fiat])
        type_param = 1 if direction == 'sell' else 0

        coordinators = Robosats._discover_coordinators(session)
        if not coordinators:
            print('[Robosats] coordinator discovery failed')
            return []

        results = []
        seen = set()
        statuses = []
        for base, alias in coordinators:
            coord_statuses = set()
            zero_skipped = 0
            for cid in id_candidates:
                status, payload = Robosats._get_json(
                    session, f"{base}/api/book/",
                    params={'currency': cid, 'type': type_param})
                if status != 'ok':
                    coord_statuses.add(status)
                    continue
                # Empty-book sentinel (404 + JSON dict body, seen live)
                if isinstance(payload, dict) and payload.get('not_found'):
                    coord_statuses.add('0 rows (empty book)')
                    continue
                if not isinstance(payload, list):
                    coord_statuses.add('schema: non-list payload')
                    continue
                if not payload:
                    coord_statuses.add('0 rows (empty book)')
                    continue
                parsed = 0
                for line in payload:
                    if not isinstance(line, dict):
                        continue
                    price = float(line.get('price') or 0)
                    if not price:
                        zero_skipped += 1
                        continue
                    oid = line.get('id')
                    if oid in seen:
                        continue
                    seen.add(oid)
                    sats = float(line.get('satoshis_now') or 0)
                    min_fiat = float(line.get('min_amount') or 0)
                    max_fiat = float(line.get('max_amount') or 0)
                    results.append({
                        'exchange': 'Robosats',
                        'price': price,
                        'dif': (price / refprice - 1) * 100,
                        'min_btc': (min_fiat / price) if price else 0,
                        'max_btc': sats / 1e8,
                        'min_amount': int(min_fiat),
                        'max_amount': int(max_fiat) if max_fiat
                                      else int(sats * price / 1e8),
                        'method': str(line.get('payment_method', '')).strip()
                                  or '?',
                    })
                    parsed += 1
                coord_statuses.add(f'{parsed} offers parsed')
            statuses.append(f"{alias}: {'; '.join(sorted(coord_statuses)) or 'no attempts'}"
                            + (f" [{zero_skipped} rows had price=0]"
                               if zero_skipped else ''))

        print('[Robosats] per-coordinator status:')
        for line in statuses:
            print(f'  {line}')
        return results
