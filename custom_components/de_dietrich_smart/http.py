"""Fabrique des clients HTTP du client BDR.

Les clients de `create_async_httpx_client` ne se ferment pas (`aclose` et `async with` neutralisés) :
le client BDR en crée un par connexion et les ferme lui-même, il lui faut donc des clients httpx
ordinaires, avec le contexte SSL de Home Assistant (déjà chargé, sans lecture bloquante).
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

import httpx
from homeassistant.core import HomeAssistant
from homeassistant.util.ssl import client_context


def client_factory(hass: HomeAssistant) -> Callable[..., httpx.AsyncClient]:
    return partial(httpx.AsyncClient, verify=client_context())
