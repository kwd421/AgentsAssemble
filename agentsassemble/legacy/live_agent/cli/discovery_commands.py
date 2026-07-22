"""Retained live-agent health, discovery, and proof CLI commands."""
from __future__ import annotations

import argparse
import json
import math
import re
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.legacy.live_agent.cli.session_commands import (
    format_session_start,
    session_command_exit_code,
    validate_session_auto_restart_args,
)
from agentsassemble.legacy.live_agent.codex_sessions import write_agent_config
from agentsassemble.legacy.live_agent.runtime.continuity_proof import (
    run_live_agent_continuity_proof,
    run_live_agent_continuity_proof_batch,
)
from agentsassemble.legacy.live_agent.runtime.discovery import (
    add_session_bundle_outputs,
    apply_discovery_approval_filter,
    build_discovered_live_agent_config,
    build_discovered_session_bundle,
    discovery_has_exact_approval,
    discovered_session_bundle_paths,
    fill_discovery_next_command_output,
    validate_distinct_session_bundle_paths,
)
from agentsassemble.legacy.live_agent.runtime.processes import clean_live_agent_group_id
from agentsassemble.live_agent_runner import ResidentAgentConfig, load_group_configs


@dataclass(frozen=True)
class LegacyDiscoveryCliRuntime:
    request_json: Callable[..., dict[str, object]]
    server_url: Callable[[str, str], str]
    monotonic: Callable[[], float]
    sleep: Callable[[float], None]
    is_wait_timeout: Callable[[BaseException], bool]
    ensure_session_run: Callable[[argparse.Namespace], tuple[str, dict[str, object]]]
    setup_error_checker: Callable[[ResidentAgentConfig], str]
    preflight_config: Callable[..., dict[str, object]]
    write_discovery_outputs: Callable[..., tuple[Path | None, dict[str, object]]]


def run_legacy_discovery_command(
    args: argparse.Namespace,
    *,
    runtime: LegacyDiscoveryCliRuntime,
) -> int | None:
    handlers = {
        "health": _run_health,
        "local-resources": _run_local_resources,
        "preflight": _run_preflight,
        "discover": _run_discover,
        "auto-join": _run_auto_join,
        "continuity-proof": _run_continuity_proof,
        "continuity-proof-group": _run_continuity_proof_group,
        "persona-smoke": _run_persona_smoke,
    }
    handler = handlers.get(str(getattr(args, "live_agent_command", "")))
    return handler(args, runtime) if handler is not None else None


def _run_health(args: argparse.Namespace, runtime: LegacyDiscoveryCliRuntime) -> int:
    if args.wait_ok and args.wait_session_ready:
        raise ValueError("Use only one of --wait-ok or --wait-session-ready.")
    if args.wait_session_ready and (not str(args.meeting_id or "").strip() or not str(args.group_id or "").strip()):
        raise ValueError("--wait-session-ready requires --meeting-id and --group-id.")
    if args.wait_ok or args.wait_session_ready:
        return _run_health_wait(args, runtime)
    payload = runtime.request_json(runtime.server_url(args.server, "/api/live-agent-health"))
    _print_live_agent_health_payload(payload, as_json=args.as_json)
    return 1 if args.fail_on_degraded and payload.get("status") != "ok" else 0


def _run_health_wait(args: argparse.Namespace, runtime: LegacyDiscoveryCliRuntime) -> int:
    timeout_seconds = float(args.timeout)
    poll_interval = max(0.01, float(args.poll_interval))
    deadline = runtime.monotonic() + timeout_seconds
    attempts = 0
    last_payload: dict[str, object] | None = None
    while True:
        now = runtime.monotonic()
        if attempts > 0 and now >= deadline:
            if last_payload is not None:
                _print_live_agent_health_payload(last_payload, as_json=args.as_json)
            return 1
        remaining_before_poll = max(0.01, deadline - now)
        attempts += 1
        try:
            payload = runtime.request_json(
                runtime.server_url(args.server, "/api/live-agent-health"),
                timeout_seconds=remaining_before_poll,
            )
        except (TimeoutError, urllib.error.URLError) as error:
            if not runtime.is_wait_timeout(error):
                raise
            if last_payload is not None:
                _print_live_agent_health_payload(last_payload, as_json=args.as_json)
            return 1
        last_payload = payload
        if _live_agent_health_wait_satisfied(payload, args):
            _print_live_agent_health_payload(payload, as_json=args.as_json)
            return 0
        remaining_after_poll = max(0.0, deadline - runtime.monotonic())
        if remaining_after_poll > 0:
            runtime.sleep(min(poll_interval, remaining_after_poll))


