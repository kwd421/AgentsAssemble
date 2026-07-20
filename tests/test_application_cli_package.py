import unittest

from agentsassemble.application.cli.common import parse_codex_timeout
from agentsassemble.application.cli.core import register_core_parsers
from agentsassemble.application.cli.persona import register_persona_parsers
from agentsassemble.application.cli.room import register_room_parsers
from agentsassemble.legacy.live_agent.cli.parser import register_live_agent_parsers
from agentsassemble.legacy.live_agent.cli.sessions import register_sessions_parsers
from agentsassemble.cli_parser_common import parse_codex_timeout as compatibility_timeout
from agentsassemble.cli_parser_core import register_core_parsers as compatibility_core
from agentsassemble.cli_parser_live_agent import (
    register_live_agent_parsers as compatibility_live_agent,
)
from agentsassemble.cli_parser_persona import (
    register_persona_parsers as compatibility_persona,
)
from agentsassemble.cli_parser_room import register_room_parsers as compatibility_room
from agentsassemble.cli_parser_sessions import (
    register_sessions_parsers as compatibility_sessions,
)


class ApplicationCliPackageTests(unittest.TestCase):
    def test_root_parser_modules_export_owned_registration(self) -> None:
        pairs = (
            (compatibility_timeout, parse_codex_timeout),
            (compatibility_core, register_core_parsers),
            (compatibility_live_agent, register_live_agent_parsers),
            (compatibility_persona, register_persona_parsers),
            (compatibility_room, register_room_parsers),
            (compatibility_sessions, register_sessions_parsers),
        )
        for compatibility_export, owned_export in pairs:
            with self.subTest(export=owned_export.__name__):
                self.assertIs(compatibility_export, owned_export)


if __name__ == "__main__":
    unittest.main()
