#!/usr/bin/env python3
# Based on 2022 @j4imefoo/nokyc, hardened: per-exchange isolation,
# timeouts, proper avoid_methods parsing, unified dif computation,
# reference-price fallback when the Bisq pricenode is unreachable.

import argparse
import ast
import configparser
import itertools
import requests
import statistics
import sys
import signal
import threading
import time

from bisq import Bisq
from robosats import Robosats
from hodlhodl import HodlHodl
from peach import Peach

config = configparser.ConfigParser()
config.read('nokycconfig.ini')

def get_tor_session():
    session = requests.session()
    try:
        proxy_port = config['DEFAULT'].get('TOR_PORT')
        if proxy_port:
            proxy = f'socks5h://127.0.0.1:{proxy_port}'
            session.proxies = {'http': proxy, 'https': proxy}
    except Exception:
        pass
    return session

def tor_get(session, url, **kw):
    """Session.get with a mandatory timeout (fails loudly, never hangs)."""
    kw.setdefault('timeout', 90)
    return session.get(url, **kw)

def sigint_handler(signal, frame):
    print('\nCancelled.')
    sys.exit(0)

def animate():
    for c in itertools.cycle(['|', '/', '-', '\\']):
        if done:
            break
        sys.stdout.write('\rGathering offers... ' + c)
        sys.stdout.flush()
        time.sleep(0.1)

def get_user_arguments():
    parser = argparse.ArgumentParser(
        description="Lists current Bisq, HodlHodl and Robosats offers in the terminal")
    supported_exchanges = ["bisq", "robosats", "hodlhodl", "peach", "all"]

    parser.add_argument('-t', '--type', help='Type of orders (buy or sell)',
                        type=str, choices=['buy', 'sell'], default='sell')
    parser.add_argument('-f', '--fiat', help='Fiat currency', type=str,
                        choices=['eur', 'usd', 'gbp', 'cad', 'aud', 'chf',
                                 'brl', 'czk', 'sek', 'nzd', 'dkk', 'pln'],
                        default='eur')
    parser.add_argument('-d', '--deviation', help='Max deviation from market price',
                        type=int, default=8)
    parser.add_argument('-e', '--exchange', help='Exchange: ' + ' '.join(supported_exchanges),
                        type=str, default='all')
    args = parser.parse_args()

    exchange = args.exchange.lower()
    exchanges = exchange.replace(' ', '').split(',') if ',' in exchange else [exchange]
    for ex in exchanges:
        if ex not in supported_exchanges:
            raise Exception('Sorry, only the following exchanges are supported: '
                            + ' '.join(supported_exchanges))
    return args.fiat, args.type, args.deviation, exchanges

def collect(exchange_name, fiat, direction, limit, session, price_exch, allOffers):
    """Run one exchange's getter, isolated so failures don't abort the run."""
    try:
        if exchange_name == 'bisq':
            offers = Bisq.getOffers(fiat, direction, price_exch, session)
        elif exchange_name == 'robosats':
            offers = Robosats.getOffers(fiat, direction, session, price_exch)
        elif exchange_name == 'hodlhodl':
            offers = HodlHodl.getOffers(fiat, direction, price_exch, session)
        elif exchange_name == 'peach':
            offers = Peach.getOffers(fiat, direction, price_exch, session)
        else:
            return
        for offer in offers:
            # Unified deviation calculation (defensive: fill if missing)
            if offer.get('dif') is None:
                offer['dif'] = (offer['price'] / price_exch - 1) * 100
            offer['exchange'] = offer.get('exchange', exchange_name.capitalize())
            allOffers.append(offer)
    except Exception as e:
        print(f"\n[{exchange_name}] failed: {type(e).__name__}: {e}\n")

if __name__ == '__main__':
    done = False
    t = threading.Thread(target=animate)
    t.daemon = True
    t.start()

    signal.signal(signal.SIGINT, sigint_handler)
    fiat, direction, limit, exchanges = get_user_arguments()
    session = get_tor_session()

    price_exch = None
    try:
        price_exch = Bisq.getFiatPrice(fiat, session)
    except Exception as e:
        print(f"\nCould not get fiat price from Bisq pricenode: {e}")
        print("Continuing without a market reference price; deviation "
              "filter will run against the median of gathered offers.\n")

    # When falling back, pass a sentinel reference price; dif is recomputed
    # uniformly after the true reference (median of offers) is derived.
    ref_for_modules = price_exch if price_exch is not None else 1.0

    allOffers = []
    exchanges_to_query = ['bisq', 'robosats', 'hodlhodl', 'peach'] \
        if 'all' in exchanges else exchanges
    for ex in exchanges_to_query:
        collect(ex, fiat, direction, limit, session, ref_for_modules, allOffers)

    if price_exch is None:
        prices = [o['price'] for o in allOffers if o.get('price')]
        if prices:
            price_exch = int(statistics.median(prices))
            print(f"Using median of gathered offers as reference price: "
                  f"{price_exch} {fiat.upper()} (caveat: weighted toward "
                  f"the exchange with the most offers)\n")
        else:
            done = True
            print("\nNo reference price and no offers available.")
            sys.exit(1)

    # Recompute dif uniformly against the (possibly fallback) reference price
    for offer in allOffers:
        offer['dif'] = (offer['price'] / price_exch - 1) * 100

    allOffers.sort(key=lambda item: item.get('price') or 0,
                   reverse=(direction == 'buy'))

    done = True
    print('\r                      ', end='')
    print(f"\rPrice: {price_exch} {fiat.upper()}\n")
    print(f"BTC {direction} offers:\n")
    print(f"{'Exchange':9} {'Price':12} {'Dif':6} {'BTC min':8} "
          f"{'BTC max':9} {'Min':7} {'Max':7} {'Method'}")

    avoid_methods = ast.literal_eval(config['DEFAULT']['avoid_methods'])

    for offer in allOffers:
        if ((direction == 'sell' and offer['dif'] < limit)
                or (direction == 'buy' and offer['dif'] > -limit)) \
                and offer['method'].lower() not in avoid_methods:
            print(f"{offer['exchange']:9}{offer['price']:9n} {fiat.upper():4} "
                  f"{offer['dif']:4.1f}% {offer['min_btc']:8.4f} "
                  f"{offer['max_btc']:8.4f} {offer['min_amount']:7n} "
                  f"{offer['max_amount']:7n} {offer['method']}")