def _print_live_agent_health_payload(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_health(payload))


def _live_agent_health_wait_satisfied(payload: dict[str, object], args: argparse.Namespace) -> bool:
    if args.wait_session_ready:
        session = _find_live_agent_health_session(payload, args.meeting_id, args.group_id)
        if session is None or str(session.get("status") or "").strip() != "ready":
            return False
        return not args.fail_on_degraded or payload.get("status") == "ok"
    return payload.get("status") == "ok"


def _run_local_resources(args: argparse.Namespace, runtime: LegacyDiscoveryCliRuntime) -> int:
    payload = runtime.request_json(runtime.server_url(args.server, "/api/local-resources"))
    _print_live_agent_local_resources_payload(payload, as_json=args.as_json)
    return 1 if args.fail_on_degraded and payload.get("status") != "ok" else 0


def _print_live_agent_local_resources_payload(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_local_resources(payload))


def _format_live_agent_local_resources(payload: dict[str, object]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    load_average = payload.get("load_average") if isinstance(payload.get("load_average"), dict) else {}
    processes = payload.get("processes") if isinstance(payload.get("processes"), list) else []
    lines = [
        f"local resources: {payload.get('status') or 'unknown'}",
        (
            f"load: {load_average.get('one', 0)} / {load_average.get('five', 0)} / "
            f"{load_average.get('fifteen', 0)} on {payload.get('cpu_count') or 0} CPUs"
        ),
        (
            f"tracked processes: {summary.get('process_count', 0)}, "
            f"cpu {summary.get('total_cpu_pct', 0)}%, rss {_format_kb_as_mb(summary.get('total_rss_kb'))}"
        ),
    ]
    attention = summary.get("attention") if isinstance(summary.get("attention"), list) else []
    if attention:
        lines.append(f"attention: {_attention_summary(attention)}")
    for process in processes[:8]:
        if not isinstance(process, dict):
            continue
        lines.append(
            (
                f"- {process.get('pid')}: {process.get('comm') or 'unknown'} "
                f"{process.get('role') or 'other'} "
                f"cpu {process.get('cpu_pct', 0)}% rss {_format_kb_as_mb(process.get('rss_kb'))}"
            )
        )
    return "\n".join(lines)


def _format_kb_as_mb(value: object) -> str:
    try:
        kb = float(value or 0)
    except (TypeError, ValueError):
        kb = 0.0
    return f"{kb / 1024:.1f} MB"


def _find_live_agent_health_session(
    payload: dict[str, object],
    meeting_id: str,
    group_id: str,
) -> dict[str, object] | None:
    sessions = payload.get("sessions") if isinstance(payload.get("sessions"), dict) else {}
    items = sessions.get("items") if isinstance(sessions.get("items"), list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("meeting_id") or "") == meeting_id and str(item.get("group_id") or "") == group_id:
            return item
    return None


def _run_preflight(args: argparse.Namespace, runtime: LegacyDiscoveryCliRuntime) -> int:
    report = runtime.preflight_config(Path(args.config), server_override=args.server)
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_preflight(report))
    return 0 if report.get("status") == "ok" else 1


