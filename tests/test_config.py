"""Tests for Serializd provider configuration."""

import msgspec
import pytest

from anibridge.providers.list.serializd.config import SerializdListProviderConfig


def test_config_parses_required_fields() -> None:
    config = msgspec.convert(
        {"email": "user@example.com", "password": "hunter2"},
        type=SerializdListProviderConfig,
    )
    assert config.email == "user@example.com"
    assert config.password == "hunter2"


def test_config_requires_email() -> None:
    with pytest.raises(msgspec.ValidationError):
        msgspec.convert({"password": "hunter2"}, type=SerializdListProviderConfig)


def test_config_requires_password() -> None:
    with pytest.raises(msgspec.ValidationError):
        msgspec.convert({"email": "user@example.com"}, type=SerializdListProviderConfig)
