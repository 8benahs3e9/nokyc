#!/usr/bin/env python3
# Peach Bitcoin module per https://docs.peachbitcoin.com/
# Public market data, no credentials expected. Primary: POST /v1/offer/search
# with a type filter; fallback: GET /v1/offers, filtered client-side.
# Schema per docs: offers have type(ask|bid), amount (sats, scalar or range),
# meansOfPayment {CCY: [method ids]}, premium, prices {CCY: fiat/BTC}.
# NOTE: schema taken from docs, not yet verified live over Tor — if it
# misbehaves, run: curl -X POST https://api.peachbitcoin.com/v1/offer/search \
#   -H 'Content-Type: application/json' -d '{"type":"ask"}'

import configparser
import requests

config = configparser.ConfigParser()
config.read('nokycconfig.ini')

API_BASE = config.get('PEACH', 'base_url',
                      fallback='https://api.peachbitcoin.com').rstrip('/')

class Peach:

    @staticmethod
    def _fetch_raw(session, otype):
        """Fetch offers of one type; returns (offers_list, note) or (None, err)."""
        # --- primary: POST /v1/offer/search with type filter ---
        try:
            r = session.post(f'{API_BASE}/v1/offer/search',
                             json={'type': otype},
                             headers={'Accept': 'application/json'},
                             timeout=60)
            if r.status_code in (401, 403):
                return (None, f'post search: http {r.status_code} (auth required?)')
            r.raise_for_status()
            payload = r.json()
            entries = payload.get('offers') if isinstance(payload, dict) \
                else (payload if isinstance(payload, list) else None)
            if isinstance(entries, list):
                return (entries, 'post /v1/offer/search')
            return (None, 'post search: schema mismatch')
        except (requests.RequestException, ValueError) as e:
            primary_err = f'{type(e).__name__}: {e}'
        else:
            primary_err = 'unreachable'

        # --- fallback: GET /v1/offers, filter client-side ---
        try:
            r = session.get(f'{API_BASE}/v1/offers',
                            headers={'Accept': 'application/json'},
                            timeout=60)
            r.raise_for_status()
            payload = r.json()
            entries = payload.get('offers') if isinstance(payload, dict) \
                else (payload if isinstance(payload, list) else None)
            if isinstance(entries, list):
                wanted = [o for o in entries
                          if isinstance(o, dict) and o.get('type') == otype]
                return (wanted, f'get /v1/offers ({primary_err}; filtered {len(wanted)})')
            return (None, 'get /v1/offers: schema mismatch')
        except (requests.RequestException, ValueError) as e:
            return (None, f'search: {primary_err} | offers: {type(e).__name__}: {e}')

    @staticmethod
    def getOffers(fiat, direction, refprice, session):
        fiat = fiat.upper()
        # Peach 'ask' = selling sats, 'bid' = buying sats. Mirror our direction.
        otype = 'ask' if direction == 'sell' else 'bid'

        entries, note = Peach._fetch_raw(session, otype)
        if entries is None:
            raise Exception(f'Peach offers fetch failed: {note}')

        alloffers = []
        for item in entries:
            try:
                if not isinstance(item, dict):
                    continue
                if item.get('online') is False:
                    continue
                mop = item.get('meansOfPayment') or {}
                if fiat not in mop:
                    continue

                amount = item.get('amount')
                if isinstance(amount, (list, tuple)) and amount:
                    sats_min, sats_max = float(amount[0]), float(amount[-1])
                else:
                    sats_min = sats_max = float(amount or 0)

                # Peach 'prices.CCY' = TOTAL FIAT VOLUME of the offer, not
                # a per-BTC unit price (verified live 2026-09: volume /
                # btc amount tracks market). Unit price = volume / amount.
                volume = (item.get('prices') or {}).get(fiat)
                premium = item.get('premium')

                if volume and sats_max:
                    # volume corresponds to the offer amount; use mid for ranges
                    btc = ((sats_min + sats_max) / 2) / 1e8 \
                        if sats_min != sats_max else sats_max / 1e8
                    price = float(volume) / btc
                elif premium is not None and refprice:
                    price = float(refprice) * (1 + float(premium) / 100)
                else:
                    continue

                methods = ', '.join(str(m) for m in mop.get(fiat, [])[:3]) or '?'

                min_vol = volume if volume else sats_min / 1e8 * price
                max_vol = volume if volume else sats_max / 1e8 * price
                # For range offers without an explicit volume, scale by min/max
                if not volume and sats_min != sats_max:
                    min_vol = sats_min / 1e8 * price
                    max_vol = sats_max / 1e8 * price

                alloffers.append({
                    'exchange': 'Peach',
                    'price': price,
                    'dif': (price / refprice - 1) * 100,
                    'min_btc': sats_min / 1e8,
                    'max_btc': sats_max / 1e8,
                    'min_amount': int(min_vol),
                    'max_amount': int(max_vol),
                    'method': methods,
                })
            except (TypeError, ValueError) as e:
                print(f'[Peach] skipped malformed offer: {e}')

        print(f'[Peach] {len(alloffers)} offers via {note}')
        return alloffers
