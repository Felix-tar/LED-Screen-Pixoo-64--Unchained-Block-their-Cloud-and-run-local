"""Example market plugin.

Demonstrates the interface without depending on any external API. Copy this,
add your own HTTP source (with timeouts + try/except), and set
dashboard.market.plugin to your module name.
"""
from __future__ import annotations


def get_quotes(cfg) -> list[dict]:
    # A real plugin would fetch from an API here, e.g.:
    #   import requests
    #   r = requests.get(url, timeout=4); data = r.json(); ...
    # Always guard network calls and return quickly.
    return [
        {"label": "DAX", "value": "+0.4%", "change": 0.4},
        {"label": "BTC", "value": "-1.2%", "change": -1.2},
        {"label": "NVDA", "value": "+2.1%", "change": 2.1},
        {"label": "GOLD", "value": "+0.1%", "change": 0.1},
    ]