def write_live_agent_discovery_outputs(
    args: argparse.Namespace,
    *,
    session_bundle: bool,
) -> tuple[Path | None, dict[str, object]]:
    report = build_discovered_live_agent_config(
        server=args.server,
        meeting_id=args.meeting_id,
        engagement_mode=args.engagement_mode,
        include_legacy_gemini=args.include_legacy_gemini,
    )
    if _live_agent_auto_join_has_exact_approval_args(args):
        apply_discovery_approval_filter(
            report,
            approved_agents=getattr(args, "approve_agents", []) or [],
            approved_commands=getattr(args, "approve_commands", []) or [],
        )
    output_path = Path(args.output) if args.output else None
    if report.get("status") == "ok" and output_path is not None:
        session_bundle_paths = None
        if session_bundle:
            session_bundle_paths = discovered_session_bundle_paths(
                output_path,
                council_output=args.session_council_output,
                agent_output=args.session_agent_output,
            )
            validate_distinct_session_bundle_paths(output_path, *session_bundle_paths)
        write_agent_config(output_path, report["config"])
        fill_discovery_next_command_output(report, str(output_path))
        if session_bundle and session_bundle_paths is not None:
            council_output, agent_output = session_bundle_paths
            bundle = build_discovered_session_bundle(report["config"])
            write_agent_config(council_output, bundle["council_config"])
            write_agent_config(agent_output, bundle["agent_config"])
            add_session_bundle_outputs(
                report,
                live_agent_output=str(output_path),
                council_output=str(council_output),
                agent_output=str(agent_output),
                server=args.server,
                meeting_id=args.meeting_id,
                group_id=clean_live_agent_group_id(output_path.stem),
            )
    return output_path, report


def _run_discover(args: argparse.Namespace, runtime: LegacyDiscoveryCliRuntime) -> int:
    output_path, report = runtime.write_discovery_outputs(
        args,
        session_bundle=bool(args.session_bundle),
    )
    if args.as_json:
        print(json.dumps({"output": str(output_path or ""), **report}, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_discovery(report, output_path=output_path))
    return 0 if report.get("status") == "ok" else 1


def _run_auto_join(args: argparse.Namespace, runtime: LegacyDiscoveryCliRuntime) -> int:
    validate_session_auto_restart_args(args)
    output_path, report = runtime.write_discovery_outputs(args, session_bundle=True)
    discovery_payload = {"output": str(output_path or ""), **report}
    if report.get("status") != "ok":
        result = {"status": report.get("status") or "empty", "action": "none", "discovery": discovery_payload, "session": {}}
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(_format_live_agent_discovery(report, output_path=output_path))
        return 1
    if _live_agent_discovery_requires_approval(report) and not bool(args.approve_real_providers):
        result = {
            "status": "approval_required",
            "action": "none",
            "approval_required": {
                "commands": _live_agent_discovery_approval_commands(report),
            },
            "discovery": discovery_payload,
            "session": {},
        }
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            commands = ", ".join(result["approval_required"]["commands"]) or "real provider CLI"
            print(f"Auto-join requires --approve-real-providers before starting: {commands}")
        return 1
    session_bundle = report.get("session_bundle") if isinstance(report.get("session_bundle"), dict) else {}
    ensure_args = argparse.Namespace(**vars(args))
    ensure_args.group_id = str(session_bundle.get("group_id") or "")
    ensure_args.council_config = str(session_bundle.get("council_config_path") or "")
    ensure_args.agent_config = str(session_bundle.get("agent_config_path") or "")
    ensure_args.live_agent_config = str(session_bundle.get("live_agent_config_path") or output_path or "")
    ensure_args.probe_bound_agents = _live_agent_auto_join_requires_reply_probe(args, report)
    ensure_args.approve_real_providers = bool(args.approve_real_providers) or discovery_has_exact_approval(report)
    action, response = runtime.ensure_session_run(ensure_args)
    result = {
        "status": response.get("status") or "unknown",
        "action": action,
        "discovery": discovery_payload,
        "session": response,
    }
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Auto-joined via {action}: {format_session_start(response)}")
    return session_command_exit_code(response)


def _live_agent_discovery_requires_approval(report: dict[str, object]) -> bool:
    discoveries = report.get("discoveries") if isinstance(report.get("discoveries"), list) else []
    return any(
        isinstance(item, dict)
        and item.get("included")
        and item.get("requires_approval")
        and item.get("approval_status") != "approved"
        for item in discoveries
    )


def _live_agent_auto_join_requires_reply_probe(args: argparse.Namespace, report: dict[str, object]) -> bool:
    return bool(getattr(args, "probe_bound_agents", False)) or discovery_has_exact_approval(report) or (
        bool(getattr(args, "approve_real_providers", False)) and _live_agent_discovery_requires_approval(report)
    )


def _live_agent_auto_join_has_exact_approval_args(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "approve_agents", []) or getattr(args, "approve_commands", []))


