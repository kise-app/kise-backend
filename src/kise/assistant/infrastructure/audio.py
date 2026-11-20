"""How long a voice note is, read from the file itself, without decoding the audio.

MP4 / M4A (what the app records): the ``mvhd`` box holds a timescale and a duration. WAV: the
``fmt `` chunk's byte rate and the ``data`` chunk's size. Anything else, or a file that cannot be
read, answers None, and the service falls back to a size limit.
"""

from __future__ import annotations

import struct

from kise.assistant.application.ports import AudioInspector

# Boxes that can contain ``moov`` / ``mvhd`` on the way down.
_CONTAINERS = {b"moov", b"trak", b"mdia", b"udta"}


def _mp4_duration(data: bytes) -> float | None:
    def walk(start: int, end: int) -> float | None:
        offset = start
        while offset + 8 <= end:
            size, kind = struct.unpack(">I4s", data[offset : offset + 8])
            header = 8
            if size == 1:  # 64-bit size follows
                if offset + 16 > end:
                    return None
                size = struct.unpack(">Q", data[offset + 8 : offset + 16])[0]
                header = 16
            elif size == 0:  # to the end of the file
                size = end - offset
            if size < header or offset + size > end:
                return None
            body = offset + header
            if kind == b"mvhd":
                version = data[body]
                if version == 1:
                    timescale, duration = struct.unpack(">IQ", data[body + 20 : body + 32])
                else:
                    timescale, duration = struct.unpack(">II", data[body + 12 : body + 20])
                return duration / timescale if timescale else None
            if kind in _CONTAINERS:
                found = walk(body, offset + size)
                if found is not None:
                    return found
            offset += size
        return None

    try:
        return walk(0, len(data))
    except (struct.error, IndexError):
        return None


def _wav_duration(data: bytes) -> float | None:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    offset, byte_rate = 12, None
    try:
        while offset + 8 <= len(data):
            kind, size = struct.unpack("<4sI", data[offset : offset + 8])
            if kind == b"fmt ":
                byte_rate = struct.unpack("<I", data[offset + 16 : offset + 20])[0]
            elif kind == b"data":
                return size / byte_rate if byte_rate else None
            offset += 8 + size + (size & 1)
    except struct.error:
        return None
    return None


class HeaderAudioInspector(AudioInspector):
    def duration_seconds(self, data: bytes, mime_type: str) -> float | None:
        if mime_type == "audio/mp4":
            return _mp4_duration(data)
        if mime_type == "audio/wav":
            return _wav_duration(data)
        return None
