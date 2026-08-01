import { useCallback, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, ChevronRight, Search } from "lucide-react";

import type { ProviderControlOption } from "../../roomSocketClient";
import {
  filterProviderControlOptions,
  groupProviderControlOptions,
  isFreeProviderOption,
  type ProviderOptionGroup,
} from "./providerModelOptions";
import "./ProviderControlSelect.css";

type MenuPosition = {
  left: number;
  top: number;
  width: number;
};

/** Size an open menu in whole rows so the last one is never sliced in half.
 *
 * Row height is not a constant: options carrying a description are taller, and
 * the visible set changes as the search text and the free-only filter change.
 * Measuring once when the element mounts left the wrong row height behind
 * whenever any of that moved, so the observer re-measures the first row on
 * every layout change, including window resizes.
 */
function useWholeRowMenu() {
  const observerRef = useRef<ResizeObserver | null>(null);

  useEffect(() => () => observerRef.current?.disconnect(), []);

  return useCallback((node: HTMLElement | null) => {
    observerRef.current?.disconnect();
    if (!node) {
      observerRef.current = null;
      return;
    }
    const measure = () => {
      const row = node.querySelector("button");
      const height = row?.getBoundingClientRect().height ?? 0;
      if (height > 0) node.style.setProperty("--dc-select-row", `${height}px`);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    // The menu itself catches list changes; the first row catches a row growing
    // taller when descriptions appear.
    observer.observe(node);
    const row = node.querySelector("button");
    if (row) observer.observe(row);
    observerRef.current = observer;
  }, []);
}

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
  const snapMenu = useWholeRowMenu();
  const snapSubMenu = useWholeRowMenu();
  const [open, setOpen] = useState(false);
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);
  const [activeGroup, setActiveGroup] = useState("");
  const [subMenuPosition, setSubMenuPosition] = useState<MenuPosition | null>(null);
  const [query, setQuery] = useState("");
  const [freeOnly, setFreeOnly] = useState(false);
  const listboxId = useId();
  const controlRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const subMenuRef = useRef<HTMLDivElement>(null);
  const selectedOption = options.find((option) => option.value === value);
  const hasOnlyResolvedOption = options.length === 1 && Boolean(selectedOption);
  const controlDisabled = disabled || options.length === 0 || hasOnlyResolvedOption;
  const isModelControl = label === "모델";
  const showModelTools = isModelControl && options.length > 1;
  const hasFreeOptions = options.some(isFreeProviderOption);
  const filteredOptions = filterProviderControlOptions(label, options, query, freeOnly);
  const optionGroups = groupProviderControlOptions(label, filteredOptions);
  const showGroupLabels = !query.trim() && optionGroups.length > 1;

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

  useEffect(() => {
    setActiveGroup("");
    setSubMenuPosition(null);
  }, [freeOnly, query]);

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
      (showGroupLabels ? optionGroups.length * 36 : filteredOptions.length * optionHeight) +
        (showModelTools ? 50 : 8)
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

  function openSubMenu(group: ProviderOptionGroup, target: HTMLButtonElement) {
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
    setQuery("");
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
              className="dc-agent-select-popover"
              style={menuPosition}
            >
              {showModelTools && (
                <div className="dc-agent-model-tools">
                  <label className="dc-agent-model-search">
                    <Search size={15} aria-hidden="true" />
                    <input
                      type="search"
                      aria-label="모델 검색"
                      value={query}
                      placeholder={`${options.length.toLocaleString()}개 모델 검색`}
                      onChange={(event) => setQuery(event.currentTarget.value)}
                    />
                  </label>
                  {hasFreeOptions && (
                    <button
                      type="button"
                      className="dc-agent-model-free-filter"
                      aria-label="무료 모델만 보기"
                      aria-pressed={freeOnly}
                      data-active={freeOnly}
                      onClick={() => setFreeOnly((current) => !current)}
                    >
                      무료
                    </button>
                  )}
                </div>
              )}
              <div
                id={listboxId}
                ref={snapMenu}
                className="dc-agent-select-menu"
                role={showGroupLabels ? "menu" : "listbox"}
                aria-label={showGroupLabels ? `${label} 분류` : label}
              >
              {filteredOptions.length === 0 ? (
                <p className="dc-agent-model-empty" role="status">조건에 맞는 모델이 없습니다.</p>
              ) : showGroupLabels
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
                        className="dc-agent-select-group"
                        aria-haspopup="listbox"
                        aria-expanded={activeGroup === group.label}
                        data-selected={containsSelected}
                        onClick={(event) => openSubMenu(group, event.currentTarget)}
                        onMouseEnter={(event) => openSubMenu(group, event.currentTarget)}
                      >
                        <span className="truncate preserve-words">{group.label}</span>
                        <ChevronRight className="dc-agent-select-group-arrow" size={15} aria-hidden="true" />
                      </button>
                    );
                  })
                : filteredOptions.map((option) => {
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
                    ref={(node) => {
                      subMenuRef.current = node;
                      snapSubMenu(node);
                    }}
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
