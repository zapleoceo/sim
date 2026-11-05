# ruff: noqa: S105, S106
import inspect

import pytest

from sim.connectors.registry import REGISTRY, connector_for
from sim.connectors.spec import Connector
from sim.domain.enums import ChannelKind

MEMBERS = ["kind", "label", "polling", "connect", "verify", "state", "disconnect",
           "build_port", "verify_webhook", "parse_webhook"]


def test_every_kind_registered():
    assert set(REGISTRY) == set(ChannelKind)


@pytest.mark.parametrize("kind", list(ChannelKind))
def test_entry_satisfies_protocol(kind):
    c = REGISTRY[kind]
    assert c.kind is kind and isinstance(c.label, str) and isinstance(c.polling, bool)
    for name in MEMBERS:
        assert hasattr(c, name), name
    for name in ("connect", "verify", "state", "disconnect"):
        assert inspect.iscoroutinefunction(getattr(c, name)), name
    # signatures match the Protocol parameter names
    for name in ("connect", "verify", "state", "disconnect", "build_port",
                 "verify_webhook", "parse_webhook"):
        assert list(inspect.signature(getattr(c, name)).parameters) == list(
            inspect.signature(getattr(Connector, name)).parameters)[1:], name
    assert connector_for(kind) is c and connector_for(kind.value) is c


def test_polling_flags():
    assert REGISTRY[ChannelKind.INSTAGRAM].polling is True
    assert REGISTRY[ChannelKind.TELEGRAM].polling is False
    assert REGISTRY[ChannelKind.WHATSAPP].polling is False


def test_unknown_kind_is_value_error():
    with pytest.raises(ValueError, match="No connector"):
        connector_for("myspace")
