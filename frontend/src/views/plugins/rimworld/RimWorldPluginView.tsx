import { useEffect, useRef, useState } from "react";
import type { PluginEnvelope } from "../../../roomSocketClient";

type RimWorldPluginViewProps = {
  roomId: string;
  onCommand: (command: {
    plugin_id: string;
    command: string;
    args?: Record<string, unknown>;
    revision?: string;
  }) => void;
  envelopes: PluginEnvelope[];
  onOpenSideChat: () => void;
  canManage: boolean;
};

export default function RimWorldPluginView({
  roomId,
  onCommand,
  envelopes,
  onOpenSideChat,
  canManage,
}: RimWorldPluginViewProps) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const portRef = useRef<MessagePort | null>(null);
  const envelopesRef = useRef(envelopes);
  const lastSentSequenceRef = useRef(0);
  const activationRoomRef = useRef("");
  const [error, setError] = useState("");
  envelopesRef.current = envelopes;

  useEffect(() => {
    if (canManage && activationRoomRef.current !== roomId) {
      activationRoomRef.current = roomId;
      onCommand({ plugin_id: "rimworld", command: "activate" });
    }
  }, [canManage, onCommand, roomId]);

  useEffect(() => {
    function onWindowMessage(event: MessageEvent) {
      if (event.source !== iframeRef.current?.contentWindow) return;
      const data = event.data as { type?: string; plugin_id?: string } | null;
      if (data?.type !== "plugin.web.ready" || data.plugin_id !== "rimworld") return;
      const port = event.ports[0];
      if (!port) return;
      portRef.current = port;
      lastSentSequenceRef.current = 0;
      port.onmessage = (portEvent: MessageEvent) => {
        const message = portEvent.data as Record<string, unknown>;
        if (message?.type === "plugin.command") {
          onCommand({
            plugin_id: "rimworld",
            command: String(message.command || ""),
            args: message.args as Record<string, unknown> | undefined,
            revision: typeof message.revision === "string" ? message.revision : undefined,
          });
        }
      };
      port.start();
      port.postMessage({ type: "plugin.host.hello", room_id: roomId });
      envelopesRef.current.forEach((envelope) => {
        port.postMessage(envelope);
        if (!envelope.plugin_seq) return;
        lastSentSequenceRef.current = Math.max(
          lastSentSequenceRef.current,
          envelope.plugin_seq
        );
      });
    }
    window.addEventListener("message", onWindowMessage);
    return () => window.removeEventListener("message", onWindowMessage);
  }, [onCommand, roomId]);

  useEffect(() => {
    const latest = envelopes[envelopes.length - 1];
    if (!latest || !portRef.current) return;
    if (latest.type === "plugin.error") {
      setError(latest.message || latest.code || "plugin error");
    } else if (latest.type === "plugin.snapshot") {
      setError("");
    }
    envelopes.forEach((envelope) => {
      if (envelope.plugin_seq && envelope.plugin_seq <= lastSentSequenceRef.current) return;
      portRef.current?.postMessage(envelope);
      if (envelope.plugin_seq) lastSentSequenceRef.current = envelope.plugin_seq;
    });
  }, [envelopes]);

  return (
    <div className="dc-plugin-stage" data-plugin="rimworld">
      <div className="dc-plugin-stage-toolbar">
        <strong>RimWorld Survival Slice</strong>
        <button type="button" onClick={onOpenSideChat}>
          보조 채팅
        </button>
        {error ? <span className="dc-plugin-error">{error}</span> : null}
      </div>
      <iframe
        ref={iframeRef}
        className="dc-plugin-frame"
        title="RimWorld plugin"
        sandbox="allow-scripts"
        src="/plugins/rimworld/web/index.html"
      />
    </div>
  );
}
