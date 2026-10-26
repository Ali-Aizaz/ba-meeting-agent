"""Mark video m-lines inactive without touching audio or data."""

from __future__ import annotations

_DIRECTIONS = frozenset({"a=sendrecv", "a=sendonly", "a=recvonly", "a=inactive"})


def reject_incoming_video_sdp(sdp: str) -> str:
    """Keep each video section in the bundle, and set its direction to inactive.

    Port 0 would reject the section and can drop bundled audio with it. An inactive
    video line stays in the bundle, so the browser does not decode cameras.
    """
    if sdp == "":
        return ""
    newline = "\r\n" if "\r\n" in sdp else "\n"
    ended_with_newline = sdp.endswith("\n")
    out: list[str] = []
    in_video = False
    saw_direction = False

    def finish_section() -> None:
        nonlocal saw_direction
        if in_video and not saw_direction:
            out.append("a=inactive")
        saw_direction = False

    for line in sdp.splitlines():
        if line.startswith("m="):
            finish_section()
            in_video = line.startswith("m=video")
            out.append(line)
            continue
        if in_video and line.strip() in _DIRECTIONS:
            if not saw_direction:
                out.append("a=inactive")
                saw_direction = True
            continue
        out.append(line)
    finish_section()
    body = newline.join(out)
    if ended_with_newline:
        body += newline
    return body
