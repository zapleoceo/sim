"""Bounded container/signature checks, not a codec decoder or malware sandbox."""
import io
import struct
import wave


def valid_artifact(data: bytes, mime: str, kind: str) -> bool:
    if kind == "speech":
        if mime != "audio/wav" or len(data) > 2 * 1024 * 1024 or len(data) < 44:
            return False
        if data[:4] != b"RIFF" or data[8:12] != b"WAVE" or int.from_bytes(data[4:8], "little") + 8 != len(data):
            return False
        try:
            with wave.open(io.BytesIO(data)) as wav:
                expected = wav.getnframes() * wav.getnchannels() * wav.getsampwidth()
                return (wav.getnchannels() in (1, 2) and wav.getsampwidth() == 2
                        and 8000 <= wav.getframerate() <= 48000 and expected > 0
                        and len(wav.readframes(wav.getnframes())) == expected)
        except (wave.Error, EOFError, struct.error):
            return False
    if mime != "video/mp4" or not 24 <= len(data) <= 20 * 1024 * 1024:
        return False
    position, count, boxes = 0, 0, set()
    while position < len(data):
        count += 1
        if count > 1024:
            return False
        if len(data) - position < 8:
            return False
        size = int.from_bytes(data[position:position + 4], "big")
        tag = data[position + 4:position + 8]
        if size < 8 or position + size > len(data):
            return False  # refuse extended/zero-size boxes, truncated and overflowing files
        if position == 0:
            if tag != b"ftyp" or size < 16 or data[8:12] not in {b"isom", b"iso2", b"mp41", b"mp42", b"avc1"}:
                return False
        if tag in {b"moov", b"mdat"} and size <= 8:
            return False
        boxes.add(tag)
        position += size
    return {b"ftyp", b"moov", b"mdat"}.issubset(boxes)
