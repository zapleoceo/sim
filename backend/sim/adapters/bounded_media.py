"""A bounded sink: reject a chunk before allocating a larger media buffer."""
import io


class BoundedMediaBuffer(io.BytesIO):
    def __init__(self, max_bytes: int) -> None:
        super().__init__()
        self.max_bytes = max_bytes

    def write(self, data: bytes) -> int:
        if self.tell() + len(data) > self.max_bytes:
            raise ValueError("media download limit exceeded")
        return super().write(data)
