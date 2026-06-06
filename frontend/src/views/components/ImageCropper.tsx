import { useEffect, useMemo, useRef, useState } from "react";

type ImageCropperProps = {
  file: File;
  onCancel: () => void;
  onCropped: (file: File) => void;
};

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

export default function ImageCropper({ file, onCancel, onCropped }: ImageCropperProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [objectUrl, setObjectUrl] = useState("");
  const [scale, setScale] = useState(1.2);
  const [offsetX, setOffsetX] = useState(0);
  const [offsetY, setOffsetY] = useState(0);
  const [status, setStatus] = useState("");
  const previewStyle = useMemo(
    () => ({
      backgroundImage: objectUrl ? `url("${objectUrl}")` : undefined,
      backgroundSize: `${scale * 100}%`,
      backgroundPosition: `${50 + offsetX}% ${50 + offsetY}%`,
    }),
    [objectUrl, offsetX, offsetY, scale]
  );

  useEffect(() => {
    const url = URL.createObjectURL(file);
    setObjectUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

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
      const sourceX = Math.min(maxX, Math.max(0, (sourceImage.width - cropSize) / 2 - (offsetX / 100) * maxX));
      const sourceY = Math.min(maxY, Math.max(0, (sourceImage.height - cropSize) / 2 - (offsetY / 100) * maxY));

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
      <div className="dc-image-crop-preview" style={previewStyle} aria-label="프로필 사진 미리보기" />
      <canvas ref={canvasRef} className="hidden" aria-hidden />
      <label>
        확대/축소
        <input
          type="range"
          min="1"
          max="3"
          step="0.05"
          value={scale}
          onChange={(event) => setScale(Number(event.currentTarget.value))}
        />
      </label>
      <label>
        좌우 위치
        <input
          type="range"
          min="-50"
          max="50"
          step="1"
          value={offsetX}
          onChange={(event) => setOffsetX(Number(event.currentTarget.value))}
        />
      </label>
      <label>
        상하 위치
        <input
          type="range"
          min="-50"
          max="50"
          step="1"
          value={offsetY}
          onChange={(event) => setOffsetY(Number(event.currentTarget.value))}
        />
      </label>
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
