from collections import deque
from dataclasses import dataclass, field


@dataclass
class BufferedMessage:
    timestamp: str
    user_id: str
    username: str
    text: str
    has_image: bool = False
    image_urls: list[str] = field(default_factory=list)
    thread_ts: str | None = None


class MessageBuffer:
    def __init__(self, max_size: int = 50):
        self.messages: deque[BufferedMessage] = deque(maxlen=max_size)

    def add(self, msg: BufferedMessage) -> None:
        self.messages.append(msg)

    def recent(self, n: int = 10) -> list[BufferedMessage]:
        return list(self.messages)[-n:]

    def full_context(self) -> list[BufferedMessage]:
        return list(self.messages)


if __name__ == "__main__":
    buf = MessageBuffer(max_size=3)
    buf.add(BufferedMessage("1", "U1", "terry", "hey"))
    buf.add(BufferedMessage("2", "U2", "tom", "what's up"))
    buf.add(BufferedMessage("3", "U3", "nick", "not much"))
    buf.add(BufferedMessage("4", "U1", "terry", "fourth message"))

    assert len(buf.messages) == 3, "maxlen eviction failed"
    assert buf.messages[0].text == "what's up", "oldest should be evicted"
    assert len(buf.recent(2)) == 2, "recent(2) should return 2"
    assert len(buf.full_context()) == 3, "full_context should return all"

    print("all checks passed")
