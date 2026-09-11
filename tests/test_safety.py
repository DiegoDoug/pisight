"""Executable guards on PiSight's passive-only, read-only security boundary.

These tests exist because the boundary is the product's core promise, and prose in a README
cannot enforce it. They inspect the actual source tree -- parsed, not grepped, so a mention
inside a docstring that *disclaims* a capability is not mistaken for the capability itself.

If a future change adds packet injection, a shell invocation, a Kismet write, or a
credential in the repository, one of these fails.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src" / "pisight"
REPO = Path(__file__).parent.parent

PYTHON_FILES = sorted(SRC.rglob("*.py"))


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def executable_strings(tree: ast.Module) -> list[str]:
    """Every string literal that is *not* a docstring, lowercased.

    Docstrings are excluded deliberately: PiSight's modules describe what they refuse to do,
    and that prose must not be mistaken for the behaviour itself.
    """
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))

    return [
        node.value.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_there_are_python_files_to_inspect() -> None:
    """Guards the guards: a path typo must not turn every test below into a no-op."""
    assert len(PYTHON_FILES) > 15


# --- No shell execution ----------------------------------------------------------------


@pytest.mark.parametrize("path", PYTHON_FILES, ids=lambda p: p.name)
def test_no_call_uses_shell_true(path: Path) -> None:
    """`shell=True` would make a hostile filename or environment variable executable."""
    for node in ast.walk(parse(path)):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == "shell":
                assert not (
                    isinstance(keyword.value, ast.Constant) and keyword.value.value is True
                ), f"{path.name}: shell=True"


@pytest.mark.parametrize("path", PYTHON_FILES, ids=lambda p: p.name)
def test_no_shell_helper_is_called(path: Path) -> None:
    """`os.system` and `os.popen` run a shell by definition; neither may appear."""
    forbidden = {"system", "popen", "execl", "execv", "spawnl"}
    for node in ast.walk(parse(path)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in forbidden
            and isinstance(node.func.value, ast.Name)
        ):
            assert node.func.value.id != "os", f"{path.name}: os.{node.func.attr}"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in ("eval", "exec"), f"{path.name}: {node.func.id}()"


def test_subprocess_is_only_imported_by_the_doctor() -> None:
    """Only the diagnostic runs external commands, and only read-only ones."""
    importers = set()
    for path in PYTHON_FILES:
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.Import) and any(
                alias.name.split(".")[0] == "subprocess" for alias in node.names
            ):
                importers.add(path.name)
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("subprocess"):
                importers.add(path.name)

    assert importers <= {"doctor.py"}, f"unexpected subprocess users: {importers}"


def test_every_external_command_is_read_only() -> None:
    """The doctor may only run commands that inspect; never ones that configure."""
    allowed_commands = {"lsusb", "iw"}
    allowed_subcommands = {"list", "dev"}

    tree = parse(SRC / "doctor.py")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "_run_command" or not node.args:
            continue

        argv = node.args[0]
        assert isinstance(argv, ast.List), "commands must be built as an explicit list"
        parts = [element.value for element in argv.elts if isinstance(element, ast.Constant)]
        assert parts, "command list must be literal, not computed"
        assert parts[0] in allowed_commands, f"unexpected executable: {parts[0]}"
        for part in parts[1:]:
            assert part in allowed_subcommands, f"unexpected argument: {part}"


# --- No offensive capability -----------------------------------------------------------

#: Offensive tool binaries PiSight must never invoke. Naming one of these in an executable
#: string is only ever a prelude to running it.
FORBIDDEN_TOOLS = (
    "aireplay",
    "airodump",
    "aircrack",
    "airmon",
    "wifite",
    "airgeddon",
    "bettercap",
    "mdk3",
    "mdk4",
    "hashcat",
    "hcxdumptool",
    "reaver",
    "bully",
    "pixiewps",
    "scapy",
)

#: Commands that *change* radio or network state. PiSight only ever reads.
FORBIDDEN_MUTATIONS = (
    "wpa_supplicant",
    "iwconfig",
    "hostapd",
    "ifconfig",
    "--monitor",
    "mode monitor",
    "mon0",
    "airmon-ng",
)

# Note on what is deliberately *not* listed here: technique words such as "deauth" and
# "disassoc" appear legitimately in PiSight as the names of Kismet alerts it displays
# (DEAUTHFLOOD, DISASSOCTRAFFIC) and in the CLI's own passive-only disclaimer. Banning the
# substring would forbid *reporting* an attack, which is the product's entire purpose.
# Actually performing one is prevented by the command allowlist and import checks above.


@pytest.mark.parametrize("path", PYTHON_FILES, ids=lambda p: p.name)
def test_no_offensive_tool_is_referenced_in_code(path: Path) -> None:
    """Docstrings may name these to disclaim them; executable code may not."""
    for literal in executable_strings(parse(path)):
        for token in FORBIDDEN_TOOLS + FORBIDDEN_MUTATIONS:
            assert token not in literal, f"{path.name}: forbidden reference {token!r}"


@pytest.mark.parametrize("path", PYTHON_FILES, ids=lambda p: p.name)
def test_no_raw_socket_or_packet_capture_import(path: Path) -> None:
    """PiSight is a display client: it must not be able to capture packets itself."""
    forbidden_modules = {"scapy", "pcapy", "pyshark", "dpkt", "socket", "raw"}
    for node in ast.walk(parse(path)):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [(node.module or "").split(".")[0]]
        for name in names:
            assert name not in forbidden_modules, f"{path.name} imports {name}"


# --- Kismet access is read-only --------------------------------------------------------


def test_no_kismet_write_or_control_endpoint_is_referenced() -> None:
    """Every configured endpoint must be a read. Control paths must not appear at all."""
    control_fragments = (
        "/datasource/add",
        "/datasource/remove",
        "set_channel",
        "set_hop",
        "open_source",
        "close_source",
        "pause_source",
        "resume_source",
        "/config/",
        "/alerts/definitions/define",
        "session/create",
    )
    for path in (SRC / "config.py", SRC / "providers" / "kismet.py"):
        for literal in executable_strings(parse(path)):
            for fragment in control_fragments:
                assert fragment.lower() not in literal, f"{path.name}: {fragment}"


def test_the_only_http_methods_used_are_get_and_post() -> None:
    """POST is used solely for Kismet's bounded query endpoints; nothing else."""
    literals = executable_strings(parse(SRC / "providers" / "kismet.py"))
    for method in ("put", "delete", "patch"):
        assert f'"{method}"' not in literals
    assert "get" in literals
    assert "post" in literals


