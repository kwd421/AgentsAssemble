"""Executable entry point for the bundled desktop room runtime."""

import sys

from agentsassemble.cli import main
from agentsassemble.application.agent_bridge_entrypoint import main as agent_bridge_main
from agentsassemble.plugin.isolated_runner import main as plugin_runner_main
from agentsassemble.providers.dns_resolver_worker import main as dns_resolver_main


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--internal-dns-resolver":
        raise SystemExit(dns_resolver_main([sys.argv[0], *sys.argv[2:]]))
    if len(sys.argv) >= 2 and sys.argv[1] == "--internal-agent-bridge":
        raise SystemExit(agent_bridge_main())
    if len(sys.argv) >= 2 and sys.argv[1] == "--internal-plugin-runner":
        del sys.argv[1]
        raise SystemExit(plugin_runner_main())
    raise SystemExit(main())
