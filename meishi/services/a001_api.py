"""a001顧客システムAPI連携"""

import requests
from flask import current_app


A001_API_BASE = "https://a001.vpsk.net/api/v1"


def search_clients(keyword):
    """a001の顧客を会社名で検索"""
    api_key = current_app.config.get("A001_API_KEY", "")
    if not api_key:
        return []

    try:
        resp = requests.get(
            f"{A001_API_BASE}/clients",
            params={"search": keyword},
            headers={"X-API-Key": api_key},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        current_app.logger.warning(f"a001 API error: {e}")
        return []


def get_client_by_id(client_id):
    """a001の顧客をIDで取得"""
    api_key = current_app.config.get("A001_API_KEY", "")
    if not api_key:
        return None

    try:
        resp = requests.get(
            f"{A001_API_BASE}/clients/{client_id}",
            headers={"X-API-Key": api_key},
            timeout=5,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        current_app.logger.warning(f"a001 API error: {e}")
        return None
