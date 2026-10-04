import { decode, hasPngSignature } from "fast-png";
import { HttpError } from "./http.js";

const MAX_PNG_BYTES = 1_100_000;
export const ICON_REQUEST_BYTES = Math.ceil(MAX_PNG_BYTES / 3) * 4 + 1024;
const PREFIX = "data:image/png;base64,";
const invalid = () => new HttpError(400, "invalid_server_icon");

export async function iconPng(value) {
  if (typeof value !== "string" || !value.startsWith(PREFIX)) throw invalid();
  const base64 = value.slice(PREFIX.length);
  if (base64.length > Math.ceil(MAX_PNG_BYTES / 3) * 4) {
    throw new HttpError(413, "server_icon_too_large");
  }
  if (base64.length % 4 || /[^A-Za-z0-9+/=]/.test(base64)) throw invalid();
  try {
    const raw = atob(base64);
    if (raw.length > MAX_PNG_BYTES) throw new HttpError(413, "server_icon_too_large");
    const bytes = Uint8Array.from(raw, ch => ch.charCodeAt(0));
    const { compressed, scanlineBytes } = inspectPng(bytes);
    // Bound expansion before the full codec allocates pixels or inflates metadata.
    const reader = new Blob([compressed]).stream().pipeThrough(new DecompressionStream("deflate")).getReader();
    let length = 0;
    try {
      for (;;) {
        const next = await reader.read();
        if (next.done) break;
        length += next.value.byteLength;
        if (length > scanlineBytes) throw invalid();
      }
    } finally {
      await reader.cancel();
      reader.releaseLock();
    }
    if (length !== scanlineBytes) throw invalid();
    const decoded = decode(bytes, { checkCrc: true });
    if (decoded.data.length !== 512 * 512 * decoded.channels) throw invalid();
    return bytes;
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw invalid();
  }
}

function inspectPng(bytes) {
  if (bytes.length < 57 || !hasPngSignature(bytes)) throw invalid();
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (view.getUint32(8) !== 13 || String.fromCharCode(...bytes.subarray(12, 16)) !== "IHDR" ||
      view.getUint32(16) !== 512 || view.getUint32(20) !== 512 || bytes[24] !== 8 ||
      ![2, 6].includes(bytes[25]) || bytes[26] || bytes[27] || bytes[28]) throw invalid();
  const chunks = [];
  let total = 0;
  let count = 0;
  let ended = false;
  let hasExif = false;
  for (let offset = 33; offset < bytes.length;) {
    if (offset + 12 > bytes.length || ++count > 128) throw invalid();
    const size = view.getUint32(offset);
    const end = offset + size + 12;
    if (end > bytes.length) throw invalid();
    const type = String.fromCharCode(...bytes.subarray(offset + 4, offset + 8));
    if (type === "IDAT") {
      if (ended) throw invalid();
      const data = bytes.subarray(offset + 8, end - 4);
      chunks.push(data); total += size;
    } else if (type === "IEND") {
      if (size !== 0 || end !== bytes.length || !total) throw invalid();
      const compressed = new Uint8Array(total);
      let position = 0;
      for (const chunk of chunks) { compressed.set(chunk, position); position += chunk.length; }
      return { compressed, scanlineBytes: 512 * (512 * (bytes[25] === 6 ? 4 : 3) + 1) };
    } else if (type === "eXIf") {
      // WebKit's canvas exporter adds uncompressed Exif (68 bytes in the verified
      // app). Bound one pre-IDAT chunk; fast-png skips its payload and checks CRC.
      if (hasExif || chunks.length || size < 8 || size > 4096) throw invalid();
      hasExif = true;
    } else {
      // Canvas PNG colour/density metadata is fixed-size; no animation or compressed ancillary data.
      if (chunks.length) ended = true;
      if (size !== ({ sRGB: 1, gAMA: 4, cHRM: 32, pHYs: 9 })[type]) throw invalid();
    }
    offset = end;
  }
  throw invalid();
}
