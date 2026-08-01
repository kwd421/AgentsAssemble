import { useEffect, useMemo, useState } from "react";
import { ChevronDown, FileUser, PackageOpen, Search, Upload } from "lucide-react";
import {
  fetchPersonaAssets,
  importPersonaAsset,
  type PersonaAssetSummary,
} from "../../api/personas";
import "./AgentPersonaPicker.css";

const VISIBLE_RESULT_LIMIT = 8;

export default function AgentPersonaPicker({
  value,
  applied,
  disabled = false,
  onChange,
}: {
  value: string;
  applied?: PersonaAssetSummary;
  disabled?: boolean;
  onChange: (personaId: string) => void;
}) {
  const [items, setItems] = useState<PersonaAssetSummary[]>([]);
  const [status, setStatus] = useState("");
  const [importing, setImporting] = useState(false);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (!libraryOpen || loaded) return;
    let active = true;
    setStatus("불러오는 중...");
    fetchPersonaAssets()
      .then((nextItems) => {
        if (!active) return;
        setItems(nextItems);
        setLoaded(true);
        // An empty library is explained inside the list itself, so leaving a
        // status line here as well said the same thing twice.
        setStatus("");
      })
      .catch((error) => {
        if (!active) return;
        setStatus(error instanceof Error ? error.message : "라이브러리를 불러오지 못했습니다.");
      });
    return () => {
      active = false;
    };
  }, [libraryOpen, loaded]);

  const libraryItems = useMemo(() => {
    if (!applied || items.some((item) => item.id === applied.id)) return items;
    return [applied, ...items];
  }, [applied, items]);
  const selectedItem = useMemo(
    () => libraryItems.find((item) => item.id === value) || (applied?.id === value ? applied : undefined),
    [applied, libraryItems, value]
  );
  const matchingItems = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return libraryItems;
    return libraryItems.filter((item) =>
      [item.display_name, item.asset_kind === "module" ? "Risu 모듈" : "봇카드"]
        .join(" ")
        .toLocaleLowerCase()
        .includes(needle)
    );
  }, [libraryItems, query]);
  const visibleItems = matchingItems.slice(0, VISIBLE_RESULT_LIMIT);
  const searching = Boolean(query.trim());
  // With nothing imported there is only one thing to choose, and it is already
  // named on the trigger. Showing a search field, a repeat of that one row and
  // a "no results" line for it made an empty library look like a busy one.
  const emptyLibrary = loaded && libraryItems.length === 0;

  async function handleImport(file: File) {
    setImporting(true);
    setStatus("가져오는 중...");
    try {
      const imported = await importPersonaAsset(file);
      setItems((current) => [imported, ...current.filter((item) => item.id !== imported.id)]);
      setLoaded(true);
      onChange(imported.id);
      setLibraryOpen(false);
      setQuery("");
      setStatus(`${imported.display_name} 가져오기 완료`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "가져오기에 실패했습니다.");
    } finally {
      setImporting(false);
    }
  }

  return (
    <div className="dc-persona-picker">
      <div className="dc-persona-picker-head">
        <div>
          <strong>봇카드 · Risu 모듈</strong>
          <span>API/Local 모델의 캐릭터와 세계관에 적용됩니다.</span>
        </div>
        <label className="dc-persona-import" data-disabled={disabled || importing}>
          <Upload size={15} aria-hidden="true" />
          {importing ? "가져오는 중" : "파일 가져오기"}
          <input
            className="sr-only"
            type="file"
            accept=".json,.png,.apng,.charx,.risum"
            disabled={disabled || importing}
            onChange={(event) => {
              const file = event.currentTarget.files?.[0];
              event.currentTarget.value = "";
              if (file) void handleImport(file);
            }}
          />
        </label>
      </div>

      <button
        type="button"
        className="dc-persona-current"
        aria-expanded={libraryOpen}
        disabled={disabled}
        onClick={() => {
          setLibraryOpen((current) => !current);
          setQuery("");
        }}
      >
        <span className="dc-persona-symbol" data-kind={selectedItem?.asset_kind || "none"}>
          {selectedItem?.thumbnail_url ? (
            <img src={selectedItem.thumbnail_url} alt="" />
          ) : selectedItem?.asset_kind === "module" ? (
            <PackageOpen size={19} aria-hidden="true" />
          ) : selectedItem ? (
            <FileUser size={19} aria-hidden="true" />
          ) : (
            "—"
          )}
        </span>
        <span className="dc-persona-copy">
          <strong>{selectedItem?.display_name || "적용 안 함"}</strong>
          <small>
            {selectedItem
              ? `${selectedItem.asset_kind === "module" ? "Risu 모듈" : "봇카드"}${
                  selectedItem.lorebook_count ? ` · 로어 ${selectedItem.lorebook_count}` : ""
                }`
              : "기본 모델 성격 사용"}
          </small>
        </span>
        <span className="dc-persona-current-action">
          {value ? "변경" : "선택"}
          <ChevronDown size={16} aria-hidden="true" />
        </span>
      </button>

      {libraryOpen && (
        <div className="dc-persona-library">
          {emptyLibrary ? (
            <p className="dc-persona-empty preserve-words">
              아직 가져온 봇카드나 Risu 모듈이 없습니다. 위의 <strong>파일 가져오기</strong>로
              추가하면 여기에서 고를 수 있습니다.
            </p>
          ) : (
            <>
              {libraryItems.length > VISIBLE_RESULT_LIMIT && (
                <label className="dc-persona-search">
                  <Search size={16} aria-hidden="true" />
                  <input
                    autoFocus
                    value={query}
                    placeholder="봇카드 또는 모듈 검색"
                    onChange={(event) => setQuery(event.currentTarget.value)}
                  />
                </label>
              )}
              <div className="dc-persona-grid" role="radiogroup" aria-label="봇카드 또는 Risu 모듈">
                {/* Clearing the selection is only an option when something is
                    selected; otherwise it repeats what the trigger already says. */}
                {value && !searching && (
                  <button
                    type="button"
                    role="radio"
                    aria-checked={false}
                    data-selected="false"
                    onClick={() => {
                      onChange("");
                      setLibraryOpen(false);
                    }}
                  >
                    <span className="dc-persona-symbol" data-kind="none">—</span>
                    <span className="dc-persona-copy">
                      <strong>적용 안 함</strong>
                      <small>기본 모델 성격 사용</small>
                    </span>
                  </button>
                )}
                {visibleItems.map((item) => {
                  const selected = value === item.id;
                  const Icon = item.asset_kind === "module" ? PackageOpen : FileUser;
                  return (
                    <button
                      key={item.id}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      data-selected={selected ? "true" : "false"}
                      onClick={() => {
                        onChange(item.id);
                        setLibraryOpen(false);
                      }}
                    >
                      <span className="dc-persona-symbol" data-kind={item.asset_kind}>
                        {item.thumbnail_url ? (
                          <img src={item.thumbnail_url} alt="" />
                        ) : (
                          <Icon size={19} aria-hidden="true" />
                        )}
                      </span>
                      <span className="dc-persona-copy">
                        <strong>{item.display_name}</strong>
                        <small>
                          {item.asset_kind === "module" ? "Risu 모듈" : "봇카드"}
                          {item.lorebook_count ? ` · 로어 ${item.lorebook_count}` : ""}
                        </small>
                      </span>
                      {selected && <em>{applied?.id === item.id ? "적용됨" : "선택됨"}</em>}
                    </button>
                  );
                })}
              </div>
              {searching && matchingItems.length === 0 && (
                <p className="dc-persona-status">
                  「{query.trim()}」에 맞는 봇카드나 모듈이 없습니다.
                </p>
              )}
              {matchingItems.length > VISIBLE_RESULT_LIMIT && (
                <p className="dc-persona-status">
                  {matchingItems.length.toLocaleString()}개 중 {VISIBLE_RESULT_LIMIT}개만
                  표시합니다. 검색어로 좁혀보세요.
                </p>
              )}
            </>
          )}
        </div>
      )}
      {status && <p className="dc-persona-status preserve-words">{status}</p>}
      <p className="dc-persona-safety preserve-words">
        실행형 스크립트·정규식·트리거는 보관만 하며 실행하지 않습니다.
      </p>
    </div>
  );
}
