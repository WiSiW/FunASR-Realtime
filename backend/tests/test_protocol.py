from __future__ import annotations

import json

import numpy as np
import pytest

from backend.app.api.protocol import (
    ProtocolError,
    decode_pcm_s16le,
    event,
    parse_client_message,
)


def test_parse_ping_message() -> None:
    message = parse_client_message('{"type":"ping","request_id":"abc"}')

    assert message.type == "ping"
    assert message.request_id == "abc"


def test_reject_non_object_message() -> None:
    with pytest.raises(ProtocolError):
        parse_client_message("[]")


def test_decode_pcm_s16le() -> None:
    source = np.array([-32768, -1, 0, 16384, 32767], dtype="<i2")

    decoded = decode_pcm_s16le(source.tobytes())

    assert decoded.dtype == np.float32
    assert decoded[0] == pytest.approx(-1.0)
    assert decoded[-1] == pytest.approx(32767 / 32768)


def test_reject_odd_pcm_length() -> None:
    with pytest.raises(ProtocolError):
        decode_pcm_s16le(b"\x00")


def test_event_serializes_unicode_without_escaping() -> None:
    payload = json.loads(event("status", data={"message": "正在聆听"}))

    assert payload["type"] == "status"
    assert payload["data"]["message"] == "正在聆听"
