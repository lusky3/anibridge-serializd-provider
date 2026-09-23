"""Serializd provider configuration."""

from typing import Annotated

import msgspec


class SerializdListProviderConfig(msgspec.Struct, kw_only=True):
    """Configuration for the Serializd list provider."""

    email: Annotated[
        str,
        msgspec.Meta(description="Serializd account email address."),
    ]
    password: Annotated[
        str,
        msgspec.Meta(description="Serializd account password."),
    ]
