"""Reading a voice note's length from its header, and the plan's length rule."""

from __future__ import annotations

import struct

import pytest

from kise.assistant.domain.usage import voice_within
from kise.assistant.infrastructure.audio import HeaderAudioInspector
from tests.api.test_plans_and_admin_portal import m4a

inspector = HeaderAudioInspector()


def wav(seconds: float, byte_rate: int = 32_000) -> bytes:
    data_size = int(seconds * byte_rate)
    fmt = struct.pack("<HHIIHH", 1, 1, 16_000, byte_rate, 2, 16)
    body = b"WAVE" + struct.pack("<4sI", b"fmt ", len(fmt)) + fmt
    body += struct.pack("<4sI", b"data", data_size) + b"\x00" * 16
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_reads_an_m4a_length():
    assert inspector.duration_seconds(m4a(42.5), "audio/mp4") == pytest.approx(42.5)


def test_reads_a_wav_length_from_its_header_even_if_cut_short():
    assert inspector.duration_seconds(wav(12), "audio/wav") == pytest.approx(12)


@pytest.mark.parametrize(
    ("data", "mime"),
    [
        (b"", "audio/mp4"),
        (b"\x00\x00\x00\x10junk", "audio/mp4"),
        (b"RIFF", "audio/wav"),
        (b"anything", "audio/ogg"),
    ],
)
def test_unreadable_or_other_formats_have_no_length(data, mime):
    assert inspector.duration_seconds(data, mime) is None


def test_the_length_rule_allows_a_little_over_and_falls_back_to_size():
    assert voice_within(60, duration=61.5, size=10**9)
    assert not voice_within(60, duration=63, size=10)
    assert voice_within(60, duration=None, size=500_000)
    assert not voice_within(60, duration=None, size=5_000_000)
