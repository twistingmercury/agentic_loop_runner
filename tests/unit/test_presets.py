import json

from alr.engine.presets import (
    CREDENTIAL_PATHS,
    DOCKER_COMMANDS,
    WEB_DOMAINS,
    Preset,
    Switches,
    preset_path,
    render,
    switches_for,
)

TIGHT_TEXT = """{
  "sandbox": {
    "allowUnsandboxedCommands": false,
    "enabled": true,
    "failIfUnavailable": true,
    "filesystem": {
      "denyRead": [
        "~/.ssh",
        "~/.aws",
        "~/.gnupg",
        "~/.config/gh",
        "~/.netrc",
        "~/.claude"
      ]
    }
  }
}
"""


def test_tight_switches() -> None:
    assert switches_for(Preset.TIGHT) == Switches(
        web=False, docker=False, hide_credentials=True
    )


def test_large_switches() -> None:
    assert switches_for(Preset.LARGE) == Switches(
        web=True, docker=True, hide_credentials=True
    )


def test_render_tight_is_the_documented_text() -> None:
    assert render(switches_for(Preset.TIGHT)) == TIGHT_TEXT


def test_render_large_has_the_web_list_and_docker() -> None:
    doc = json.loads(render(switches_for(Preset.LARGE)))["sandbox"]

    assert doc["network"] == {"allowedDomains": list(WEB_DOMAINS)}
    assert doc["excludedCommands"] == list(DOCKER_COMMANDS)
    assert doc["filesystem"] == {"denyRead": list(CREDENTIAL_PATHS)}


def test_render_large_excludes_and_allows_the_docker_commands() -> None:
    doc = json.loads(render(switches_for(Preset.LARGE)))

    assert doc["sandbox"]["excludedCommands"] == [
        "docker *",
        "./build/build.sh",
        "./build/build.sh *",
    ]
    assert doc["permissions"] == {
        "allow": [
            "Bash(docker *)",
            "Bash(./build/build.sh)",
            "Bash(./build/build.sh *)",
        ]
    }


def test_docker_off_leaves_permissions_absent() -> None:
    doc = json.loads(render(Switches(web=True, docker=False, hide_credentials=True)))

    assert "permissions" not in doc
    assert "excludedCommands" not in doc["sandbox"]


def test_an_off_switch_leaves_its_key_absent() -> None:
    doc = json.loads(render(Switches(web=False, docker=False, hide_credentials=False)))[
        "sandbox"
    ]

    assert set(doc) == {"enabled", "allowUnsandboxedCommands", "failIfUnavailable"}


def test_tight_never_excludes_a_command() -> None:
    assert "excludedCommands" not in render(switches_for(Preset.TIGHT))


def test_render_is_deterministic() -> None:
    assert render(switches_for(Preset.LARGE)) == render(switches_for(Preset.LARGE))


def test_preset_path_is_beside_the_task_file() -> None:
    assert preset_path("run dir/tasks.yaml") == "run dir/sandbox.json"
