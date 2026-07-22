import textwrap

import pytest

from measure.cli import parse_duration
from measure.config import ConfigError, load_config


def test_duration_bare_seconds():
    assert parse_duration("600") == 600


def test_duration_suffixes():
    assert parse_duration("90s") == 90
    assert parse_duration("10m") == 600
    assert parse_duration("2h") == 7200
    assert parse_duration("1.5m") == 90


def test_duration_unlimited():
    assert parse_duration("unlimited") is None
    assert parse_duration("0") is None


@pytest.mark.parametrize("bad", ["", "abc", "10x", "-5", "1h30m"])
def test_duration_invalid(bad):
    with pytest.raises(ValueError):
        parse_duration(bad)


def _write_config(tmp_path, body):
    path = tmp_path / "config.toml"
    path.write_text(textwrap.dedent(body))
    return path


def test_config_load(tmp_path):
    path = _write_config(tmp_path, """
        [defaults]
        interval = 1.5

        [credentials.tapo]
        username = "u@example.com"
        password = "pw"

        [plugs.desk]
        type = "tapo"
        ip = "10.0.0.7"
    """)
    config = load_config(path)
    assert config.interval == 1.5
    assert config.plugs["desk"].ip == "10.0.0.7"
    assert config.plugs["desk"].credentials["username"] == "u@example.com"


def test_config_env_override(tmp_path, monkeypatch):
    path = _write_config(tmp_path, """
        [credentials.tapo]
        username = "file@example.com"
        password = "filepw"

        [plugs.desk]
        type = "tapo"
        ip = "10.0.0.7"
    """)
    monkeypatch.setenv("TAPO_USERNAME", "env@example.com")
    monkeypatch.setenv("TAPO_PASSWORD", "envpw")
    config = load_config(path)
    assert config.plugs["desk"].credentials["username"] == "env@example.com"
    assert config.plugs["desk"].credentials["password"] == "envpw"


def test_config_extra_plug_keys_passed_to_device(tmp_path):
    path = _write_config(tmp_path, """
        [plugs.flaky]
        type = "fake"
        fail_rate = 0.5
    """)
    config = load_config(path)
    assert config.plugs["flaky"].credentials == {"fail_rate": 0.5}


def test_config_missing_ip(tmp_path):
    path = _write_config(tmp_path, """
        [plugs.desk]
        type = "tapo"
    """)
    with pytest.raises(ConfigError):
        load_config(path)


def test_config_missing_type(tmp_path):
    path = _write_config(tmp_path, """
        [plugs.desk]
        ip = "10.0.0.7"
    """)
    with pytest.raises(ConfigError):
        load_config(path)


def test_config_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")
