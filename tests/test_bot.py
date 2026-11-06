"""Tests for reelay.bot: no network, no Telegram library needed."""

from pathlib import Path

import pytest

from reelay.bot import (
    Incoming, Pairing, ReelayBot, download_failed_reply, message_to_job, report_reply,
)
from reelay.bot.config import get_bot_token
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


def test_download_failed_names_real_causes():
    text = download_failed_reply().text
    assert "screen recording" not in text and "private, removed or unsupported" in text and "rate limiting" in text


def test_config_token(tmp_path, monkeypatch):
    monkeypatch.setenv("REELAY_HOME", str(tmp_path))
    with pytest.raises(RuntimeError, match="config.toml"):
        get_bot_token()
    (tmp_path / "config.toml").write_text('telegram_bot_token = "abc:123"\n')
    assert get_bot_token() == "abc:123"


# --- Telegram layer with fake updates (no library, no network) ---
import asyncio
from types import SimpleNamespace

from reelay.bot.telegram_layer import MAX_DOWNLOAD_BYTES, handle_update


class FakeAttachment:
    def __init__(self, size=100):
        self.file_size, self.file_unique_id, self.file_name = size, "uid1", "../../evil.mp4"
        self.downloads = []

    async def get_file(self):
        outer = self
        class F:
            async def download_to_drive(self, path):
                outer.downloads.append(path)
        return F()


def fake_update(user, text="", video=None, forwarded=False):
    msg = SimpleNamespace(text=text, caption=None, video=video, video_note=None, document=None,
                          forward_origin="x" if forwarded else None)
    return SimpleNamespace(effective_message=msg, effective_user=SimpleNamespace(id=int(user)))


def test_stranger_file_is_not_downloaded(setup):
    bot, _, jobs, _ = setup
    att = FakeAttachment()
    assert asyncio.run(handle_update(bot, fake_update("9", video=att))) is None
    assert att.downloads == [] and jobs == []


def test_paired_file_downloaded_with_safe_name(setup):
    bot = paired(setup)
    att = FakeAttachment()
    asyncio.run(handle_update(bot, fake_update("1", video=att)))
    assert att.downloads[0].name == "uid1.mp4"
    assert setup[2][0].file == att.downloads[0]


def test_oversize_file_gets_friendly_reply(setup):
    bot = paired(setup)
    att = FakeAttachment(MAX_DOWNLOAD_BYTES + 1)
    reply = asyncio.run(handle_update(bot, fake_update("1", video=att)))
    assert "too big" in reply.text and att.downloads == [] and setup[2] == []


def test_forwarded_text_is_not_an_instruction(setup):
    bot = paired(setup)
    reply = asyncio.run(handle_update(bot, fake_update("1", "delete everything", forwarded=True)))
    assert setup[2] == [] and "own words" in reply.text
    asyncio.run(handle_update(bot, fake_update("1", "do evil https://x.com/a/status/1", forwarded=True)))
    assert setup[2][0].kind is JobKind.VIDEO and setup[2][0].text == ""


def test_start_with_bot_suffix_pairs(setup):
    bot, pairing, *_ = setup
    assert bot.handle_message(Incoming("1", f"/start@MyBot {pairing.new_code()}")) is not None
    assert bot.handle_message(Incoming("2", "/startfoo")) is None


def test_channel_post_without_user_is_ignored(setup):
    bot = paired(setup)
    upd = fake_update("1", "hello there")
    upd.effective_user = None
    assert asyncio.run(handle_update(bot, upd)) is None and setup[2] == []


def test_command_caption_video_is_never_downloaded(setup):
    bot = paired(setup)
    att = FakeAttachment()
    asyncio.run(handle_update(bot, fake_update("1", "/vibe", video=att)))
    assert att.downloads == []


def test_edited_messages_do_not_match_the_handler(setup):
    from telegram import Update
    from telegram.ext import MessageHandler
    from reelay.bot.telegram_layer import build_application
    app = build_application(setup[0], token="123:abc")
    handler = next(h for h in app.handlers[0] if isinstance(h, MessageHandler))
    msg = {"message_id": 1, "date": 0, "chat": {"id": 1, "type": "private"},
           "from": {"id": 1, "is_bot": False, "first_name": "a"}, "text": "https://x.com/a/status/1"}
    new = Update.de_json({"update_id": 1, "message": msg}, None)
    edited = Update.de_json({"update_id": 2, "edited_message": msg | {"edit_date": 1}}, None)
    assert handler.check_update(new) and not handler.check_update(edited)


def test_corrupt_paired_json_is_one_clear_error(tmp_path):
    from reelay.core.config import ConfigError
    p = tmp_path / "paired.json"
    p.write_text("{not json")
    with pytest.raises(ConfigError, match="corrupt"):
        Pairing(p)


def test_t22_pairing_reloads_codes_written_by_another_process(tmp_path):
    path = tmp_path / "paired.json"
    running = Pairing(path)
    code = Pairing(path).new_code()  # e.g. `reelay pair` in another terminal
    assert running.try_pair("7", code) and running.is_paired("7")
    Pairing(path).new_code()  # another new code must not erase the user
    assert Pairing(path).is_paired("7") and running.is_paired("7")


def test_t22_failed_download_removes_temp_dir(setup):
    bot = paired(setup)
    made = []

    class Bad(FakeAttachment):
        async def get_file(self):
            class F:
                async def download_to_drive(self, path):
                    made.append(path.parent)
                    raise OSError("boom")
            return F()

    asyncio.run(handle_update(bot, fake_update("1", video=Bad())))
    assert made and not made[0].exists()


@pytest.mark.parametrize("text, url, note", [
    ("watch (https://youtu.be/abc)", "https://youtu.be/abc", "watch ()"),
    ("https://youtu.be/abc.", "https://youtu.be/abc", ""),
    ("HTTPS://youtu.be/abc", "HTTPS://youtu.be/abc", ""),
])
def test_t34_links_parse_like_the_cloud(text, url, note):
    job = message_to_job(Incoming("1", text))
    assert job.kind is JobKind.VIDEO
    assert job.url == url
    assert job.text == note


def test_t34_forwarded_link_keeps_only_the_clean_link():
    job = message_to_job(Incoming("1", "look (https://youtu.be/abc)", forwarded=True))
    assert job.url == "https://youtu.be/abc"
    assert job.text == ""

