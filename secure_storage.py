from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SERVICE_NAME = "YouTubeShortMaker"
ACCOUNT_NAME = "openrouter_api_key"


class SecureStorageError(RuntimeError):
    pass


def _keyring():
    try:
        import keyring
    except ImportError as exc:
        raise SecureStorageError(
            "安全なAPIキー保存機能がありません。setup.batをもう一度実行してください。"
        ) from exc
    return keyring


def get_api_key() -> str:
    try:
        return _keyring().get_password(SERVICE_NAME, ACCOUNT_NAME) or ""
    except Exception as exc:
        if isinstance(exc, SecureStorageError):
            raise
        raise SecureStorageError(f"Windows資格情報マネージャーを読み取れませんでした。\n{exc}") from exc


def set_api_key(api_key: str) -> None:
    if not api_key:
        delete_api_key()
        return
    try:
        _keyring().set_password(SERVICE_NAME, ACCOUNT_NAME, api_key)
    except Exception as exc:
        if isinstance(exc, SecureStorageError):
            raise
        raise SecureStorageError(f"Windows資格情報マネージャーへ保存できませんでした。\n{exc}") from exc


def delete_api_key() -> None:
    try:
        keyring = _keyring()
        if keyring.get_password(SERVICE_NAME, ACCOUNT_NAME):
            keyring.delete_password(SERVICE_NAME, ACCOUNT_NAME)
    except Exception as exc:
        if isinstance(exc, SecureStorageError):
            raise
        raise SecureStorageError(f"Windows資格情報マネージャーを更新できませんでした。\n{exc}") from exc


def write_public_settings(path: Path, data: dict[str, Any]) -> None:
    """Write non-secret settings atomically. API keys are never accepted here."""
    sanitized = {key: value for key, value in data.items() if key != "api_key"}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(sanitized, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_settings_and_migrate(path: Path) -> tuple[dict[str, Any], str]:
    """Move a legacy plaintext API key into Windows Credential Manager."""
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        data = {}
    plaintext_key = str(data.get("api_key") or "")
    if plaintext_key:
        # Only erase the plaintext after the secure write succeeds.
        set_api_key(plaintext_key)
        write_public_settings(path, data)
    return data, get_api_key()