def _live_agent_discovery_approval_commands(report: dict[str, object]) -> list[str]:
    discoveries = report.get("discoveries") if isinstance(report.get("discoveries"), list) else []
    commands = []
    for item in discoveries:
        if not isinstance(item, dict) or not item.get("included") or not item.get("requires_approval"):
            continue
        command = str(item.get("command") or "").strip()
        if command:
            commands.append(command)
    return commands[:5]


def _format_live_agent_discovery(report: dict[str, object], *, output_path: Path | None) -> str:
    status = str(report.get("status") or "empty")
    lines = [f"discover: {status}"]
    config = report.get("config") if isinstance(report.get("config"), dict) else {}
    agents = config.get("agents") if isinstance(config.get("agents"), list) else []
    if output_path is not None and status == "ok":
        lines.append(f"wrote {output_path}")
    if agents:
        labels = [str(agent.get("agent_id") or "") for agent in agents if isinstance(agent, dict)]
        lines.append("agents " + ", ".join(label for label in labels if label))
    discoveries = report.get("discoveries") if isinstance(report.get("discoveries"), list) else []
    for item in discoveries:
        if not isinstance(item, dict):
            continue
        entry = _format_live_agent_discovery_entry(item)
        if entry:
            lines.append(entry)
    skipped = [
        f"{item.get('command')}:{item.get('reason')}"
        for item in discoveries
        if isinstance(item, dict) and item.get("available") and not item.get("included")
    ]
    if skipped:
        lines.append("skipped " + ", ".join(skipped))
    if status != "ok":
        lines.append("No supported local agent CLIs found.")
    return "\n".join(lines)


def _format_live_agent_discovery_entry(item: dict[str, object]) -> str:
    command = str(item.get("command") or "").strip()
    entry_status = str(item.get("entry_status") or "").strip()
    entry_mode = str(item.get("entry_mode") or item.get("connection_kind") or "").strip()
    join_semantics = str(item.get("join_semantics") or "").strip()
    context_durability = str(item.get("context_durability") or "").strip()
    sandbox_enforcement = str(item.get("sandbox_enforcement") or "").strip()
    evidence_basis = str(item.get("evidence_basis") or "").strip()
    operator_action = str(item.get("operator_action") or "").strip()
    approval = "approval required" if item.get("requires_approval") else ""
    parts = [
        command,
        entry_status,
        entry_mode,
        join_semantics,
        context_durability,
        sandbox_enforcement,
        evidence_basis,
        operator_action,
        approval,
    ]
    clean = [part for part in parts if part]
    return "entry " + " ".join(clean) if clean else ""


