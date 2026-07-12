import unittest

from agentsassemble.gui_response import GuiResponseMethods


class _BrokenPipeHandler:
    def handle(self) -> None:
        raise BrokenPipeError


class _ConnectionResetHandler:
    def handle(self) -> None:
        raise ConnectionResetError


class _UnexpectedFailureHandler:
    def handle(self) -> None:
        raise ValueError("unexpected")


class GuiResponseDisconnectTests(unittest.TestCase):
    def test_broken_pipe_and_connection_reset_close_the_request(self):
        for base in (_BrokenPipeHandler, _ConnectionResetHandler):
            handler_type = type("Handler", (GuiResponseMethods, base), {})
            handler = handler_type()
            handler.close_connection = False

            handler.handle()

            self.assertTrue(handler.close_connection)

    def test_unexpected_handler_failure_is_not_hidden(self):
        handler_type = type("Handler", (GuiResponseMethods, _UnexpectedFailureHandler), {})
        handler = handler_type()
        handler.close_connection = False

        with self.assertRaisesRegex(ValueError, "unexpected"):
            handler.handle()


if __name__ == "__main__":
    unittest.main()
