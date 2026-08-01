import { useEffect, useMemo, useState } from "react";
import { FileUser, PackageOpen, Upload } from "lucide-react";
import {
  fetchPersonaAssets,
  importPersonaAsset,
  type PersonaAssetSummary,
} from "../../api/personas";
import "./AgentPersonaPicker.css";

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
  const [status, setStatus] = useState("불러오는 중...");
  const [importing, setImporting] = useState(false);

  useEffect(() => {
    let active = true;
    fetchPersonaAssets()
      .then((nextItems) => {
        if (!active) return;
        setItems(nextItems);
        setStatus(nextItems.length ? "" : "가져온 봇카드나 모듈이 없습니다.");
      })
      .catch((error) => {
        if (!active) return;
        setStatus(error instanceof Error ? error.message : "라이브러리를 불러오지 못했습니다.");
      });
    return () => {
      active = false;
    };
  }, []);

  const visibleItems = useMemo(() => {
    if (!applied || items.some((item) => item.id === applied.id)) return items;
    return [applied, ...items];
  }, [applied, items]);

  async function handleImport(file: File) {
    setImporting(true);
    setStatus("가져오는 중...");
    try {
      const imported = await importPersonaAsset(file);
      setItems((current) => [imported, ...current.filter((item) => item.id !== imported.id)]);
      onChange(imported.id);
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

      <div className="dc-persona-grid" role="radiogroup" aria-label="봇카드 또는 Risu 모듈">
        <button
          type="button"
          role="radio"
          aria-checked={!value}
          data-selected={!value}
          disabled={disabled}
          onClick={() => onChange("")}
        >
          <span className="dc-persona-symbol" data-kind="none">—</span>
          <span className="dc-persona-copy">
            <strong>적용 안 함</strong>
            <small>기본 모델 성격 사용</small>
          </span>
          {!value && <em>선택됨</em>}
        </button>
        {visibleItems.map((item) => {
          const selected = value === item.id;
          const Icon = item.asset_kind === "module" ? PackageOpen : FileUser;
          return (
            <button
              key={item.id}
              type="button"
              role="radio"
              aria-checked={selected}
              data-selected={selected}
              disabled={disabled}
              onClick={() => onChange(item.id)}
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
      {status && <p className="dc-persona-status preserve-words">{status}</p>}
      <p className="dc-persona-safety preserve-words">
        실행형 스크립트·정규식·트리거는 보관만 하며 실행하지 않습니다.
      </p>
    </div>
  );
}
