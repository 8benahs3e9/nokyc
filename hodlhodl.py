#!/usr/bin/env python3
# HodlHodl module per https://hodlhodl.com/api/docs
# GET https://hodlhodl.com/api/v1/offers, header "Version: HTTP/1.0"
# Field names verified live Sep 2026.

import requests

class HodlHodl:

    API = 'https://hodlhodl.com/api/v1/offers'

    @staticmethod
    def getOffers(fiat, direction, refprice, session):
        params = {
            'filters[side]': direction,
            'filters[currency_code]': fiat.upper(),
            'page': 1,
        }
        headers = {'Version': 'HTTP/1.0', 'Accept': 'application/json'}
        r = session.get(HodlHodl.API, params=params, headers=headers,
                        timeout=60)
        r.raise_for_status()
        payload = r.json()

        entries = None
        if isinstance(payload, dict):
            entries = payload.get('offers') or payload.get('data')
        elif isinstance(payload, list):
            entries = payload
        if not isinstance(entries, list):
            print('[HodlHodl] unexpected top-level schema')
            return []
        if not entries:
            return []

        alloffers = []
        for item in entries:
            try:
                raw_price = item.get('price')
                if isinstance(raw_price, dict):
                    raw_price = (raw_price.get('value')
                                 or raw_price.get('amount') or 0)
                price = float(raw_price or 0)
                if not price:
                    continue

                min_sats = float(item.get('min_amount_sats') or 0)
                max_sats = float(item.get('max_amount_sats')
                                 or item.get('balance_sats') or 0)
                min_fiat = float(item.get('min_amount') or 0)
                max_fiat = float(item.get('max_amount') or 0)

                names = []
                for p in (item.get('payment_method_instructions') or []):
                    if isinstance(p, dict):
                        inner = p.get('attributes', p)
                        name = (inner.get('payment_method_name')
                                or inner.get('payment_method_type')
                                or inner.get('name'))
                        names.append(str(name) if name else '?')
                    else:
                        names.append(str(p))
                method = ', '.join(names[:3]) if names else '?'

                alloffers.append({
                    'exchange': 'HodlHodl',
                    'price': price,
                    'dif': (price / refprice - 1) * 100,
                    'min_btc': min_sats / 1e8,
                    'max_btc': max_sats / 1e8,
                    'min_amount': int(min_fiat) if min_fiat > 1
                                  else int(min_sats * price / 1e8),
                    'max_amount': int(max_fiat) if max_fiat > 1
                                  else int(max_sats * price / 1e8),
                    'method': method,
                })
            except (TypeError, ValueError) as e:
                print(f'[HodlHodl] skipped malformed offer: {e}')
        return alloffers
