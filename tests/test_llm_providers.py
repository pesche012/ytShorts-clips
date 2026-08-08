import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm_providers import OpenRouterProvider, get_provider


def fake_response(status_code, payload):
    response = Mock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.json.return_value = payload
    response.text = json.dumps(payload)
    return response


class OpenRouterProviderTests(unittest.TestCase):
    def test_provider_registry(self):
        provider = get_provider("openrouter")
        self.assertEqual(provider.default_model, "openrouter/auto")

    @patch("requests.get")
    def test_list_models(self, request_get):
        request_get.return_value = fake_response(
            200,
            {
                "data": [
                    {
                        "id": "example/model",
                        "name": "Example Model",
                        "context_length": 128000,
                        "architecture": {"output_modalities": ["text"]},
                        "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                        "supported_parameters": ["response_format"],
                    }
                ]
            },
        )
        models = OpenRouterProvider().list_models("sk-or-test")
        self.assertEqual(models[0].id, "openrouter/auto")
        self.assertEqual(models[1].id, "example/model")
        self.assertTrue(models[1].supports_structured_output)

    @patch("requests.post")
    def test_json_schema_falls_back_for_unsupported_model(self, request_post):
        request_post.side_effect = [
            fake_response(400, {"error": {"message": "response_format not supported"}}),
            fake_response(
                200,
                {"choices": [{"message": {"content": '{"highlights": []}'}, "finish_reason": "stop"}]},
            ),
        ]
        content = OpenRouterProvider().complete_json(
            "sk-or-test", "example/model", "system", "user"
        )
        self.assertEqual(content, '{"highlights": []}')
        self.assertEqual(request_post.call_count, 2)
        first_payload = request_post.call_args_list[0].kwargs["json"]
        second_payload = request_post.call_args_list[1].kwargs["json"]
        self.assertIn("response_format", first_payload)
        self.assertNotIn("response_format", second_payload)


if __name__ == "__main__":
    unittest.main()
