"""Absolute read deadlines for browser-facing HTTP requests."""
from __future__ import annotations

import socket
import threading
from typing import Any


DEFAULT_REQUEST_HEADER_TIMEOUT_SECONDS = 15.0
DEFAULT_REQUEST_BODY_TIMEOUT_SECONDS = 60.0


class RequestBodyDeadlineExceeded(TimeoutError):
    """The complete declared request body did not arrive before its deadline."""


class RequestDeadlineHandlerMixin:
    """Bound header and body reads even when a peer keeps dripping bytes."""

    _agentsassemble_header_timer: threading.Timer | None = None
    _agentsassemble_reading_headers = False
    _agentsassemble_header_expired = False

    def handle_one_request(self) -> None:
        self._agentsassemble_reading_headers = True
        self._agentsassemble_header_expired = False
        timer = self._deadline_timer(
            "request_header_timeout_seconds",
            DEFAULT_REQUEST_HEADER_TIMEOUT_SECONDS,
            self._expire_header_read,
        )
        self._agentsassemble_header_timer = timer
        timer.start()
        try:
            super().handle_one_request()
        finally:
            self._agentsassemble_reading_headers = False
            timer.cancel()
            self._agentsassemble_header_timer = None

    def parse_request(self) -> bool:
        parsed = super().parse_request()
        self._agentsassemble_reading_headers = False
        timer = self._agentsassemble_header_timer
        if timer is not None:
            timer.cancel()
        return parsed and not self._agentsassemble_header_expired

    def read_request_body(self, length: int) -> bytes:
        timed_out = threading.Event()

        def expire() -> None:
            timed_out.set()
            self.close_connection = True
            self._shutdown_request_read()

        timer = self._deadline_timer(
            "request_body_timeout_seconds",
            DEFAULT_REQUEST_BODY_TIMEOUT_SECONDS,
            expire,
        )
        timer.start()
        try:
            data = self.rfile.read(length)
        finally:
            timer.cancel()
        if timed_out.is_set():
            raise RequestBodyDeadlineExceeded
        return data

    def _expire_header_read(self) -> None:
        if not self._agentsassemble_reading_headers:
            return
        self._agentsassemble_header_expired = True
        self.close_connection = True
        self._shutdown_request_read()

    def _shutdown_request_read(self) -> None:
        try:
            self.connection.shutdown(socket.SHUT_RD)
        except OSError:
            pass

    def _deadline_timer(
        self,
        server_attribute: str,
        default: float,
        callback: Any,
    ) -> threading.Timer:
        seconds = float(getattr(self.server, server_attribute, default))
        timer = threading.Timer(seconds, callback)
        timer.daemon = True
        return timer
