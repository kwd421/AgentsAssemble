import { useEffect, useRef, useState } from "react";

type PluginEnvelope = {
  type: string;
  plugin_id?: string;
  payload?: Record<string, unknown>;
  message?: string;
  code?: string;
  revision?: string;
};

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
};

export default function RimWorldPluginView({
  roomId,
  onCommand,
  envelopes,
  onOpenSideChat,
}: RimWorldPluginViewProps) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const portRef = useRef<MessagePort | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    function onWindowMessage(event: MessageEvent) {
      if (event.source !== iframeRef.current?.contentWindow) return;
      const data = event.data as { type?: string; plugin_id?: string } | null;
      if (data?.type !== "plugin.web.ready" || data.plugin_id !== "rimworld") return;
      const port = event.ports[0];
      if (!port) return;
      portRef.current = port;
      port.onmessage = (portEvent: MessageEvent) => {
        const message = portEvent.data as PluginEnvelope;
        if (message?.type === "plugin.command") {
          onCommand({
            plugin_id: "rimworld",
            command: String((message as { command?: string }).command || ""),
            args: (message as { args?: Record<string, unknown> }).args,
            revision: (message as { revision?: string }).revision,
          });
        }
      };
      port.start();
      iframeRef.current?.contentWindow?.postMessage(
        { type: "plugin.host.hello", room_id: roomId },
        "*"
      );
    }
    window.addEventListener("message", onWindowMessage);
    return () => window.removeEventListener("message", onWindowMessage);
  }, [onCommand, roomId]);

  useEffect(() => {
    const latest = envelopes[envelopes.length - 1];
    if (!latest || !portRef.current) return;
    if (latest.type === "plugin.error") {
      setError(latest.message || latest.code || "plugin error");
    }
    portRef.current.postMessage(latest);
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
