import pytest
from conftest import ROOT

from tripwire.cli import main
from tripwire.spec import SpecError, load_watchers

TEMPLATE = """\
params:
  who:
    description: who is greeted
  every:
    description: cadence
    default: slow
---
name: "{{who}} greeting"
every: "{{every}}"
url: "http://example.invalid/{{ who }}"
headers:
  X-Who: "{{who}}"
filter:
  - grep: "hello {{who}}"
"""


@pytest.fixture
def templates(tmp_path):
    folder = tmp_path / "templates"
    folder.mkdir()
    (folder / "greet.yaml").write_text(TEMPLATE)
    return folder


def load(tmp_path, templates, yaml_text):
    file = tmp_path / "watcher.yaml"
    file.write_text(yaml_text)
    return load_watchers(file, templates)


def test_parameters_are_filled_in_everywhere_including_nested_values(tmp_path, templates):
    (w,) = load(tmp_path, templates, "uses: greet\nwith: {who: ada}\n")
    assert w.name == "ada greeting"
    assert str(w.url) == "http://example.invalid/ada"
    assert w.headers == {"X-Who": "ada"}
    assert w.filter == [{"grep": "hello ada"}]


def test_a_default_applies_and_can_be_overridden(tmp_path, templates):
    (default,) = load(tmp_path, templates, "uses: greet\nwith: {who: ada}\n")
    (custom,) = load(tmp_path, templates, "uses: greet\nwith: {who: ada, every: fast}\n")
    assert (default.every, custom.every) == ("slow", "fast")


def test_one_template_can_watch_many_things_by_using_it_many_times(tmp_path, templates):
    watchers = load(
        tmp_path,
        templates,
        "uses: greet\nwith: {who: ada}\n---\nuses: greet\nwith: {who: grace}\n",
    )
    assert [w.name for w in watchers] == ["ada greeting", "grace greeting"]


def test_template_jobs_mix_with_plain_jobs_in_one_file(tmp_path, templates):
    watchers = load(
        tmp_path,
        templates,
        "name: plain\nevery: x\nurl: http://p.invalid/\n---\nuses: greet\nwith: {who: ada}\n",
    )
    assert [w.name for w in watchers] == ["plain", "ada greeting"]


@pytest.mark.parametrize(
    ("yaml_text", "message"),
    [
        ("uses: nope\n", r"unknown template 'nope' \(available: greet\)"),
        ("uses: greet\n", r"needs \['who'\] under 'with:'"),
        ("uses: greet\nwith: {who: ada, color: red}\n", r"no parameter \['color'\]"),
        ("uses: greet\nwith: {who: 'a\"b'}\n", "may only contain"),
        ("uses: greet\nwith: {who: 'a\\b'}\n", "may only contain"),
        ("uses: greet\nextra: 1\n", "extra: Extra inputs"),
        ("uses: greet\nwith: {who: ada}\n---\nuses: greet\nwith: {who: ada}\n", "duplicate name"),
    ],
)
def test_template_mistakes_are_reported_with_the_fix(tmp_path, templates, yaml_text, message):
    with pytest.raises(SpecError, match=message):
        load(tmp_path, templates, yaml_text)


def test_a_placeholder_the_template_never_declared_is_an_error(tmp_path, templates):
    (templates / "greet.yaml").write_text(TEMPLATE.replace('"hello {{who}}"', '"hi {{whom}}"'))
    with pytest.raises(SpecError, match="undeclared"):
        load(tmp_path, templates, "uses: greet\nwith: {who: ada}\n")


def test_watching_another_repository_takes_only_a_few_lines_of_yaml(tmp_path, capfd):
    file = tmp_path / "watcher.yaml"
    file.write_text("uses: github-repo-feed\nwith: {name: rust, owner: rust-lang, repo: rust}\n")
    watchers = load_watchers(file)
    assert len(watchers) == 5
    assert all(w.every == "live" for w in watchers)
    assert all("rust-lang/rust" in str(w.url) or "graphql" in str(w.url) for w in watchers)
    assert main(["check", str(file)]) == 0  # the whole expansion also loads in urlwatch


def test_every_committed_template_expands_with_placeholder_values():
    import yaml

    from tripwire.spec import TemplateHeader, Use, expand_use

    for file in sorted((ROOT / "templates").glob("*.yaml")):
        header = TemplateHeader.model_validate(next(yaml.safe_load_all(file.read_text())))
        values = {k: "example" for k, p in header.params.items() if p.default is None}
        jobs = expand_use(Use(uses=file.stem, **{"with": values}))
        assert jobs, f"{file.name} has no jobs"


def test_a_path_with_slashes_is_a_valid_parameter(tmp_path, templates):
    (w,) = load(tmp_path, templates, "uses: greet\nwith: {who: core/cap}\n")
    assert str(w.url) == "http://example.invalid/core/cap"
