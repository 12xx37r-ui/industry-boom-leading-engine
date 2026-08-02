import json
from pathlib import Path

import yaml


def test_all_configured_sec_tickers_have_local_cik_mapping():
    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / "config" / "themes.yml").read_text(encoding="utf-8"))
    mapping = json.loads((root / "config" / "sec_cik_map.json").read_text(encoding="utf-8"))
    requested = {ticker for theme in config["themes"] if theme.get("sec_enabled", True) for ticker in theme.get("us_tickers", [])}
    missing = sorted(requested - set(mapping))
    assert not missing, f"Missing SEC CIK mappings: {missing}"
    assert all(len(str(mapping[ticker])) == 10 for ticker in requested)


def test_water_theme_uses_idex_corp_real_ticker():
    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / "config" / "themes.yml").read_text(encoding="utf-8"))
    water = next(theme for theme in config["themes"] if theme["id"] == "WATER_INFRA")
    assert "IEX" in water["us_tickers"]
    assert "IDEX" not in water["us_tickers"]
