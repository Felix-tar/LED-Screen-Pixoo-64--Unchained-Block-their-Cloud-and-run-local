"""Optional market-data plugins.

Set dashboard.market.provider: "plugin" and dashboard.market.plugin: "<name>"
in config.yaml. A plugin module must expose:

    def get_quotes(cfg) -> list[dict]:
        # return [{"label": "DAX", "value": "+0.4%", "change": 0.4}, ...]

`change` (float) decides the arrow/colour; if omitted it is parsed from `value`.
Keep network calls robust and fast; exceptions are caught by dashboard.market
and the previous good data is reused so the dashboard never stalls.
"""
