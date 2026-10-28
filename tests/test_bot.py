"""Tests for reelay.bot: no network, no Telegram library needed."""

from pathlib import Path

import pytest

from reelay.bot import (
    Incoming, Pairing, ReelayBot, download_failed_reply, message_to_job, report_reply,
)
from reelay.bot.config import get_bot_token, load_config
from reelay.bot.pairing import CODE_TTL_S, MAX_FAILED_ATTEMPTS
from reelay.core.types import JobKind


@pytest.fixture
def setup(tmp_path):
    jobs, decisions = [], []
    pairing = Pairing(tmp_path / "paired.json")
    bot = ReelayBot(pairing, jobs.append, lambda *a: decisions.append(a))
    return bot, pairing, jobs, decisions


def paired(setup, user="1"):
    bot, pairing, *_ = setup
    assert bot.handle_message(Incoming(user, f"/start {pairing.new_code()}"))
    return bot


def test_stranger_is_ignored(setup):
    bot, _, jobs, _ = setup
    assert bot.handle_message(Incoming("9", "https://youtu.be/x")) is None
    assert bot.handle_message(Incoming("9", "/start")) is None
    assert jobs == []


def test_pairing_code_works_once(setup):
    bot, pairing, jobs, _ = setup
    code = pairing.new_code()
    assert bot.handle_message(Incoming("1", f"/start {code}")) is not None
    assert pairing.is_paired("1")
    assert bot.handle_message(Incoming("2", f"/start {code}")) is None
    assert not pairing.is_paired("2")


def test_wrong_code_ignored_and_locks_out(setup):
    bot, pairing, *_ = setup
    code = pairing.new_code()
    for _ in range(MAX_FAILED_ATTEMPTS):
        assert bot.handle_message(Incoming("2", "/start WRONG123")) is None
    assert bot.handle_message(Incoming("2", f"/start {code}")) is None


def test_code_expires(tmp_path):
    now = [1000.0]
    pairing = Pairing(tmp_path / "p.json", clock=lambda: now[0])
    code = pairing.new_code()
    now[0] += CODE_TTL_S + 1
    assert not pairing.try_pair("1", code)


def test_pairing_persists(tmp_path):
    path = tmp_path / "p.json"
    first = Pairing(path)
    assert first.try_pair("1", first.new_code())
    assert Pairing(path).is_paired("1")


def test_link_becomes_video_job(setup):
    bot = paired(setup)
    job = message_to_job(Incoming("1", "check this https://tiktok.com/@a/video/1 please"))
    assert job.kind is JobKind.VIDEO
    assert job.url == "https://tiktok.com/@a/video/1"
    assert job.text == "check this  please"
    assert bot.handle_message(Incoming("1", "https://x.com/a/status/2")) is not None
    assert setup[2][0].url == "https://x.com/a/status/2"


def test_file_becomes_video_job():
    job = message_to_job(Incoming("1", "", Path("/tmp/a.mp4")))
    assert job.kind is JobKind.VIDEO and job.file == Path("/tmp/a.mp4")


def test_plain_text_is_instruction(setup):
    bot = paired(setup)
    bot.handle_message(Incoming("1", "use opus on my website"))
    job = setup[2][0]
    assert job.kind is JobKind.INSTRUCTION and job.text == "use opus on my website"


def test_adopt_and_skip_callbacks(setup):
    bot = paired(setup)
    reply = report_reply("r1", "Report")
    assert reply.buttons == [("Adopt", "adopt:r1"), ("Skip", "skip:r1")]
    bot.handle_button("1", "adopt:r1")
    bot.handle_button("1", "skip:r2")
    assert setup[3] == [("1", "r1", "adopt"), ("1", "r2", "skip")]


def test_buttons_from_strangers_and_junk_ignored(setup):
    bot = paired(setup)
    assert bot.handle_button("9", "adopt:r1") is None
    assert bot.handle_button("1", "delete:r1") is None
    assert bot.handle_button("1", "adopt:") is None
    assert setup[3] == []


def test_download_failed_asks_for_screen_recording():
    assert "screen recording" in download_failed_reply().text


def test_config_token(tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("# comment\nTELEGRAM_BOT_TOKEN='abc:123'\n")
    assert load_config(cfg) == {"TELEGRAM_BOT_TOKEN": "abc:123"}
    assert get_bot_token(cfg) == "abc:123"
    with pytest.raises(RuntimeError):
        get_bot_token(tmp_path / "missing")
