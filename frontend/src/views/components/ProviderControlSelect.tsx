import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, ChevronRight } from "lucide-react";

import type { ProviderControlOption } from "../../roomSocketClient";

type MenuPosition = {
  left: number;
  top: number;
  width: number;
};

type OptionGroup = {
  label: string;
  options: ProviderControlOption[];
};

const MODEL_FAMILY_LABELS = [
  ["haiku", "Haiku"],
  ["sonnet", "Sonnet"],
  ["opus", "Opus"],
  ["fable", "Fable"],
  ["gpt", "GPT"],
  ["gemini", "Gemini"],
  ["grok", "Grok"],
  ["deepseek", "DeepSeek"],
  ["qwen", "Qwen"],
  ["glm", "GLM"],
  ["kimi", "Kimi"],
  ["nemotron", "Nemotron"],
  ["llama", "Llama"],
] as const;

export default function ProviderControlSelect({
  label,
  options,
  value,
  disabled = false,
  onChange,
}: {
  label: string;
  options: ProviderControlOption[];
  value: string;
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);
  const [activeGroup, setActiveGroup] = useState("");
  const [subMenuPosition, setSubMenuPosition] = useState<MenuPosition | null>(null);
  const listboxId = useId();
  const controlRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const subMenuRef = useRef<HTMLDivElement>(null);
  const selectedOption = options.find((option) => option.value === value);
  const hasOnlyResolvedOption = options.length === 1 && Boolean(selectedOption);
  const controlDisabled = disabled || options.length === 0 || hasOnlyResolvedOption;
  const optionGroups = groupOptions(label, options);
  const showGroupLabels = optionGroups.length > 1;

  useEffect(() => {
    if (!open) return;
    const close = (event: Event) => {
      const target = event.target;
      if (
        target instanceof Node &&
        !controlRef.current?.contains(target) &&
        !menuRef.current?.contains(target) &&
        !subMenuRef.current?.contains(target)
      ) {
        setOpen(false);
        setActiveGroup("");
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (activeGroup) {
          setActiveGroup("");
          setSubMenuPosition(null);
          return;
        }
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    const closeOnViewportChange = (event: Event) => {
      const target = event.target;
      if (
        target instanceof Node &&
        (menuRef.current?.contains(target) || subMenuRef.current?.contains(target))
      ) {
        return;
      }
      setOpen(false);
      setActiveGroup("");
      setSubMenuPosition(null);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", closeOnEscape);
    window.addEventListener("resize", closeOnViewportChange);
    window.addEventListener("scroll", closeOnViewportChange, true);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("resize", closeOnViewportChange);
      window.removeEventListener("scroll", closeOnViewportChange, true);
    };
  }, [activeGroup, open]);

  useEffect(() => {
    if (controlDisabled) {
      setOpen(false);
      setActiveGroup("");
    }
  }, [controlDisabled]);

  function toggleMenu() {
    if (controlDisabled || !buttonRef.current) return;
    if (open) {
      setOpen(false);
      setActiveGroup("");
      return;
    }
    const rect = buttonRef.current.getBoundingClientRect();
    const optionHeight = options.some(hasOptionDescription) ? 50 : 36;
    const estimatedHeight = Math.min(
      240,
      (showGroupLabels ? optionGroups.length * 36 : options.length * optionHeight) + 8
    );
    const spaceBelow = window.innerHeight - rect.bottom - 8;
    const spaceAbove = rect.top - 8;
    const openAbove = spaceBelow < estimatedHeight && spaceAbove > spaceBelow;
    setMenuPosition({
      left: rect.left,
      top: openAbove
        ? Math.max(8, rect.top - estimatedHeight - 6)
        : rect.bottom + 6,
      width: rect.width,
    });
    setOpen(true);
  }

  function openSubMenu(group: OptionGroup, target: HTMLButtonElement) {
    const rect = target.getBoundingClientRect();
    const width = menuPosition?.width || rect.width;
    const rightLeft = rect.right + 6;
    const left =
      rightLeft + width <= window.innerWidth - 8
        ? rightLeft
        : Math.max(8, rect.left - width - 6);
    const optionHeight = group.options.some(hasOptionDescription) ? 50 : 36;
    const estimatedHeight = Math.min(240, group.options.length * optionHeight + 8);
    setActiveGroup(group.label);
    setSubMenuPosition({
      left,
      top: Math.min(
        Math.max(8, rect.top - 4),
        Math.max(8, window.innerHeight - estimatedHeight - 8)
      ),
      width,
    });
  }

  function selectOption(option: ProviderControlOption) {
    onChange(option.value);
    setOpen(false);
    setActiveGroup("");
    setSubMenuPosition(null);
    buttonRef.current?.focus();
  }

  return (
    <div className="dc-agent-select" ref={controlRef}>
      <button
        ref={buttonRef}
        type="button"
        className="dc-agent-select-trigger"
        role="combobox"
        aria-label={label}
        aria-controls={listboxId}
        aria-expanded={open}
        aria-haspopup={showGroupLabels ? "menu" : "listbox"}
        data-effect={optionEffect(selectedOption)}
        disabled={controlDisabled}
        onClick={toggleMenu}
      >
        {selectedOption ? (
          <OptionContent option={selectedOption} />
        ) : (
          <span className="truncate preserve-words">선택 필요</span>
        )}
        <ChevronDown size={15} aria-hidden="true" />
      </button>
      {open &&
        menuPosition &&
        createPortal(
          <>
            <div
              ref={menuRef}
              id={listboxId}
              className="dc-agent-select-menu"
              role={showGroupLabels ? "menu" : "listbox"}
              aria-label={showGroupLabels ? `${label} 분류` : label}
              style={menuPosition}
            >
              {showGroupLabels
                ? optionGroups.map((group) => {
                    if (group.options.length === 1) {
                      const option = group.options[0];
                      const selected = option.value === value;
                      return (
                        <button
                          key={group.label}
                          type="button"
                          role="menuitemradio"
                          aria-label={optionAccessibleName(option)}
                          aria-checked={selected}
                          data-selected={selected}
                          data-effect={optionEffect(option)}
                          onMouseEnter={() => {
                            setActiveGroup("");
                            setSubMenuPosition(null);
                          }}
                          onClick={() => selectOption(option)}
                        >
                          <OptionContent option={option} showDescription />
                          {selected && <Check size={15} aria-hidden="true" />}
                        </button>
                      );
                    }
                    const containsSelected = group.options.some(
                      (option) => option.value === value
                    );
                    return (
                      <button
                        key={group.label}
                        type="button"
                        role="menuitem"
                        aria-haspopup="listbox"
                        aria-expanded={activeGroup === group.label}
                        data-selected={containsSelected}
                        onClick={(event) => openSubMenu(group, event.currentTarget)}
                        onMouseEnter={(event) => openSubMenu(group, event.currentTarget)}
                      >
                        <span className="truncate preserve-words">{group.label}</span>
                        <ChevronRight size={15} aria-hidden="true" />
                      </button>
                    );
                  })
                : options.map((option) => {
                  const selected = option.value === value;
                  return (
                    <button
                      key={option.value || "default"}
                      type="button"
                      role="option"
                      aria-label={optionAccessibleName(option)}
                      aria-selected={selected}
                      data-selected={selected}
                      data-effect={optionEffect(option)}
                      onClick={() => selectOption(option)}
                    >
                      <OptionContent option={option} showDescription />
                      {selected && <Check size={15} aria-hidden="true" />}
                    </button>
                  );
                })}
            </div>
            {showGroupLabels &&
              activeGroup &&
              subMenuPosition &&
              (() => {
                const group = optionGroups.find(
                  (candidate) => candidate.label === activeGroup
                );
                if (!group) return null;
                return (
                  <div
                    ref={subMenuRef}
                    className="dc-agent-select-menu dc-agent-select-submenu"
                    role="listbox"
                    aria-label={`${group.label} 모델`}
                    style={subMenuPosition}
                  >
                    {group.options.map((option) => {
                      const selected = option.value === value;
                      return (
                        <button
                          key={option.value || "default"}
                          type="button"
                          role="option"
                          aria-label={optionAccessibleName(option)}
                          aria-selected={selected}
                          data-selected={selected}
                          data-effect={optionEffect(option)}
                          onClick={() => selectOption(option)}
                        >
                          <OptionContent option={option} showDescription />
                          {selected && <Check size={15} aria-hidden="true" />}
                        </button>
                      );
                    })}
                  </div>
                );
              })()}
          </>,
          document.body
        )}
    </div>
  );
}

function groupOptions(
  controlLabel: string,
  options: ProviderControlOption[]
): OptionGroup[] {
  if (controlLabel !== "모델") return [{ label: "", options }];
  const groups = new Map<string, ProviderControlOption[]>();
  for (const option of options) {
    const family = modelFamily(option) || "기타";
    groups.set(family, [...(groups.get(family) || []), option]);
  }
  if (groups.size <= 1) return [{ label: "", options }];
  return [...groups].map(([label, groupOptions]) => ({
    label,
    options: groupOptions,
  }));
}

function modelFamily(option: ProviderControlOption): string {
  const explicitGroup = option.metadata?.group;
  if (typeof explicitGroup === "string" && explicitGroup.trim()) {
    return explicitGroup.trim();
  }
  const explicitFamily = option.metadata?.family;
  if (typeof explicitFamily === "string" && explicitFamily.trim()) {
    return explicitFamily.trim();
  }
  const normalized = `${option.value} ${option.label}`.toLowerCase();
  for (const [token, label] of MODEL_FAMILY_LABELS) {
    if (new RegExp(`(^|[^a-z0-9])${token}([^a-z0-9]|$)`).test(normalized)) {
      return label;
    }
  }
  return "";
}

function OptionContent({
  option,
  showDescription = false,
}: {
  option: ProviderControlOption;
  showDescription?: boolean;
}) {
  const badges = optionBadges(option);
  const description =
    showDescription && typeof option.metadata?.description === "string"
      ? option.metadata.description.trim()
      : "";
  return (
    <span className="dc-agent-select-option-content">
      <span className="dc-agent-select-option-copy">
        <span className="truncate preserve-words">{option.label}</span>
        {description && (
          <small className="truncate preserve-words">{description}</small>
        )}
      </span>
      <span className="dc-agent-select-option-trailing">
        {optionEffect(option) === "ultra" && (
          <small className="dc-agent-select-ultra-badge">Ultra</small>
        )}
        {badges.length > 0 && (
          <span className="dc-agent-select-badges">
            {badges.map((badge) => (
              <small key={badge}>{badge}</small>
            ))}
          </span>
        )}
      </span>
    </span>
  );
}

function optionEffect(option?: ProviderControlOption): string {
  return option?.metadata?.effect === "ultra" ? "ultra" : "";
}

function hasOptionDescription(option: ProviderControlOption): boolean {
  return (
    typeof option.metadata?.description === "string" &&
    Boolean(option.metadata.description.trim())
  );
}

function optionBadges(option: ProviderControlOption): string[] {
  const badges: string[] = [];
  const configured = option.metadata?.badges;
  if (Array.isArray(configured)) {
    for (const value of configured) {
      if (typeof value === "string" && value.trim()) badges.push(value.trim());
    }
  }
  if (option.metadata?.pricing === "free") badges.push("Free");
  if (option.metadata?.pricing === "free_tier") badges.push("Free tier");
  if (option.metadata?.execution_location === "cloud") badges.push("Cloud");
  if (option.metadata?.execution_location === "local") badges.push("Local");
  return [...new Set(badges)];
}

function optionAccessibleName(option: ProviderControlOption): string {
  return [option.label, ...optionBadges(option)].join(" ");
}
