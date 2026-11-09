#!/usr/bin/env python3
# Bisq module: pricenode onion (confirmed live 2026-09), offers with
# onion-first fallback chain configured in nokycconfig.ini [BISQ].
# Clearnet fallback is OPT-IN (allow_clearnet_fallback), always over HTTP,
# never through the Tor session, and never with TLS verification disabled.

import configparser
import requests

config = configparser.ConfigParser()
config.read('nokycconfig.ini')

PRICE_URL = config.get('BISQ', 'price_url',
                       fallback='http://ro7nv73awqs3ga2qtqeqawrjpbxwarsazznszvr6whv7tes5ehffopid.onion/getAllMarketPrices')
OFFERS_URLS = None  # parsed lazily


class Bisq:

    @staticmethod
    def getFiatPrice(fiat, session):
        r = session.get(PRICE_URL, timeout=90)
        r.raise_for_status()
        for currency in r.json()['data']:
            if currency['currencyCode'].lower() == fiat:
                return int(float(currency['price']))
        raise Exception(f"No {fiat.upper()} price found in pricenode response")

    @staticmethod
    def _offers_urls(fiat, direction):
        global OFFERS_URLS
        if OFFERS_URLS is None:
            import ast
            base = ast.literal_eval(config.get(
                'BISQ', 'offers_urls',
                fallback='["http://bisqmktwih46lbda65e27uefnn2nznnk5oygdwyqcdrpo7yy7i5qd7ad.onion", "https://bisq.markets"]'))
            OFFERS_URLS = base
        for base in OFFERS_URLS:
            yield f"{base.rstrip('/')}/api/offers?market=btc_{fiat.upper()}&direction={direction.upper()}"

    @staticmethod
    def _parse(values, fiat, direction):
        """Normalize a response to {'_list': [...]} or None if unusable."""
        if isinstance(values, list) and values:
            return {'_list': values}
        key = f"btc_{fiat}"
        if isinstance(values, dict) and isinstance(values.get(key), dict) \
                and values[key].get(direction + 's'):
            return values
        return None

    @staticmethod
    def getOffers(fiat, direction, refprice, session):
        ALLOW_CLEARNET = config.getboolean('BISQ', 'allow_clearnet_fallback',
                                           fallback=False)

        def schema_ok(payload):
            return Bisq._parse(payload, fiat, direction) is not None

        values, errors = None, []
        for url in Bisq._offers_urls(fiat, direction):
            # --- primary attempt: through the caller's Tor session ---
            try:
                r = session.get(url, timeout=60)
                parsed = Bisq._parse(r.json(), fiat, direction)
                if parsed is not None:
                    values = parsed
                    break
                errors.append(f"{url}: schema mismatch "
                              f"({type(values).__name__} payload)")
            except (requests.RequestException, ValueError) as e:
                errors.append(f"{url}: {type(e).__name__}: {e}")

            # --- opt-in clearnet fallback: dedicated session, no proxy,
            #     TLS verification stays ON, plain http only if offered ---
            if ALLOW_CLEARNET and url.startswith('https://'):
                http_url = url.replace('https://', 'http://')
                try:
                    plain = requests.session()  # no proxies, verify=True
                    r = plain.get(http_url, timeout=60)
                    r.raise_for_status()
                    parsed = Bisq._parse(r.json(), fiat, direction)
                    if parsed is not None:
                        values = parsed
                        errors.append(f"{http_url}: OK (clearnet fallback)")
                        break
                    errors.append(f"{http_url}: schema mismatch")
                except Exception as e:
                    errors.append(f"{http_url}: {type(e).__name__}: {e}")

        if values is None:
            detail = ' | '.join(errors) if errors else 'unknown'
            raise Exception(f"All Bisq offers sources failed. Attempts: {detail}.")

        key = f"btc_{fiat}"
        lines = values[key][direction + 's'] if key in values \
            else values['_list']

        alloffers = []
        for line in lines:
            att = line.get('attributes', line) if isinstance(line, dict) else line
            price = int(float(att['price']))
            min_btc = float(att.get('min_amount') or 0)
            alloffers.append({
                'exchange': 'Bisq',
                'price': price,
                'dif': (price / refprice - 1) * 100,
                'min_btc': min_btc,
                'max_btc': float(att.get('amount') or 0),
                'min_amount': int(min_btc * price),
                'max_amount': int(float(att.get('volume') or 0)),
                'method': att.get('payment_method') or '?',
            })
        alloffers.sort(key=lambda item: item.get('price'))
        return alloffers
