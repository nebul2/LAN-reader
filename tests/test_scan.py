import textwrap

from measure.scan import (
    plug_section,
    remove_plug_sections,
    sanitize_alias,
    unique_alias,
)


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


def test_plug_section_roundtrip():
    import tomllib
    text = CONFIG + plug_section("bench", "10.0.0.20")
    parsed = tomllib.loads(text)
    assert parsed["plugs"]["bench"] == {"type": "tapo", "ip": "10.0.0.20"}
