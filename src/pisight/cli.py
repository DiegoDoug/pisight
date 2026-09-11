"""Command-line entry point.

Subcommands:

* ``pisight`` / ``pisight run`` -- launch the dashboard (``--mode mock|live``);
* ``pisight doctor [--json]`` -- read-only environment diagnostic;
* ``pisight screenshots --output DIR`` -- render the four screens to PNG;
* ``pisight config`` -- print the resolved configuration (never the token).

Nothing here can be made to capture, transmit or reconfigure anything. There is no flag
that puts an interface into monitor mode, and no flag that writes to Kismet.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from dataclasses import asdict
from types import FrameType
from typing import Any

from . import __version__
from .config import API_TOKEN_ENV, AppConfig, ConfigError, get_api_token, load_config
from .health.host import HostHealthProvider
from .logging_setup import configure_logging
from .polling.coordinator import PollingCoordinator
from .providers.base import DashboardProvider

__all__ = ["build_parser", "main"]

logger = logging.getLogger(__name__)

#: Exit codes. 0 success, 1 runtime failure, 2 usage/configuration error.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

_PASSIVE_NOTICE = (
    "PiSight is a passive display client. It reads from Kismet and never transmits, "
    "injects, deauthenticates, associates or modifies Kismet configuration."
)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for every subcommand."""
    parser = argparse.ArgumentParser(
        prog="pisight",
        description=f"PiSight {__version__} - passive Wi-Fi reconnaissance dashboard. "
        f"{_PASSIVE_NOTICE}",
    )
    parser.add_argument("--version", action="version", version=f"pisight {__version__}")
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="path to a TOML configuration file (overrides PISIGHT_CONFIG)",
    )
    parser.add_argument(
        "--mode",
        choices=("mock", "live"),
        help="data source: 'mock' for synthetic data, 'live' to read a local Kismet",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        help="logging verbosity (overrides configuration)",
    )
    parser.add_argument(
        "--smoke-seconds",
        type=float,
        metavar="SECONDS",
        help="run the UI for this long, then exit cleanly (headless smoke test)",
    )
    parser.add_argument(
        "--fullscreen",
        action="store_true",
        help="start fullscreen (the Raspberry Pi deployment default)",
    )

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", help="launch the dashboard (default when no subcommand is given)")

    doctor = sub.add_parser("doctor", help="read-only environment diagnostic")
    doctor.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    doctor.add_argument(
        "--no-network",
        action="store_true",
        help="skip the Kismet reachability and authentication probes",
    )

    shots = sub.add_parser("screenshots", help="render the four screens to PNG files")
    shots.add_argument(
        "--output",
        default="artifacts/screenshots",
        metavar="DIR",
        help="output directory (default: artifacts/screenshots)",
    )

    sub.add_parser("config", help="print the resolved configuration (the token is never shown)")

    return parser


def _load(args: argparse.Namespace) -> AppConfig:
    """Load configuration honouring the command-line overrides."""
    config = load_config(args.config, mode_override=args.mode)
    if args.fullscreen:
        from dataclasses import replace

        config = replace(config, display=replace(config.display, fullscreen=True))
    if args.log_level:
        from dataclasses import replace

        config = replace(config, logging=replace(config.logging, level=args.log_level))
    return config


def build_provider(config: AppConfig) -> DashboardProvider:
    """Construct the provider for the configured mode.

    Live mode without a token is a usage error rather than a silent fallback to mock: an
    operator who asked for live data must not be shown synthetic data that looks real.
    """
    host_health = HostHealthProvider(
        config.storage.kismet_log_path,
        cache_seconds=config.polling.host_health_interval_seconds,
    )

    if config.mode == "live":
        from .providers.kismet import KismetDashboardProvider

        token = get_api_token()
        if not token:
            raise ConfigError(
                f"live mode requires a readonly Kismet API token in {API_TOKEN_ENV}. "
                "See docs/RPI5_DEPLOYMENT.md for how to generate one."
            )
        logger.info("live mode: reading %s (read-only)", config.kismet.base_url)
        return KismetDashboardProvider(config, api_token=token, host_health=host_health)

    from .providers.mock import MockDashboardProvider

    logger.info("mock mode: generating synthetic observations, no radio is used")
    return MockDashboardProvider(storage_path=config.storage.kismet_log_path)


def command_run(config: AppConfig, smoke_seconds: float | None) -> int:
    """Launch the dashboard and block until exit."""
    from .ui.app import PiSightApp

    provider = build_provider(config)
    coordinator = PollingCoordinator(provider, config)

    def _terminate(signum: int, _frame: FrameType | None) -> None:
        """Stop the polling thread and let pygame's loop unwind on its own."""
        logger.info("received signal %s; shutting down", signum)
        coordinator.stop()
        raise KeyboardInterrupt

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _terminate)
        except (ValueError, OSError, AttributeError):  # pragma: no cover - platform dependent
            logger.debug("could not install handler for signal %s", sig)

    coordinator.start()
    try:
        app = PiSightApp(config, coordinator)
        return app.run(smoke_seconds=smoke_seconds)
    except KeyboardInterrupt:
        return EXIT_OK
    finally:
        coordinator.stop()


def command_doctor(config: AppConfig, *, as_json: bool, no_network: bool) -> int:
    """Run the diagnostic and print the report."""
    from .doctor import format_report, run_doctor

    report = run_doctor(config, check_kismet=not no_network)
    print(format_report(report, as_json=as_json))
    return report.exit_code


def command_screenshots(config: AppConfig, output: str) -> int:
    """Render the four screens and print the written paths."""
    from .ui.screenshots import generate_screenshots

    paths = generate_screenshots(output, config)
    for path in paths:
        print(path)
    return EXIT_OK


def command_config(config: AppConfig) -> int:
    """Print the resolved configuration.

    The token is never part of :class:`AppConfig`, so this cannot print it; the token's
    presence is reported as a boolean only.
    """
    data: dict[str, Any] = asdict(config)
    data["source_path"] = str(config.source_path) if config.source_path else "(packaged defaults)"
    data["api_token_present"] = bool(get_api_token())

    def render(value: Any, indent: int = 0) -> None:
        pad = "  " * indent
        if isinstance(value, dict):
            for key, inner in value.items():
                if isinstance(inner, dict):
                    print(f"{pad}[{key}]")
                    render(inner, indent + 1)
                else:
                    print(f"{pad}{key} = {inner!r}")
        else:  # pragma: no cover - configuration is always a mapping
            print(f"{pad}{value!r}")

    render(data)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Entry point used by both the console script and ``python -m pisight``."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = _load(args)
    except ConfigError as exc:
        print(f"pisight: configuration error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    configure_logging(config.logging.level)

    command = args.command or "run"
    try:
        if command == "doctor":
            return command_doctor(config, as_json=bool(args.json), no_network=bool(args.no_network))
        if command == "screenshots":
            return command_screenshots(config, args.output)
        if command == "config":
            return command_config(config)
        return command_run(config, args.smoke_seconds)
    except ConfigError as exc:
        print(f"pisight: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        return EXIT_OK
    except Exception as exc:
        logger.exception("fatal error")
        print(f"pisight: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
