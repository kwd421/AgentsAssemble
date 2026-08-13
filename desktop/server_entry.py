"""Executable entry point for the bundled desktop room runtime."""

import sys

from agentsassemble.cli import main
from agentsassemble.application.agent_bridge_entrypoint import main as agent_bridge_main
from agentsassemble.plugin.isolated_runner import main as plugin_runner_main
from agentsassemble.providers.antigravity_hook_client import main as antigravity_hook_client_main
from agentsassemble.providers.dns_resolver_worker import main as dns_resolver_main
from agentsassemble.providers.room_portal_mcp import main as room_portal_mcp_main


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--internal-dns-resolver":
        raise SystemExit(dns_resolver_main([sys.argv[0], *sys.argv[2:]]))
    if len(sys.argv) >= 2 and sys.argv[1] == "--internal-agent-bridge":
        raise SystemExit(agent_bridge_main())
    if len(sys.argv) >= 2 and sys.argv[1] == "--internal-plugin-runner":
        del sys.argv[1]
        raise SystemExit(plugin_runner_main())
    if len(sys.argv) >= 2 and sys.argv[1] == "--internal-antigravity-hook-client":
        raise SystemExit(antigravity_hook_client_main(sys.argv[2:]))
    if len(sys.argv) >= 2 and sys.argv[1] == "--internal-room-portal-mcp":
        raise SystemExit(room_portal_mcp_main(sys.argv[2:]))
    raise SystemExit(main())
