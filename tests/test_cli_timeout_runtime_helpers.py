import os
import signal
import time

from agentsassemble.live_agent_runner import ResidentAgentConfig


class _FailingSelfServiceProcess:
    pid = 4321
    returncode = 7

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        del timeout
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


def _self_service_resident_config(**overrides) -> ResidentAgentConfig:
    data = {
        "server": "http://room.local",
        "agent_id": "selfer",
        "display_name": "Self Service",
        "provider_kind": "antigravity_cli",
        "connection_kind": "self_service",
        "session_id": "",
        "endpoint": "",
        "auth_ref": "",
        "meeting_id": "resident-m1",
        "engagement_mode": "always",
        "command": ["agent"],
        "timeout_seconds": 120,
        "poll_interval": 0,
        "heartbeat_interval": 30,
        "cooldown": 5,
        "max_chain_depth": 1,
    }
    data.update(overrides)
    return ResidentAgentConfig(**data)


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for_pid_exit(pid: int, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_exists(pid):
            return True
        time.sleep(0.01)
    return not _pid_exists(pid)


def _kill_pid(pid: int) -> None:
    stop_signal = getattr(signal, "SIGKILL", getattr(signal, "SIGTERM", None))
    if stop_signal is None:
        return
    os.kill(pid, stop_signal)
