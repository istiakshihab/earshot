"""`ownscribe config set`: one setting changes, everything the user wrote around it stays."""

import json
import stat
import tomllib

import pytest
from click.testing import CliRunner

from ownscribe.config import Config, _merge_toml
from ownscribe.config_write import ConfigWriteError, coerce, set_value

# Shaped like a real, hand-edited config: aligned comments, a commented-out alternative, a section
# that only has comments for some keys, and no [obsidian] section at all.
REAL_LIKE = """\
[audio]
backend = "coreaudio"     # "coreaudio" (default) or "sounddevice"
mic = true                # also capture microphone input

[transcription]
model = "mlx-community/whisper-large-v3-turbo"  # faster: ~1.6x on Whisper
# model = "mlx-community/whisper-large-v3-mlx-8bit"  # previous: most accurate, slowest
language = "en"            # set explicitly to skip detection

[diarization]
enabled = true           # set to true + provide hf_token to enable
hf_token = "hf_secret"  # HuggingFace token for pyannote models

[summarization]
enabled = false
backend = "claude"        # "local", "claude", "ollama", or "openai"
# template = "meeting"    # built-in: "meeting", "lecture", or "brief"

[output]
dir = "~/ownscribe"       # base output directory
"""


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(REAL_LIKE)
    path.chmod(0o600)
    return path


def _load(path) -> Config:
    with open(path, "rb") as f:
        return _merge_toml(Config(), tomllib.load(f))


def test_changing_a_value_drops_only_its_own_trailing_comment(config_file):
    result = set_value("transcription.model", "small", config_file)
    text = config_file.read_text()

    assert result["changed"] and result["previous"] == "mlx-community/whisper-large-v3-turbo"
    assert 'model = "small"\n' in text  # "# faster: …" described the old model
    assert '# model = "mlx-community/whisper-large-v3-mlx-8bit"  # previous: most accurate, slowest' in text
    assert 'language = "en"            # set explicitly to skip detection' in text
    assert 'hf_token = "hf_secret"  # HuggingFace token for pyannote models' in text
    assert _load(config_file).transcription.model == "small"


def test_only_the_changed_line_differs(config_file):
    set_value("audio.mic", "false", config_file)
    before, after = REAL_LIKE.splitlines(), config_file.read_text().splitlines()

    assert len(before) == len(after)
    assert [(b, a) for b, a in zip(before, after, strict=True) if b != a] == [
        ("mic = true                # also capture microphone input", "mic = false")]


def test_missing_keys_and_sections_are_added(config_file):
    set_value("summarization.template", "brief", config_file)
    set_value("obsidian.enabled", "off", config_file)
    loaded = _load(config_file)

    assert loaded.summarization.template == "brief"
    assert loaded.obsidian.enabled is False
    assert '# template = "meeting"    # built-in: "meeting", "lecture", or "brief"' in config_file.read_text()


def test_an_unchanged_value_writes_nothing(config_file):
    before = config_file.stat().st_mtime_ns
    result = set_value("diarization.enabled", "true", config_file)

    assert not result["changed"]
    assert config_file.stat().st_mtime_ns == before
    assert not config_file.with_name("config.toml.bak").exists()


def test_mode_is_kept_and_the_backup_is_made_once(config_file):
    set_value("transcription.model", "small", config_file)
    backup = config_file.with_name("config.toml.bak")
    set_value("transcription.model", "base", config_file)

    assert stat.S_IMODE(config_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert backup.read_text() == REAL_LIKE  # the original, not the first change


def test_a_missing_file_starts_from_the_defaults_with_private_permissions(tmp_path):
    path = tmp_path / "sub" / "config.toml"
    set_value("obsidian.enabled", "false", path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert _load(path).obsidian.enabled is False
    assert "[transcription]" in path.read_text()  # the default file, with its comments


def test_values_are_typed_by_the_schema():
    assert coerce("diarization.enabled", "yes") is True
    assert coerce("diarization.max_speakers", "3") == 3
    assert coerce("transcription.model", "medium") == "medium"
    with pytest.raises(ConfigWriteError, match="true or false"):
        coerce("diarization.enabled", "maybe")
    with pytest.raises(ConfigWriteError, match="whole number"):
        coerce("diarization.max_speakers", "three")


@pytest.mark.parametrize("key", ["nope.enabled", "transcription.nope", "transcription", "templates.x"])
def test_unknown_keys_are_refused(key, config_file):
    with pytest.raises(ConfigWriteError):
        set_value(key, "x", config_file)
    assert config_file.read_text() == REAL_LIKE


def test_the_cli_command(config_file, monkeypatch):
    from ownscribe import config_write
    from ownscribe.cli import cli

    monkeypatch.setattr(config_write, "CONFIG_PATH", config_file)
    runner = CliRunner()

    ok = runner.invoke(cli, ["config", "set", "transcription.model", "small", "--json"])
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.output) | {"path": None} == {
        "ok": True, "key": "transcription.model", "value": "small",
        "previous": "mlx-community/whisper-large-v3-turbo", "changed": True, "path": None}

    bad = runner.invoke(cli, ["config", "set", "diarization.enabled", "maybe", "--json"])
    assert bad.exit_code == 64
    assert json.loads(bad.output)["code"] == "bad_setting"


def test_if_missing_leaves_a_written_key_alone(config_file):
    kept = set_value("diarization.enabled", "false", config_file, if_missing=True)
    added = set_value("diarization.ask_speaker_names", "false", config_file, if_missing=True)
    loaded = _load(config_file)

    assert not kept["changed"] and loaded.diarization.enabled is True
    assert added["changed"] and loaded.diarization.ask_speaker_names is False
