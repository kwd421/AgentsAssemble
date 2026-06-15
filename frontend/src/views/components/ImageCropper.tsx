import { useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

type ImageCropperProps = {
  file: File;
  onCancel: () => void;
  onCropped: (file: File) => void;
};

const MIN_SCALE = 1;
const MAX_SCALE = 4;

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    const timeoutId = window.setTimeout(() => reject(new Error("이미지를 불러오지 못했습니다.")), 8000);
    image.addEventListener("load", () => {
      window.clearTimeout(timeoutId);
      resolve(image);
    });
    image.addEventListener("error", () => {
      window.clearTimeout(timeoutId);
      reject(new Error("이미지를 불러오지 못했습니다."));
    });
    image.src = src;
  });
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

export default function ImageCropper({ file, onCancel, onCropped }: ImageCropperProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const previewRef = useRef<HTMLDivElement | null>(null);
  const pointersRef = useRef<Map<number, { x: number; y: number }>>(new Map());
  const pinchDistanceRef = useRef(0);
  const [objectUrl, setObjectUrl] = useState("");
  const [scale, setScale] = useState(1.2);
  const [offsetX, setOffsetX] = useState(0);
  const [offsetY, setOffsetY] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [status, setStatus] = useState("");
  const previewStyle = useMemo(
    () => ({
      backgroundImage: objectUrl ? `url("${objectUrl}")` : undefined,
      backgroundSize: `${scale * 100}%`,
      backgroundPosition: `${50 + offsetX}% ${50 + offsetY}%`,
      cursor: dragging ? "grabbing" : "grab",
      touchAction: "none" as const,
    }),
    [dragging, objectUrl, offsetX, offsetY, scale]
  );

  useEffect(() => {
    const url = URL.createObjectURL(file);
    setObjectUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  // Wheel zoom needs a non-passive listener so the page doesn't scroll/zoom.
  useEffect(() => {
    const element = previewRef.current;
    if (!element) return;
    function handleWheel(event: WheelEvent) {
      event.preventDefault();
      const step = event.deltaY * -0.0025;
      setScale((previous) => clamp(previous * (1 + step), MIN_SCALE, MAX_SCALE));
    }
    element.addEventListener("wheel", handleWheel, { passive: false });
    return () => element.removeEventListener("wheel", handleWheel);
  }, []);

  function applyDrag(dx: number, dy: number) {
    const rect = previewRef.current?.getBoundingClientRect();
    const width = rect?.width || 200;
    const height = rect?.height || 200;
    // Dragging follows the finger: moving right shows more of the left side.
    setOffsetX((previous) => clamp(previous - (dx / width) * 100, -50, 50));
    setOffsetY((previous) => clamp(previous - (dy / height) * 100, -50, 50));
  }

  function pinchDistance(): number {
    const points = Array.from(pointersRef.current.values());
    if (points.length < 2) return 0;
    return Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);
  }

  function handlePointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    event.currentTarget.setPointerCapture(event.pointerId);
    pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    pinchDistanceRef.current = pinchDistance();
    setDragging(true);
  }

  function handlePointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const previous = pointersRef.current.get(event.pointerId);
    if (!previous) return;
    pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointersRef.current.size >= 2) {
      const distance = pinchDistance();
      if (pinchDistanceRef.current > 0 && distance > 0) {
        const ratio = distance / pinchDistanceRef.current;
        setScale((current) => clamp(current * ratio, MIN_SCALE, MAX_SCALE));
      }
      pinchDistanceRef.current = distance;
      return;
    }
    applyDrag(event.clientX - previous.x, event.clientY - previous.y);
  }

  function handlePointerEnd(event: ReactPointerEvent<HTMLDivElement>) {
    pointersRef.current.delete(event.pointerId);
    pinchDistanceRef.current = pinchDistance();
    if (pointersRef.current.size === 0) setDragging(false);
  }

  async function cropImage() {
    if (!objectUrl) return;
    setStatus("이미지 처리 중...");
    try {
      const sourceImage = await loadImage(objectUrl);
      const canvas = canvasRef.current || document.createElement("canvas");
      canvas.width = 512;
      canvas.height = 512;
      const context = canvas.getContext("2d");
      if (!context) throw new Error("이미지 편집 캔버스를 사용할 수 없습니다.");
      context.clearRect(0, 0, canvas.width, canvas.height);
      context.fillStyle = "#111214";
      context.fillRect(0, 0, canvas.width, canvas.height);

      const baseSize = Math.min(sourceImage.width, sourceImage.height);
      const cropSize = baseSize / scale;
      const maxX = Math.max(0, sourceImage.width - cropSize);
      const maxY = Math.max(0, sourceImage.height - cropSize);
      // Same sign as the preview's background-position so the saved crop is
      // exactly what the preview showed (the slider version was mirrored).
      const sourceX = clamp((sourceImage.width - cropSize) / 2 + (offsetX / 100) * maxX, 0, maxX);
      const sourceY = clamp((sourceImage.height - cropSize) / 2 + (offsetY / 100) * maxY, 0, maxY);

      context.save();
      context.beginPath();
      context.arc(256, 256, 256, 0, Math.PI * 2);
      context.clip();
      context.drawImage(sourceImage, sourceX, sourceY, cropSize, cropSize, 0, 0, 512, 512);
      context.restore();
      let completed = false;
      const timeoutId = window.setTimeout(() => {
        if (!completed) setStatus("이미지 처리 실패");
      }, 8000);
      canvas.toBlob((blob) => {
        completed = true;
        window.clearTimeout(timeoutId);
        if (!blob) {
          setStatus("이미지 처리 실패");
          return;
        }
        onCropped(new File([blob], `profile-${Date.now()}.png`, { type: "image/png" }));
      }, "image/png");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "이미지 처리 실패");
    }
  }

  return (
    <div className="dc-image-cropper">
      <div
        ref={previewRef}
        className="dc-image-crop-preview"
        style={previewStyle}
        aria-label="프로필 사진 미리보기 (드래그로 이동, 휠 또는 핀치로 확대/축소)"
        role="img"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerEnd}
        onPointerCancel={handlePointerEnd}
      />
      <canvas ref={canvasRef} className="hidden" aria-hidden />
      <p className="dc-image-crop-hint">드래그로 위치 이동 · 휠(⌘/Ctrl+휠)로 확대/축소 · 모바일은 핀치 줌</p>
      <div className="dc-image-crop-actions">
        <button type="button" className="dc-member-session-button" onClick={cropImage}>
          적용
        </button>
        <button type="button" className="dc-member-session-button" onClick={onCancel}>
          취소
        </button>
      </div>
      {status && <p className="dc-member-session-status preserve-words">{status}</p>}
    </div>
  );
}
