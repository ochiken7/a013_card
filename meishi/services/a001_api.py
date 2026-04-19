"""a001顧客システムAPI連携"""

import requests
from flask import current_app


A001_API_BASE = "https://a001.vpsk.net/api/v1"


def _request(method, path, **kwargs):
    """a001 APIへの共通リクエスト処理（エラー時は None / [] を返す）"""
    api_key = current_app.config.get("A001_API_KEY", "")
    if not api_key:
        current_app.logger.warning("A001_API_KEY が未設定です")
        return None

    try:
        resp = requests.request(
            method,
            f"{A001_API_BASE}{path}",
            headers={"X-API-Key": api_key},
            timeout=5,
            **kwargs,
        )
        if resp.status_code == 401:
            current_app.logger.error("a001 API 認証失敗 - APIキーを確認してください")
            return None
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.Timeout:
        current_app.logger.warning(f"a001 APIタイムアウト: {path}")
        return None
    except requests.ConnectionError:
        current_app.logger.warning(f"a001 API接続失敗: {path}")
        return None
    except Exception as e:
        current_app.logger.warning(f"a001 API error: {e}")
        return None


def search_clients(keyword):
    """a001の顧客を会社名で検索"""
    result = _request("GET", "/clients", params={"search": keyword})
    return result if isinstance(result, list) else []


def get_client_by_id(client_id):
    """a001の顧客をIDで取得"""
    result = _request("GET", f"/clients/{client_id}")
    return result if isinstance(result, dict) else None
