"""Turn saved Meet chat HTML, or observer nodes, into chat messages."""

from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser

from ba_agent.models import ChatMessage

UTC = timezone.utc


class _ChatHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.nodes: list[dict[str, str]] = []
        self._active: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        if self._active is not None:
            self._active["depth"] += 1
            return
        if "data-chat-message" in attr:
            self._active = {
                "sender": attr.get("data-sender", ""),
                "text": [],
                "depth": 1,
            }

    def handle_endtag(self, tag: str) -> None:
        if self._active is None:
            return
        self._active["depth"] -= 1
        if self._active["depth"] <= 0:
            self.nodes.append(
                {
                    "sender": self._active["sender"],
                    "text": " ".join("".join(self._active["text"]).split()),
                }
            )
            self._active = None

    def handle_data(self, data: str) -> None:
        if self._active is not None:
            self._active["text"].append(data)


def parse_chat_html(
    html: str,
    bot_name: str = "TechSol BA",
    wall_time: datetime | None = None,
) -> list[ChatMessage]:
    parser = _ChatHTMLParser()
    parser.feed(html)
    return messages_from_nodes(parser.nodes, bot_name=bot_name, wall_time=wall_time)


class ChatReader:
    """Carry the last sender forward across drained batches."""

    def __init__(self, bot_name: str = "TechSol BA") -> None:
        self.bot_name = bot_name
        self.last_sender = ""

    def consume(
        self,
        nodes: list[dict],
        wall_time: datetime | None = None,
    ) -> list[ChatMessage]:
        self_names = {"you", self.bot_name.strip().lower(), "techsol ba", "ba assistant"}
        messages = messages_from_nodes(
            nodes,
            bot_name=self.bot_name,
            wall_time=wall_time,
            last_sender=self.last_sender,
        )
        if messages:
            last = messages[-1]
            self.last_sender = "" if last.is_self else last.sender
        elif nodes:
            sender = (nodes[-1].get("sender") or "").strip()
            if sender:
                self.last_sender = "" if sender.lower() in self_names else sender
        return messages


def messages_from_nodes(
    nodes: list[dict],
    bot_name: str = "TechSol BA",
    wall_time: datetime | None = None,
    last_sender: str = "",
) -> list[ChatMessage]:
    now = wall_time or datetime.now(UTC)
    self_names = {"you", bot_name.strip().lower(), "techsol ba", "ba assistant"}
    if last_sender.strip().lower() in self_names:
        last_sender = ""
    messages: list[ChatMessage] = []
    for node in nodes:
        raw_sender = (node.get("sender") or "").strip()
        node_is_self = bool(node.get("is_self"))
        sender = raw_sender or ("" if node_is_self else last_sender)
        text = " ".join((node.get("text") or "").split())
        is_self = node_is_self or (sender.strip().lower() in self_names)
        if is_self:
            last_sender = ""
        elif sender:
            last_sender = sender
        if not text:
            continue
        messages.append(
            ChatMessage(
                sender=sender or ("You" if is_self else "Unknown speaker"),
                text=text,
                wall_time=now,
                is_self=is_self,
            )
        )
    return messages
