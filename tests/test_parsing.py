import textwrap

import pytest

from lem.cli import parse_duration
from lem.config import ConfigError, load_config


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


def test_tapo_name_parsed_and_kept_out_of_credentials(tmp_path):
    path = _write_config(tmp_path, """
        [plugs.desk]
        type = "tapo"
        ip = "10.0.0.7"
        tapo_name = "Lyon TV"
        username = "u@example.com"
        password = "pw"
    """)
    from lem.config import upload_alias
    config = load_config(path)
    plug = config.plugs["desk"]
    assert plug.tapo_name == "Lyon TV"
    assert "tapo_name" not in plug.credentials  # must not reach connect()
    assert upload_alias(plug) == "Lyon TV"


def test_upload_alias_falls_back_to_local_alias(tmp_path):
    path = _write_config(tmp_path, """
        [plugs.fake1]
        type = "fake"
    """)
    from lem.config import upload_alias
    assert upload_alias(load_config(path).plugs["fake1"]) == "fake1"


def test_rem_section_parsing(tmp_path):
    path = _write_config(tmp_path, """
        [rem]
        url = "https://rem.example.org/"
        token = "tok"
        experiment_id = "tv-standby"
        experiment_name = "TV Standby"
    """)
    config = load_config(path)
    assert config.rem.url == "https://rem.example.org"  # trailing slash stripped
    assert config.rem.experiment_id == "tv-standby"


def test_nickname_warnings(tmp_path):
    from lem.config import nickname_warnings
    path = _write_config(tmp_path, """
        [plugs.a]
        type = "tapo"
        ip = "10.0.0.1"
        tapo_name = "TV"
        [plugs.b]
        type = "tapo"
        ip = "10.0.0.2"
        tapo_name = "TV"
        [plugs.c]
        type = "tapo"
        ip = "10.0.0.3"
        [plugs.fake1]
        type = "fake"
    """)
    warns = nickname_warnings(load_config(path).plugs.values())
    text = " ".join(warns)
    assert "Duplicate" in text and "TV" in text     # a & b collide
    assert "'c' has no Tapo nickname" in text        # c blank
    assert "fake1" not in text                        # fakes ignored


def test_rem_section_incomplete_raises(tmp_path):
    path = _write_config(tmp_path, """
        [rem]
        url = "https://rem.example.org"
    """)
    with pytest.raises(ConfigError):
        load_config(path)
