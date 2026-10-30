"""Opt-in, localhost-only web viewer for committed BananLoop campaign state.

The UI does not call an agent or mutate a campaign; its Node bridge invokes
only the existing operator-snapshot/operator-events read-side CLI commands.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from .registry import CampaignRegistry, RegistryError


def build_command(args: argparse.Namespace) -> list[str]:
    if args.campaign_id and args.state_dir:
        raise ValueError("choose --campaign-id or --state-dir, not both")
    if args.demo and (args.campaign_id or args.state_dir):
        raise ValueError("--demo cannot be combined with a campaign selector")
    if not 1024 <= args.port <= 65535:
        raise ValueError("port must be 1024..65535")

    executable = args.node or shutil.which("node")
    if not executable:
        raise ValueError("Node.js 18+ is required for the optional web viewer")
    script = Path(__file__).resolve().parent / "web_ui_static" / "server.mjs"
    if not script.is_file():
        raise ValueError("web UI assets not installed; reinstall BananLoop")
    command = [executable, str(script), "--port", str(args.port)]
    if args.demo:
        return command

    repo = Path(args.repo).expanduser().resolve()
    if not repo.is_dir():
        raise ValueError(f"project repository does not exist: {repo}")
    campaign = args.campaign_id
    if not campaign and not args.state_dir:
        _, campaign = CampaignRegistry(repo).resolve()
        if not campaign:
            raise ValueError("no active campaign; use --campaign-id, --state-dir or --demo")
    command.extend(["--repo", str(repo)])
    if args.state_dir:
        command.extend(["--state-dir", str(Path(args.state_dir).expanduser().resolve())])
    else:
        command.extend(["--campaign-id", campaign])
    command.extend(["--python", str(Path(args.python).expanduser().resolve()) if args.python else sys.executable])
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Local read-only BananLoop web viewer; no agent chat or campaign writes"
    )
    parser.add_argument("--repo", default=".", help="target project repo, defaults to current directory")
    parser.add_argument("--campaign-id", help="registered campaign ID (defaults to active campaign)")
    parser.add_argument("--state-dir", help="explicit legacy or standalone campaign state directory")
    parser.add_argument("--demo", action="store_true", help="synthetic UI only (no BananLoop campaign)")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--node", help="optional Node.js executable path")
    parser.add_argument("--python", help="Python executable with BananLoop installed (defaults to this Python)")
    args = parser.parse_args(argv)
    try:
        command = build_command(args)
        print(f"BananLoop web viewer: http://127.0.0.1:{args.port}", flush=True)
        return subprocess.call(command)
    except (ValueError, RegistryError, OSError) as exc:
        parser.exit(2, f"BananLoop UI error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())