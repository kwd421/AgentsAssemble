from __future__ import annotations

import unittest

from agentsassemble.providers.secrets import (
    ProviderSecretStore,
    ProviderSecretStoreUnavailable,
)


class FailingKeyring:
    def get_password(self, service_name: str, username: str) -> str | None:
        del service_name, username
        raise OSError("keyring locked")

    def set_password(self, service_name: str, username: str, password: str) -> None:
        del service_name, username, password
        raise OSError("keyring locked")

    def delete_password(self, service_name: str, username: str) -> None:
        del service_name, username
        raise OSError("keyring locked")


class ProviderSecretStoreFailureTests(unittest.TestCase):
    def test_keyring_read_failure_does_not_fall_back_to_a_different_environment_key(self):
        store = ProviderSecretStore(
            backend=FailingKeyring(),
            environment={"DEEPSEEK_API_KEY": "environment-secret"},
        )

        with self.assertRaisesRegex(ProviderSecretStoreUnavailable, "secure_store_unavailable"):
            store.get("deepseek")
        with self.assertRaisesRegex(ProviderSecretStoreUnavailable, "secure_store_unavailable"):
            store.status("deepseek")

    def test_keyring_delete_failure_is_reported_instead_of_returning_success(self):
        store = ProviderSecretStore(backend=FailingKeyring(), environment={})

        with self.assertRaisesRegex(ProviderSecretStoreUnavailable, "secure_store_unavailable"):
            store.delete("deepseek")


if __name__ == "__main__":
    unittest.main()
