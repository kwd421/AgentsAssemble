import { fetchJson, fileToBase64, postJson } from "./http";

export type PersonaAssetSummary = {
  id: string;
  display_name: string;
  asset_kind: "card" | "module";
  source_kind?: string;
  lorebook_count: number;
  asset_count: number;
  ignored_feature_count: number;
  tag_count: number;
  thumbnail_url?: string;
};

export async function fetchPersonaAssets(): Promise<PersonaAssetSummary[]> {
  const payload = await fetchJson<{ items?: PersonaAssetSummary[] }>("/api/personas");
  return Array.isArray(payload.items) ? payload.items : [];
}

export async function importPersonaAsset(file: File): Promise<PersonaAssetSummary> {
  const dataBase64 = await fileToBase64(file);
  const payload = await postJson<{ persona: PersonaAssetSummary }>("/api/personas/import", {
    filename: file.name,
    data_base64: dataBase64,
  });
  return payload.persona;
}
