import textwrap

import pytest

from lem.scan import (
    plug_section,
    remove_plug_sections,
    resolve_network,
    sanitize_alias,
    unique_alias,
)


def test_resolve_network_bare_address_means_slash24():
    assert str(resolve_network("192.168.1.0")) == "192.168.1.0/24"
    assert str(resolve_network("192.168.1.37")) == "192.168.1.0/24"


def test_resolve_network_explicit_prefix_kept():
    assert str(resolve_network("10.0.0.0/28")) == "10.0.0.0/28"


def test_resolve_network_too_large():
    with pytest.raises(ValueError):
        resolve_network("10.0.0.0/8")


def test_sanitize_alias():
    assert sanitize_alias("Desk Lamp") == "desk-lamp"
    assert sanitize_alias("Rack #2 (PSU)") == "rack-2-psu"
    assert sanitize_alias("plug_1") == "plug_1"
    assert sanitize_alias("!!!") == "plug"


def test_unique_alias():
    assert unique_alias("desk", set()) == "desk"
    assert unique_alias("desk", {"desk"}) == "desk-2"
    assert unique_alias("desk", {"desk", "desk-2"}) == "desk-3"


CONFIG = textwrap.dedent("""\
    [defaults]
    interval = 2.0

    [credentials.tapo]
    username = "u"
    password = "p"

    [plugs.desk]
    type = "tapo"
    ip   = "10.0.0.7"

    # a comment between sections
    [plugs.fake1]
    type = "fake"

    [plugs.rack]
    type = "tapo"
    ip   = "10.0.0.11"
""")


def test_remove_plug_sections_keeps_everything_else():
    out = remove_plug_sections(CONFIG, {"desk", "rack"})
    assert "[plugs.desk]" not in out
    assert "[plugs.rack]" not in out
    assert "10.0.0.7" not in out
    assert "[plugs.fake1]" in out
    assert 'type = "fake"' in out
    assert "[credentials.tapo]" in out
    assert "[defaults]" in out


def test_remove_plug_sections_selective():
    out = remove_plug_sections(CONFIG, {"desk"})
    assert "[plugs.desk]" not in out
    assert "[plugs.rack]" in out
    assert "10.0.0.11" in out


def test_rename_plug_section():
    from lem.scan import rename_plug_section
    import tomllib
    out = rename_plug_section(CONFIG, "desk", "bench")
    parsed = tomllib.loads(out)
    assert "desk" not in parsed["plugs"]
    assert parsed["plugs"]["bench"] == {"type": "tapo", "ip": "10.0.0.7"}
    assert parsed["plugs"]["rack"]["ip"] == "10.0.0.11"  # others untouched


def test_decode_tapo_nickname():
    from lem.scan import _decode_tapo_nickname
    # Real base64-encoded nicknames from Tapo's local API
    assert _decode_tapo_nickname("TGFiLUE=") == "Lab-A"
    assert _decode_tapo_nickname("QmVuMi01NS1MRy1PTEVELUMy") == "Ben2-55-LG-OLED-C2"
    assert _decode_tapo_nickname("Z29zMS1zZXJ2ZXI=") == "gos1-server"
    # Unicode survives the round-trip
    import base64
    assert _decode_tapo_nickname(base64.b64encode("Décodeur".encode()).decode()) == "Décodeur"
    # Non-base64 (already decoded) falls back to raw; empty stays empty
    assert _decode_tapo_nickname("Lab-A") == "Lab-A"      # hyphen isn't base64
    assert _decode_tapo_nickname("") == ""


def test_rename_plug_section_no_match_is_noop():
    from lem.scan import rename_plug_section
    assert rename_plug_section(CONFIG, "nonexistent", "x") == CONFIG


def test_plug_section_roundtrip():
    import tomllib
    text = CONFIG + plug_section("bench", "10.0.0.20")
    parsed = tomllib.loads(text)
    assert parsed["plugs"]["bench"] == {"type": "tapo", "ip": "10.0.0.20"}


def test_identify_shelly_gen2():
    import asyncio
    from unittest.mock import patch
    from lem import scan
    resp = {"name": "Kitchen", "id": "shellyplugs-aabbcc", "model": "SNPL-00112EU", "gen": 3}
    with patch.object(scan, "_http_get_json", lambda url, timeout=4.0: resp):
        d = asyncio.run(scan._identify_shelly("10.0.0.5"))
    assert d == {"ip": "10.0.0.5", "type": "shelly", "model": "SNPL-00112EU",
                 "nickname": "Kitchen", "gen": 3}


def test_identify_shelly_gen2_no_name_falls_back_to_id():
    import asyncio
    from unittest.mock import patch
    from lem import scan
    resp = {"id": "shellyplugsg3-112233", "model": "S3PL", "gen": 3}
    with patch.object(scan, "_http_get_json", lambda url, timeout=4.0: resp):
        d = asyncio.run(scan._identify_shelly("10.0.0.6"))
    assert d["nickname"] == "shellyplugsg3-112233" and d["gen"] == 3