def test_default_endpoints_are_all_read_paths() -> None:
    from pisight.config import KismetEndpoints

    endpoints = KismetEndpoints()
    for name in endpoints.__slots__:
        value = getattr(endpoints, name)
        assert value.startswith("/"), f"{name} is not a path"
        # Read endpoints are either .json documents or the session login check.
        assert value.endswith(".json") or "check_login" in value, f"{name}={value}"


def test_no_offensive_runtime_dependency_is_declared() -> None:
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8").lower()
    for token in ("scapy", "aircrack", "bettercap", "wifite", "hashcat", "pyshark"):
        assert token not in text


# --- No secrets or real observations committed -----------------------------------------

#: Extensions that could hold real capture data.
CAPTURE_EXTENSIONS = {".kismet", ".pcap", ".pcapng", ".cap", ".kismet-journal"}


def tracked_files() -> list[Path]:
    """Repository files that are candidates for review, excluding build noise."""
    skip_dirs = {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "build",
        "dist",
        "artifacts",
    }
    found: list[Path] = []
    for path in REPO.rglob("*"):
        if not path.is_file():
            continue
        if any(part in skip_dirs or part.endswith(".egg-info") for part in path.parts):
            continue
        found.append(path)
    return found


def test_no_capture_data_is_committed() -> None:
    for path in tracked_files():
        assert path.suffix not in CAPTURE_EXTENSIONS, f"capture data committed: {path}"


def test_fixture_macs_are_all_locally_administered() -> None:
    """A locally-administered MAC cannot collide with real hardware."""
    import json
    import re

    pattern = re.compile(r"\b([0-9A-Fa-f]{2})(?::[0-9A-Fa-f]{2}){5}\b")
    fixture_dir = REPO / "mock" / "fixtures"

    checked = 0
    for path in fixture_dir.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        json.loads(text)  # also proves each fixture is valid JSON
        for match in pattern.finditer(text):
            first_octet = int(match.group(1), 16)
            assert first_octet & 0b10, f"{path.name}: {match.group(0)} is not locally administered"
            checked += 1

    assert checked > 0, "no MAC addresses found to check"


def test_no_plausible_credential_is_committed() -> None:
    """Catches an API token or password pasted into a config example or script."""
    import re

    # An assignment of a long opaque value to a secret-looking name.
    pattern = re.compile(
        r"(?i)(api[_-]?token|api[_-]?key|password|passwd|secret)\s*[=:]\s*[\"']?"
        r"([A-Za-z0-9+/_-]{16,})",
    )
    placeholders = {
        "replace_with_your_readonly_token",
        "your_readonly_token_here",
        "paste_your_readonly_token_here",
        "changeme",
        "xxxxxxxxxxxxxxxx",
    }

    for path in tracked_files():
        if path.suffix not in (".toml", ".sh", ".service", ".yml", ".yaml", ".env", ".example"):
            continue
        for match in pattern.finditer(path.read_text(encoding="utf-8", errors="replace")):
            value = match.group(2)
            assert (
                value.lower() in placeholders or value.isupper() or value.startswith("PISIGHT")
            ), f"{path}: possible committed credential {match.group(1)}"


def test_no_todo_or_placeholder_markers_remain() -> None:
    """Unfinished markers in shipped code would contradict a completed MVP."""
    import re

    markers = re.compile(r"\b(TODO|FIXME|XXX|HACK|WIP)\b")
    offenders: list[str] = []
    for path in PYTHON_FILES:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if markers.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")

    assert not offenders, "unfinished markers found:\n" + "\n".join(offenders)


def test_no_module_raises_not_implemented() -> None:
    """A NotImplementedError in shipped code would mean an unfinished screen or provider."""
    for path in PYTHON_FILES:
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.Raise) and node.exc is not None:
                name = node.exc
                if isinstance(name, ast.Call):
                    name = name.func
                if isinstance(name, ast.Name):
                    assert name.id != "NotImplementedError", f"{path.name} has a stub"


# --- Privacy defaults ------------------------------------------------------------------


def test_privacy_defaults_are_conservative() -> None:
    from pisight.config import PrivacyConfig

    privacy = PrivacyConfig()
    assert privacy.show_full_mac is False
    assert privacy.enable_gps is False
    assert privacy.allow_uploads is False


def test_no_outbound_upload_destination_is_configured() -> None:
    """PiSight must have nowhere to send observations: every URL it holds is loopback."""
    for path in PYTHON_FILES:
        for literal in executable_strings(parse(path)):
            for scheme in ("http://", "https://"):
                if not literal.startswith(scheme):
                    continue
                host = literal[len(scheme) :].split("/")[0]
                if not host:
                    continue  # a bare scheme used for prefix validation, not a destination
                assert host.startswith(("127.0.0.1", "localhost", "[::1]")), (
                    f"{path.name}: non-loopback destination {literal!r}"
                )
