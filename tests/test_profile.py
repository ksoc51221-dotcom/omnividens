"""Profile tests: quiz logic, store permissions, commands, style. No network."""

import stat

import pytest

from reelay.core.types import Profile
from reelay.profile import (QuizState, answer_button, answer_text, build_profile, delete, describe,
                            finish_quiz, handle_command, handle_vibe_button, learn, load,
                            next_step, prompt_line, save, set_vibe, start, style)
from reelay.profile import quiz, store, tone as style_mod


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REELAY_HOME", str(tmp_path))
    return tmp_path


def run_quiz(night="hype", goals="stop fake news, find tools"):
    state = QuizState()
    for qid, key in (("night", night), ("claims", "careful"), ("detail", "short")):
        assert answer_button(state, f"quiz:{qid}:{key}")
    if goals is None:
        assert answer_button(state, quiz.SKIP_GOALS)
    else:
        assert answer_text(state, goals)
    return state


def test_start_has_buttons_with_callbacks():
    step = start()
    assert 2 <= len(step.buttons) <= 3
    assert all(data.startswith("quiz:night:") for _, data in step.buttons)


def test_quiz_walks_through_questions_then_goals_then_ends():
    state = QuizState()
    seen = []
    for qid, key in (("night", "chill"), ("claims", "social"), ("detail", "deep")):
        seen.append(next_step(state).buttons[0][1].split(":")[1])
        answer_button(state, f"quiz:{qid}:{key}")
    assert seen == ["night", "claims", "detail"]
    goals = next_step(state)
    assert goals.wants_text and goals.buttons == [("Skip", quiz.SKIP_GOALS)]
    answer_text(state, "learn")
    assert next_step(state) is None


def test_build_profile_from_answers():
    p = build_profile("42", run_quiz())
    assert p.vibe == "hype" and p.goals == ["stop fake news", "find tools"]
    assert p.traits == {"claims": "careful", "detail": "short"}


def test_skip_goals_gives_empty_goals():
    p = build_profile("42", run_quiz(goals=None))
    assert p.goals == []


def test_invalid_and_stale_buttons_are_ignored():
    state = QuizState()
    for bad in ("quiz:night:nope", "quiz:nope:chill", "quiz:night", "other", "quiz:a:b:c", quiz.SKIP_GOALS):
        assert not answer_button(state, bad)
    assert answer_button(state, "quiz:night:chill")
    assert not answer_button(state, "quiz:night:hype")  # double tap / changed mind
    assert state.answers == {"night": "chill"}
    assert not answer_text(state, "too early")


def test_unfinished_quiz_falls_back_to_defaults():
    assert build_profile("1", QuizState()).vibe == "balanced"


def test_goals_are_capped():
    state = run_quiz(goals=",".join(str(i) for i in range(20)))
    assert len(build_profile("1", state).goals) == 5


def test_save_load_roundtrip_and_permissions(home):
    save(Profile("42", vibe="chill", goals=["a"], traits={"x": "y"}))
    assert load("42") == Profile("42", "chill", ["a"], {"x": "y"})
    path = home / "profiles" / "42.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert not list(path.parent.glob("*.tmp"))


def test_load_missing_or_corrupt_is_none(home):
    assert load("nobody") is None
    (home / "profiles").mkdir()
    (home / "profiles" / "7.json").write_text("{not json")
    assert load("7") is None
    (home / "profiles" / "8.json").write_text("[1,2]")
    assert load("8") is None


def test_hostile_user_id_stays_inside_profiles_dir(home):
    save(Profile("../../evil"))
    assert not (home.parent / "evil.json").exists()
    assert all(p.parent == home / "profiles" for p in (home / "profiles").iterdir())
    assert load("../../evil") is not None


def test_bad_vibe_in_file_is_repaired(home):
    save(Profile("1"))
    (home / "profiles" / "1.json").write_text('{"vibe": "ultra"}')
    assert load("1").vibe == "balanced"


def test_set_vibe_and_delete():
    assert set_vibe("1", "nonsense") is None
    assert set_vibe("1", " HYPE ").vibe == "hype"
    assert load("1").vibe == "hype"
    assert delete("1") is True
    assert delete("1") is False and load("1") is None


def test_commands():
    assert "quiz" in handle_command("1", "/profile").text
    assert handle_command("1", "/vibe hype").text.startswith("HYPE")
    assert load("1").vibe == "hype"
    assert "chill, balanced, hype" in handle_command("1", "/vibe@ReelayBot loud").text
    assert len(handle_command("1", "/vibe").buttons) == 3
    assert "hype" in handle_command("1", "/profile").text
    assert handle_vibe_button("1", "vibe:chill").text == "Okay. Taking it easy."
    assert handle_vibe_button("1", "adopt") is None
    assert "erased" in handle_command("1", "/forget").text
    assert "Nothing" in handle_command("1", "/forget").text
    assert handle_command("1", "/quiz").buttons
    assert handle_command("1", "hello") is None and handle_command("1", "") is None


def test_finish_quiz_saves_and_keeps_learned_interests():
    save(Profile("1", traits={"interest_tech": "3", "detail": "deep"}))
    profile, step = finish_quiz("1", run_quiz())
    assert load("1") == profile
    assert profile.traits["interest_tech"] == "3" and profile.traits["detail"] == "short"
    assert "/forget" in step.text


def test_style_by_vibe():
    assert style("Nice find!", Profile("1", vibe="chill")) == "Nice find."
    assert style("Nice find.", Profile("1", vibe="hype")) == "Nice find! 🔥"
    assert style("Nice find!", Profile("1", vibe="hype")) == "Nice find!"
    assert style("Hi.", Profile("1")) == "Hi." and style("Hi.", None) == "Hi."


def test_prompt_line():
    assert "calm" in prompt_line(Profile("1", vibe="chill"))
    assert "high-energy" in prompt_line(Profile("1", vibe="hype"))
    assert "verdict only" in prompt_line(Profile("1", traits={"detail": "short"}))
    assert prompt_line(None) == style_mod.PROMPT_LINES["balanced"]
    assert prompt_line(Profile("1", vibe="weird")) == style_mod.PROMPT_LINES["balanced"]


def test_learn_counts_topics_as_whole_words():
    p = Profile("1")
    learn(p, "new AI coding tool")
    learn(p, "ai again")
    learn(p, "I said hello, maintain")  # 'ai' inside 'maintain' must not match
    assert p.traits == {"interest_tech": "2"}
    assert "interest_tech: 2" in describe(p)