def _run_continuity_proof(args: argparse.Namespace, runtime: LegacyDiscoveryCliRuntime) -> int:
    config = ResidentAgentConfig(
        server="",
        agent_id=str(args.agent_id or "continuity-proof"),
        display_name=str(args.display_name or args.agent_id or "Continuity Proof"),
        provider_kind=str(args.provider_kind or ""),
        connection_kind=str(args.connection_kind or "live_session"),
        session_id=str(args.session_id or ""),
        endpoint="",
        auth_ref="",
        meeting_id="",
        engagement_mode="always",
        command=list(args.resident_command or []),
        timeout_seconds=int(args.timeout or 180),
        poll_interval=1.0,
        heartbeat_interval=30.0,
        cooldown=0.0,
        max_chain_depth=1,
        max_ticks=1,
    )
    result = run_live_agent_continuity_proof(
        config,
        approve_real_providers=bool(args.approve_real_providers),
        cwd=Path.cwd(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.as_json else _format_live_agent_continuity_proof(result))
    return 0 if result.get("status") == "ok" else 1


def _run_continuity_proof_group(args: argparse.Namespace, runtime: LegacyDiscoveryCliRuntime) -> int:
    server_override = str(args.server or "") or None
    configs = load_group_configs(Path(args.config), server_override=server_override)
    result = run_live_agent_continuity_proof_batch(
        configs,
        approve_real_providers=bool(args.approve_real_providers),
        setup_error_checker=runtime.setup_error_checker,
        cwd=Path.cwd(),
    )
    print(
        json.dumps(result, ensure_ascii=False, indent=2)
        if args.as_json
        else _format_live_agent_continuity_proof_group(result)
    )
    return _live_agent_continuity_proof_group_exit_code(result)


def _format_live_agent_continuity_proof(result: dict[str, object]) -> str:
    recall_state = "yes" if result.get("expected_suffix_recalled") else "no"
    if result.get("recall_match_mode"):
        recall_state = f"{recall_state} ({result.get('recall_match_mode')})"
    return (
        f"continuity proof {result.get('status') or 'unknown'}: "
        f"{result.get('provider_kind') or 'provider'} "
        f"{result.get('method') or 'provider_resume_suffix_recall'}; "
        f"session {'yes' if result.get('session_id_captured') else 'no'}; "
        f"suffix {recall_state}; "
        f"reason {result.get('reason') or 'unknown'}; "
        "limits two-turn provider-owned resume recall only; "
        "does not prove room admission or tool safety"
    )


def _format_live_agent_continuity_proof_group(result: dict[str, object]) -> str:
    status = result.get("status") or "unknown"
    return (
        f"continuity proof group {status}: "
        f"{result.get('ok_count') or 0} ok, "
        f"{result.get('failed_count') or 0} failed, "
        f"{result.get('unsupported_count') or 0} unsupported, "
        f"{result.get('approval_required_count') or 0} approval required; "
        "limits two-turn provider-owned resume recall only"
    )


def _live_agent_continuity_proof_group_exit_code(result: dict[str, object]) -> int:
    return 1 if result.get("status") in {"failed", "approval_required"} else 0


def _run_persona_smoke(args: argparse.Namespace, runtime: LegacyDiscoveryCliRuntime) -> int:
    from agentsassemble.legacy.live_agent.runtime.persona_smoke import run_live_agent_persona_smoke

    result = run_live_agent_persona_smoke(
        output_root=Path(args.output_root),
        card_path=Path(args.card),
        meeting_id=str(args.meeting_id or ""),
        character_mode=str(args.character_mode or "on"),
        context=str(args.context or ""),
    )
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        contract = result.get("persona_artifact_contract") if isinstance(result.get("persona_artifact_contract"), dict) else {}
        print(
            f"persona smoke {result.get('status') or 'unknown'}: "
            f"{result.get('meeting_id') or 'persona-smoke'} "
            f"contract {contract.get('status') or 'unknown'}"
        )
    return 0 if result.get("status") == "ok" else 1


def _format_live_agent_preflight(report: dict[str, object]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    agents = report.get("agents") if isinstance(report.get("agents"), list) else []
    lines = [
        f"preflight: {report.get('status') or 'unknown'}",
        f"agents: {summary.get('agents', 0)} checked, {summary.get('failed_agents', 0)} failed",
        f"checks failed: {summary.get('checks_failed', 0)}",
    ]
    for agent in agents:
        if not isinstance(agent, dict) or agent.get("status") != "failed":
            continue
        failed_checks = [
            check
            for check in agent.get("checks", [])
            if isinstance(check, dict) and check.get("status") == "failed"
        ]
        for check in failed_checks:
            lines.append(f"{agent.get('agent_id') or 'unknown'}: {check.get('id')}: {check.get('message')}")
    return "\n".join(lines)


def _readiness_probe_summary(probes: list[object]) -> str:
    labels = []
    for probe in probes:
        if not isinstance(probe, dict):
            continue
        agent_id = str(probe.get("agent_id") or "unknown")
        status = str(probe.get("status") or "unknown")
        labels.append(f"{agent_id} {status}")
    return ", ".join(labels) if labels else "none"


def _readiness_probe_group_summary(probe_groups: list[object]) -> str:
    labels = []
    for group in probe_groups:
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("group_id") or "unknown")
        status = str(group.get("status") or "unknown")
        reason = str(group.get("reason") or "")
        label = f"{group_id} {status}"
        if reason:
            label = f"{label} ({reason})"
        labels.append(label)
    return ", ".join(labels) if labels else "none"


def _official_round_smoke_summary(smoke: dict[str, object]) -> str:
    group_id = str(smoke.get("group_id") or "").strip()
    label = f"{smoke.get('status') or 'unknown'} {group_id}".strip()
    return (
        f"{label} ("
        f"{smoke.get('answered_count', 0)} answered, "
        f"{smoke.get('timeout_count', 0)} timed out, "
        f"{smoke.get('skipped_count', 0)} skipped)"
    )


def _session_smoke_summary(smoke: dict[str, object]) -> str:
    group_id = str(smoke.get("group_id") or "").strip()
    label = f"{smoke.get('status') or 'unknown'} {group_id}".strip()
    lobby_probe_count = max(1, int(smoke.get("lobby_probe_count") or 1))
    expected_total = int(smoke.get("expected_reply_count") or 0) * lobby_probe_count
    soak_cycle_count = max(0, int(smoke.get("soak_cycle_count") or 0))
    soak_part = ""
    if soak_cycle_count:
        soak_expected_total = int(smoke.get("expected_reply_count") or 0) * soak_cycle_count
        soak_part = f", soak {smoke.get('soak_reply_count', 0)}/{soak_expected_total} over {soak_cycle_count} cycles"
    post_stop_part = ""
    if smoke.get("post_stop_process_status"):
        post_stop_part = f", post-stop {smoke.get('post_stop_process_status')}"
    return (
        f"{label} ("
        f"{smoke.get('reply_count', 0)}/{expected_total} replies, "
        f"post-restart {smoke.get('post_restart_reply_count', 0)}/{expected_total}, "
        f"post-recover {smoke.get('post_recover_reply_count', 0)}/{expected_total}"
        f"{soak_part}"
        f"{post_stop_part})"
    )


def _format_live_agent_health(payload: dict[str, object]) -> str:
    agents = payload.get("agents") if isinstance(payload.get("agents"), dict) else {}
    admission = payload.get("admission") if isinstance(payload.get("admission"), dict) else {}
    processes = payload.get("processes") if isinstance(payload.get("processes"), dict) else {}
    process_monitor = payload.get("process_monitor") if isinstance(payload.get("process_monitor"), dict) else {}
    connections = payload.get("connections") if isinstance(payload.get("connections"), dict) else {}
    sandbox_enforcement = payload.get("sandbox_enforcement") if isinstance(payload.get("sandbox_enforcement"), dict) else {}
    sessions = payload.get("sessions") if isinstance(payload.get("sessions"), dict) else {}
    observations = payload.get("observations") if isinstance(payload.get("observations"), dict) else {}
    session_runs = payload.get("session_runs") if isinstance(payload.get("session_runs"), dict) else {}
    session_run_monitor = payload.get("session_run_monitor") if isinstance(payload.get("session_run_monitor"), dict) else {}
    agent_counts = agents.get("counts") if isinstance(agents.get("counts"), dict) else {}
    admission_counts = admission.get("counts") if isinstance(admission.get("counts"), dict) else {}
    process_counts = processes.get("counts") if isinstance(processes.get("counts"), dict) else {}
    agent_attention = agents.get("attention") if isinstance(agents.get("attention"), list) else []
    admission_attention = admission.get("attention") if isinstance(admission.get("attention"), list) else []
    process_attention = processes.get("attention") if isinstance(processes.get("attention"), list) else []
    process_reasons = _process_reason_summary(processes.get("reasons"))
    connection_attention = connections.get("attention") if isinstance(connections.get("attention"), list) else []
    sandbox_counts = (
        sandbox_enforcement.get("counts")
        if isinstance(sandbox_enforcement.get("counts"), dict)
        else {}
    )
    sandbox_attention = (
        sandbox_enforcement.get("attention")
        if isinstance(sandbox_enforcement.get("attention"), list)
        else []
    )
    session_attention = sessions.get("attention") if isinstance(sessions.get("attention"), list) else []
    observation_attention = observations.get("attention") if isinstance(observations.get("attention"), list) else []
    session_run_attention = session_runs.get("attention") if isinstance(session_runs.get("attention"), list) else []
    lines = [
        f"status: {payload.get('status') or 'unknown'}",
        (
            f"agents: {agents.get('live', 0)} live / {agents.get('total', 0)} total "
            f"(online {agent_counts.get('online', 0)}, working {agent_counts.get('working', 0)}, "
            f"error {agent_counts.get('error', 0)}, stale {agent_counts.get('stale', 0)}, "
            f"offline {agent_counts.get('offline', 0)})"
        ),
        f"agent attention: {_attention_summary(agent_attention)}",
        (
            f"processes: {process_counts.get('running', 0)} running / {processes.get('total', 0)} total "
            f"(restarting {process_counts.get('restarting', 0)}, error {process_counts.get('error', 0)}, "
            f"unknown {process_counts.get('unknown', 0)}, stopped {process_counts.get('stopped', 0)})"
        ),
        f"process attention: {_attention_summary(process_attention)}",
    ]
    if admission:
        lines.extend(
            [
                (
                    f"admission: {admission.get('host_approved', 0)} host-approved / "
                    f"{admission.get('total', 0)} total "
                    f"(unapproved {admission.get('unapproved', 0)}, "
                    f"bound {admission_counts.get('bound_to_meeting', 0)}, "
                    f"binding conflict {admission_counts.get('binding_conflict', 0)}, "
                    f"meeting lobby {admission_counts.get('meeting_lobby_only', 0)}, "
                    f"missing meeting {admission_counts.get('meeting_missing', 0)}, "
                    f"lobby-only {admission_counts.get('lobby_only', 0)}, "
                    f"unknown {admission_counts.get('unknown', 0)})"
                ),
                f"admission attention: {_attention_summary(admission_attention)}",
            ]
        )
    process_monitor_summary = _process_monitor_summary(process_monitor)
    if process_monitor_summary:
        lines.append(f"process monitor: {process_monitor_summary}")
    if process_reasons:
        lines.append(f"process reasons: {process_reasons}")
    lines.extend(
        [
            f"connections: {connections.get('connected', 0)} connected / {connections.get('expected', 0)} expected",
            f"connection attention: {_attention_summary(connection_attention)}",
            (
                f"sandbox: advisory {sandbox_counts.get('advisory', 0)}, "
                f"codex_readonly {sandbox_counts.get('codex_readonly', 0)}, "
                f"os_sandboxed {sandbox_counts.get('os_sandboxed', 0)}, "
                f"unknown {sandbox_counts.get('unknown', 0)}"
            ),
            f"sandbox attention: {_attention_summary(sandbox_attention)}",
            f"sessions: {sessions.get('ready', 0)} ready / {sessions.get('total', 0)} total",
            f"session attention: {_attention_summary(session_attention)}",
        ]
    )
    if observations:
        lines.extend(
            [
                (
                    f"observations: {observations.get('ready_agent_count', 0)} ready agents, "
                    f"lobby behind {observations.get('lobby_behind_count', 0)}, "
                    f"live behind {observations.get('live_behind_count', 0)}, "
                    f"errors {observations.get('error_count', 0)}"
                ),
                f"observation attention: {_attention_summary(observation_attention)}",
            ]
        )
    if session_runs:
        retry_summary = _session_run_retry_summary(session_runs.get("items"))
        lines.extend(
            [
                (
                    f"session runs: {session_runs.get('active', 0)} active / {session_runs.get('total', 0)} total "
                    f"(ready {session_runs.get('ready', 0)}, retrying {session_runs.get('retrying', 0)})"
                ),
                f"session-run attention: {_attention_summary(session_run_attention)}",
            ]
        )
        if retry_summary:
            lines.append(f"session-run retries: {retry_summary}")
    monitor_summary = _session_run_monitor_summary(session_run_monitor)
    if monitor_summary:
        lines.append(f"session-run monitor: {monitor_summary}")
    return "\n".join(lines)


def _attention_summary(items: list[object]) -> str:
    cleaned = [str(item) for item in items if str(item)]
    return ", ".join(cleaned) if cleaned else "none"


def _session_run_retry_summary(value: object) -> str:
    if not isinstance(value, list):
        return ""
    labels = []
    for item in value:
        if not isinstance(item, dict):
            continue
        parts = []
        run_id = str(item.get("run_id") or "-").strip() or "-"
        failures = _safe_int(item.get("reconcile_failure_count"))
        backoff = _safe_int(item.get("reconcile_backoff_seconds"))
        next_reconcile_at = str(item.get("next_reconcile_at") or "").strip()
        if failures > 0:
            parts.append(f"retry failures {failures}")
        if backoff > 0:
            parts.append(f"retry backoff {backoff}s")
        if re.fullmatch(r"[0-9T:+.\-Z]{1,64}", next_reconcile_at):
            parts.append(f"next retry {next_reconcile_at}")
        if parts:
            labels.append(f"{run_id} {'; '.join(parts)}")
    return ", ".join(labels[:3])


def _process_reason_summary(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    labels = []
    for group_id, reason_payload in value.items():
        clean_group_id = str(group_id or "").strip()
        if not clean_group_id:
            continue
        if isinstance(reason_payload, dict):
            event_type = str(reason_payload.get("event_type") or "").strip()
            reason = str(reason_payload.get("reason") or "").strip()
        else:
            event_type = ""
            reason = str(reason_payload or "").strip()
        if not reason:
            continue
        labels.append(" ".join(part for part in (clean_group_id, event_type, reason) if part))
    return ", ".join(labels)


def _process_monitor_summary(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    monitor_fields = {"running", "interval_seconds", "last_tick_at", "last_status", "last_group_count", "last_error_type"}
    if not any(field in value for field in monitor_fields):
        return ""
    running = "true" if value.get("running") is True else "false"
    parts = [f"running {running}"]
    interval_seconds = _safe_nonnegative_float(value.get("interval_seconds"))
    if interval_seconds > 0:
        parts.append(f"interval {_format_seconds(interval_seconds)}")
    last_status = str(value.get("last_status") or "").strip()
    if last_status:
        parts.append(f"last {last_status}")
    parts.append(f"groups {_safe_int(value.get('last_group_count'))}")
    last_tick_at = str(value.get("last_tick_at") or "").strip()
    if last_tick_at:
        parts.append(f"last tick {last_tick_at}")
    last_error_type = str(value.get("last_error_type") or "").strip()
    if last_error_type:
        parts.append(f"error {last_error_type}")
    return "; ".join(parts)


def _session_run_monitor_summary(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    monitor_fields = {"running", "interval_seconds", "last_tick_at", "last_status", "last_result_count", "last_error_type"}
    if not any(field in value for field in monitor_fields):
        return ""
    running = "true" if value.get("running") is True else "false"
    parts = [f"running {running}"]
    interval_seconds = _safe_nonnegative_float(value.get("interval_seconds"))
    if interval_seconds > 0:
        parts.append(f"interval {_format_seconds(interval_seconds)}")
    last_status = str(value.get("last_status") or "").strip()
    if last_status:
        parts.append(f"last {last_status}")
    last_result_count = _safe_int(value.get("last_result_count"))
    parts.append(f"results {last_result_count}")
    last_tick_at = str(value.get("last_tick_at") or "").strip()
    if last_tick_at:
        parts.append(f"last tick {last_tick_at}")
    last_error_type = str(value.get("last_error_type") or "").strip()
    if last_error_type:
        parts.append(f"error {last_error_type}")
    return "; ".join(parts)


def _safe_nonnegative_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number >= 0 else 0.0


def _format_seconds(value: float) -> str:
    if value.is_integer():
        return f"{int(value)}s"
    return f"{value:g}s"


def _safe_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
