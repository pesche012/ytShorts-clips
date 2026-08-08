from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class LLMProviderError(RuntimeError):
    """A provider error safe to display in the desktop app."""


@dataclass(frozen=True)
class LLMModel:
    id: str
    name: str
    context_length: int = 0
    prompt_price: float = 0.0
    completion_price: float = 0.0
    supports_structured_output: bool = False


HIGHLIGHTS_SCHEMA: dict[str, Any] = {
    "name": "youtube_highlights",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "highlights": {
                "type": "array",
                "minItems": 7,
                "maxItems": 7,
                "items": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "number"},
                        "end": {"type": "number"},
                        "title": {"type": "string"},
                        "reason": {"type": "string"},
                        "score": {"type": "number"},
                    },
                    "required": ["start", "end", "title", "reason", "score"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["highlights"],
        "additionalProperties": False,
    },
}


class LLMProvider(ABC):
    provider_id: str
    display_name: str
    api_key_url: str
    default_model: str

    @abstractmethod
    def list_models(self, api_key: str) -> list[LLMModel]:
        raise NotImplementedError

    @abstractmethod
    def complete_json(
        self,
        api_key: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        timeout: int = 300,
    ) -> str:
        raise NotImplementedError


class OpenRouterProvider(LLMProvider):
    provider_id = "openrouter"
    display_name = "OpenRouter"
    api_key_url = "https://openrouter.ai/settings/keys"
    default_model = "openrouter/auto"
    api_base = "https://openrouter.ai/api/v1"

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        if not api_key.strip():
            raise LLMProviderError("OpenRouter APIキーを入力してください。")
        return {
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
            "X-OpenRouter-Title": "YouTube Shorts Auto Maker",
        }

    @staticmethod
    def _error_message(response: Any) -> str:
        try:
            body = response.json()
            detail = body.get("error", {}).get("message") or body.get("message")
        except Exception:
            detail = None
        if response.status_code == 401:
            return "OpenRouter APIキーが正しくありません。"
        if response.status_code == 402:
            return "OpenRouterのクレジット残高を確認してください。"
        if response.status_code == 429:
            return "OpenRouterまたは選択モデルの利用上限に達しました。少し待つか別モデルを選んでください。"
        return f"OpenRouterエラー（{response.status_code}）: {detail or response.text[:300]}"

    def list_models(self, api_key: str) -> list[LLMModel]:
        import requests

        try:
            response = requests.get(
                f"{self.api_base}/models",
                headers=self._headers(api_key),
                params={"output_modalities": "text", "sort": "most-popular"},
                timeout=(15, 60),
            )
        except requests.RequestException as exc:
            raise LLMProviderError(f"OpenRouterのモデル一覧を取得できませんでした。\n{exc}") from exc
        if not response.ok:
            raise LLMProviderError(self._error_message(response))
        result: list[LLMModel] = []
        for item in response.json().get("data", []):
            model_id = str(item.get("id") or "").strip()
            if not model_id:
                continue
            architecture = item.get("architecture") or {}
            outputs = architecture.get("output_modalities") or []
            if outputs and "text" not in outputs:
                continue
            pricing = item.get("pricing") or {}
            supported = item.get("supported_parameters") or []
            try:
                prompt_price = float(pricing.get("prompt") or 0)
                completion_price = float(pricing.get("completion") or 0)
            except (TypeError, ValueError):
                prompt_price = completion_price = 0.0
            result.append(
                LLMModel(
                    id=model_id,
                    name=str(item.get("name") or model_id),
                    context_length=int(item.get("context_length") or 0),
                    prompt_price=prompt_price,
                    completion_price=completion_price,
                    supports_structured_output="structured_outputs" in supported
                    or "response_format" in supported,
                )
            )
        if not any(model.id == self.default_model for model in result):
            result.insert(0, LLMModel(self.default_model, "Auto Router"))
        return result

    def complete_json(
        self,
        api_key: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        timeout: int = 300,
    ) -> str:
        import requests

        base_payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 4000,
            "stream": False,
        }
        last_response = None
        # Some OpenRouter models support strict JSON Schema while others do not.
        # Try strict output first, then transparently fall back to prompt-only JSON.
        for use_schema in (True, False):
            payload = dict(base_payload)
            if use_schema:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": HIGHLIGHTS_SCHEMA,
                }
            try:
                response = requests.post(
                    f"{self.api_base}/chat/completions",
                    headers=self._headers(api_key),
                    json=payload,
                    timeout=(20, timeout),
                )
            except requests.RequestException as exc:
                raise LLMProviderError(f"OpenRouterへ接続できませんでした。\n{exc}") from exc
            last_response = response
            if response.ok:
                body = response.json()
                choice = (body.get("choices") or [{}])[0]
                if choice.get("finish_reason") == "error" or choice.get("error"):
                    detail = (choice.get("error") or {}).get("message", "モデルの処理中にエラーが発生しました")
                    raise LLMProviderError(f"OpenRouterモデルエラー: {detail}")
                content = (choice.get("message") or {}).get("content")
                if isinstance(content, list):
                    content = "".join(
                        str(part.get("text", "")) if isinstance(part, dict) else str(part)
                        for part in content
                    )
                if content:
                    return str(content)
                raise LLMProviderError("選択したモデルから空の応答が返されました。")
            if not (use_schema and response.status_code in {400, 404, 422}):
                break
        assert last_response is not None
        raise LLMProviderError(self._error_message(last_response))


_PROVIDERS: dict[str, LLMProvider] = {
    OpenRouterProvider.provider_id: OpenRouterProvider(),
}


def get_provider(provider_id: str) -> LLMProvider:
    try:
        return _PROVIDERS[provider_id]
    except KeyError as exc:
        raise LLMProviderError(f"未対応のLLM接続先です: {provider_id}") from exc


def available_providers() -> list[LLMProvider]:
    return list(_PROVIDERS.values())
