from __future__ import annotations

import sys
from collections.abc import Sequence

from agentsassemble.legacy_runtime import (
    install_legacy_runtime_quarantine,
    legacy_disabled_message,
    legacy_runtime_enabled,
    requested_legacy_command,
)


def _load_cli_main():
    # Import only after the quarantine is installed. agentsassemble.cli still
    # carries compatibility imports while the native migration is in progress.
    from agentsassemble.cli import main as cli_main

    return cli_main


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    legacy_command = requested_legacy_command(args)

    if legacy_command is not None and not legacy_runtime_enabled():
        print(legacy_disabled_message(legacy_command), file=sys.stderr)
        return 2

    install_legacy_runtime_quarantine()
    return int(_load_cli_main()(args))


if __name__ == "__main__":
    raise SystemExit(main())
