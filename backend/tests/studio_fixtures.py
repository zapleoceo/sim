"""Small synthetic containers; MP4 fixture tests structure, not codec playback."""
import io
import wave


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\0\0" * 16)
    return buffer.getvalue()


def mp4_bytes():
    def box(tag, payload):
        return (8 + len(payload)).to_bytes(4, "big") + tag + payload
    return box(b"ftyp", b"isom\0\0\0\0") + box(b"moov", b"synthetic") + box(b"mdat", b"synthetic")
