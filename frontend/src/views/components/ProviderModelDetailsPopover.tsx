import { useEffect, useState, type FocusEvent, type MouseEvent } from "react";
import { createPortal } from "react-dom";

import type { ProviderControlOption } from "../../roomSocketClient";

type DetailState = {
  option: ProviderControlOption;
  left: number;
  top: number;
};

type DetailItem = {
  label: string;
  value: string;
};

const CARD_WIDTH = 224;
const CARD_GAP = 7;

export function useProviderModelDetails(enabled: boolean) {
  const [detail, setDetail] = useState<DetailState | null>(null);

  useEffect(() => {
    if (!enabled) setDetail(null);
  }, [enabled]);

  function show(option: ProviderControlOption, anchor: HTMLElement) {
    const items = providerModelDetailItems(option);
    if (!enabled || items.length === 0) {
      setDetail(null);
      return;
    }
    const rect = anchor.getBoundingClientRect();
    const estimatedHeight = 50 + items.length * 24;
    const opensRight = window.innerWidth - rect.right >= CARD_WIDTH + CARD_GAP + 8;
    setDetail({
      option,
      left: opensRight
        ? rect.right + CARD_GAP
        : Math.max(8, rect.left - CARD_WIDTH - CARD_GAP),
      top: Math.min(
        Math.max(8, rect.top),
        Math.max(8, window.innerHeight - estimatedHeight - 8)
      ),
    });
  }

  function bind(option: ProviderControlOption) {
    if (!enabled) return {};
    return {
      onMouseEnter: (event: MouseEvent<HTMLElement>) =>
        show(option, event.currentTarget),
      onMouseLeave: () => setDetail(null),
      onFocus: (event: FocusEvent<HTMLElement>) => show(option, event.currentTarget),
      onBlur: () => setDetail(null),
    };
  }

  return {
    bind,
    hide: () => setDetail(null),
    popover:
      detail &&
      createPortal(
        <aside
          className="dc-agent-model-details-popover"
          style={{ left: detail.left, top: detail.top, width: CARD_WIDTH }}
          aria-hidden="true"
        >
          <strong className="truncate preserve-words">{detail.option.label}</strong>
          <dl>
            {providerModelDetailItems(detail.option).map((item) => (
              <div key={item.label}>
                <dt>{item.label}</dt>
                <dd>{item.value}</dd>
              </div>
            ))}
          </dl>
        </aside>,
        document.body
      ),
  };
}

function providerModelDetailItems(option: ProviderControlOption): DetailItem[] {
  const metadata = option.metadata || {};
  const items: DetailItem[] = [];
  addNumber(items, "Context", metadata.context_length, " tokens");
  addNumber(items, "응답 상한", metadata.max_output_tokens, " tokens");
  if (metadata.pricing === "free") items.push({ label: "요금", value: "무료" });
  addText(items, "입력", metadata.input_price_per_million, "$", "/M");
  addText(items, "출력", metadata.output_price_per_million, "$", "/M");
  addSupport(items, "추론", metadata.reasoning);
  addSupport(items, "비전", metadata.vision);
  addPlainText(items, "학습", metadata.training_policy);
  addPlainText(items, "로깅", metadata.logging_policy);
  return items;
}

function addNumber(items: DetailItem[], label: string, value: unknown, suffix: string) {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return;
  items.push({ label, value: `${value.toLocaleString()}${suffix}` });
}

function addText(
  items: DetailItem[],
  label: string,
  value: unknown,
  prefix: string,
  suffix: string
) {
  if (typeof value !== "string" || !value.trim()) return;
  items.push({ label, value: `${prefix}${value.trim()}${suffix}` });
}

function addSupport(items: DetailItem[], label: string, value: unknown) {
  if (typeof value !== "boolean") return;
  items.push({ label, value: value ? "지원" : "미지원" });
}

function addPlainText(items: DetailItem[], label: string, value: unknown) {
  if (typeof value !== "string" || !value.trim()) return;
  items.push({ label, value: value.trim() });
}
