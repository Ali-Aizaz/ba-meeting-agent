"""Incoming Meet video is marked inactive. Audio and data sections stay."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from ba_agent.bot.google_meet.scripts import REJECT_VIDEO_SDP_JS
from ba_agent.bot.google_meet.video import reject_incoming_video_sdp

AUDIO_VIDEO = (
    "v=0\r\n"
    "o=- 0 0 IN IP4 127.0.0.1\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "a=sendrecv\r\n"
    "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
    "a=sendrecv\r\n"
    "a=rtpmap:111 opus/48000/2\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
    "a=recvonly\r\n"
    "a=rtpmap:96 VP8/90000\r\n"
    "m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n"
    "a=sendrecv\r\n"
)

CASES = [
    "",
    "m=video 9 UDP/TLS/RTP/SAVPF 96\r\na=sendrecv\r\n",
    "m=video 9 UDP/TLS/RTP/SAVPF 96\na=sendonly\n",
    "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendrecv\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n",
    "v=0\r\na=sendrecv\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n",
    AUDIO_VIDEO,
    "m=video 9 UDP/TLS/RTP/SAVPF 96\r\na=inactive\r\na=rtpmap:96 VP8/90000\r\n",
]


def test_video_becomes_inactive_and_audio_stays() -> None:
    got = reject_incoming_video_sdp(AUDIO_VIDEO)
    audio, video, application = got.split("m=")[1:]
    assert "a=sendrecv" in audio
    assert "a=recvonly" not in video
    assert "a=inactive" in video
    assert "a=rtpmap:96 VP8/90000" in video
    assert "a=sendrecv" in application
    assert "m=video 0" not in got


def test_missing_video_direction_is_added() -> None:
    got = reject_incoming_video_sdp(
        "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendrecv\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
        "a=rtpmap:96 VP8/90000\r\n"
    )
    video = got.split("m=video", 1)[1]
    assert "a=inactive" in video
    assert video.index("a=rtpmap:96 VP8/90000") < video.index("a=inactive")


def test_session_direction_still_applies_to_audio() -> None:
    got = reject_incoming_video_sdp(
        "v=0\r\na=sendrecv\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
    )
    assert got.startswith("v=0\r\na=sendrecv\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n")
    assert got.endswith("m=video 9 UDP/TLS/RTP/SAVPF 96\r\na=inactive\r\n")


def test_reject_is_idempotent() -> None:
    once = reject_incoming_video_sdp(AUDIO_VIDEO)
    assert reject_incoming_video_sdp(once) == once


def test_lf_sdp_stays_lf() -> None:
    got = reject_incoming_video_sdp("m=video 9 UDP/TLS/RTP/SAVPF 96\na=sendonly\n")
    assert "\r" not in got
    assert got.endswith("\n")
    assert "a=inactive\n" in got


def test_js_reject_matches_python() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    program = REJECT_VIDEO_SDP_JS + r"""
const fs = require("fs");
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(cases.map((sdp) => __baRejectVideoSdp(sdp))));
"""
    result = subprocess.run(
        [node, "-e", program],
        input=json.dumps(CASES).encode(),
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    got = json.loads(result.stdout.decode())
    assert got == [reject_incoming_video_sdp(sdp) for sdp in CASES]
