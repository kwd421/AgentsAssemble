"""Executable entry point for the bundled desktop room runtime."""

import sys

from agentsassemble.cli import main
from agentsassemble.providers.dns_resolver_worker import main as dns_resolver_main


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--internal-dns-resolver":
        raise SystemExit(dns_resolver_main([sys.argv[0], *sys.argv[2:]]))
    raise SystemExit(main())
