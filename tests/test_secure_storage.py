import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from secure_storage import load_settings_and_migrate, write_public_settings


class MemoryKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, password):
        self.values[(service, account)] = password

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


class SecureStorageTests(unittest.TestCase):
    def test_public_settings_never_write_api_key(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "settings.json"
            write_public_settings(path, {"llm_model": "openrouter/auto", "api_key": "not-for-disk"})
            data = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("api_key", data)

    def test_plaintext_key_is_migrated_then_removed(self):
        memory = MemoryKeyring()
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "settings.json"
            path.write_text(
                json.dumps({"llm_model": "openrouter/auto", "api_key": "legacy-secret"}),
                encoding="utf-8",
            )
            with patch("secure_storage._keyring", return_value=memory):
                _, migrated_key = load_settings_and_migrate(path)
            data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(migrated_key, "legacy-secret")
        self.assertNotIn("api_key", data)


if __name__ == "__main__":
    unittest.main()