def test_identify_shelly_gen1_reads_settings_name():
    import asyncio
    from unittest.mock import patch
    from lem import scan
    def fake_get(url, timeout=4.0):
        if url.endswith("/shelly"):
            return {"type": "SHPLG-S", "mac": "AABBCC", "num_meters": 1}
        if url.endswith("/settings"):
            return {"name": "Desk Lamp"}
        return None
    with patch.object(scan, "_http_get_json", fake_get):
        d = asyncio.run(scan._identify_shelly("10.0.0.7"))
    assert d["type"] == "shelly" and d["model"] == "SHPLG-S" and d["nickname"] == "Desk Lamp"


def test_identify_shelly_not_a_shelly():
    import asyncio
    from unittest.mock import patch
    from lem import scan
    with patch.object(scan, "_http_get_json", lambda url, timeout=4.0: None):
        assert asyncio.run(scan._identify_shelly("10.0.0.8")) is None


def test_plug_section_shelly_writes_device_name_and_type():
    import tomllib
    text = CONFIG + plug_section("bench", "10.0.0.9", "Bench Shelly", "shelly")
    p = tomllib.loads(text)["plugs"]["bench"]
    assert p == {"type": "shelly", "ip": "10.0.0.9", "device_name": "Bench Shelly"}


def test_upsert_mixed_types(tmp_path):
    import tomllib
    from lem.scan import upsert_plugs
    cfg = tmp_path / "config.toml"
    cfg.write_text('[plugs.old]\ntype = "tapo"\nip = "10.0.0.1"\ntapo_name = "Old"\n')
    added, refreshed = upsert_plugs(cfg, [
        ("old", "10.0.0.1", "Old", "tapo"),          # refresh tapo by IP
        ("shelly1", "10.0.0.2", "Kitchen", "shelly"),  # add shelly
    ])
    assert (added, refreshed) == (1, 1)
    p = tomllib.loads(cfg.read_text())["plugs"]
    assert p["old"]["type"] == "tapo" and p["old"]["tapo_name"] == "Old"
    assert p["shelly1"] == {"type": "shelly", "ip": "10.0.0.2", "device_name": "Kitchen"}


def test_plug_section_tapo_name_preserved_verbatim():
    import tomllib
    for name in ['Lyon "Salon" TV', "Décodeur n°2 çà", "back\\slash"]:
        text = CONFIG + plug_section("x", "10.0.0.9", name)
        assert tomllib.loads(text)["plugs"]["x"]["tapo_name"] == name


def test_upsert_plugs_adds_and_refreshes(tmp_path):
    import tomllib
    from lem.scan import upsert_plugs
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[credentials.tapo]\nusername = "u"\npassword = "p"\n\n'
        '[plugs.old-name]\ntype = "tapo"\nip = "10.0.0.7"\n\n'   # no tapo_name yet
        '[plugs.fake1]\ntype = "fake"\n'
    )
    # Refresh 10.0.0.7 (new alias + nickname) and add a brand-new plug
    added, refreshed = upsert_plugs(cfg, [
        ("desk", "10.0.0.7", "Desk"),
        ("rack", "10.0.0.11", "Rack"),
    ])
    assert (added, refreshed) == (1, 1)
    parsed = tomllib.loads(cfg.read_text())
    assert "old-name" not in parsed["plugs"]              # refreshed in place
    assert parsed["plugs"]["desk"] == {"type": "tapo", "ip": "10.0.0.7", "tapo_name": "Desk"}
    assert parsed["plugs"]["rack"]["tapo_name"] == "Rack"
    assert parsed["plugs"]["fake1"] == {"type": "fake"}   # untouched
    assert parsed["credentials"]["tapo"]["username"] == "u"  # untouched


def test_upsert_plugs_creates_missing_config(tmp_path):
    import tomllib
    from lem.scan import upsert_plugs
    cfg = tmp_path / "sub" / "config.toml"
    added, refreshed = upsert_plugs(cfg, [("desk", "10.0.0.7", "Desk")])
    assert (added, refreshed) == (1, 0)
    assert tomllib.loads(cfg.read_text())["plugs"]["desk"]["ip"] == "10.0.0.7"


def test_rem_section_write_and_remove(tmp_path):
    import tomllib
    from lem.scan import remove_rem_section, write_rem_section
    cfg = tmp_path / "config.toml"
    cfg.write_text(CONFIG)
    write_rem_section(cfg, "https://rem.example.org/", "tok123", "tv-standby", "TV Standby")
    parsed = tomllib.loads(cfg.read_text())
    assert parsed["rem"] == {
        "url": "https://rem.example.org",
        "token": "tok123",
        "experiment_id": "tv-standby",
        "experiment_name": "TV Standby",
    }
    assert parsed["plugs"]["desk"]["ip"] == "10.0.0.7"  # rest untouched
    # Re-join replaces rather than duplicates
    write_rem_section(cfg, "https://other.example", "tok456", "exp2")
    parsed = tomllib.loads(cfg.read_text())
    assert parsed["rem"]["token"] == "tok456"
    # Leave removes the section, keeps everything else
    out = remove_rem_section(cfg.read_text())
    parsed = tomllib.loads(out)
    assert "rem" not in parsed and "desk" in parsed["plugs"]
