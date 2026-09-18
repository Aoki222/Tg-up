/**
 * 字节、速度、剩余时间的展示格式。遥测带和任务卡片共用。
 */

export function isAlbumProgress(current: number, total: number): boolean {
  return total > 0 && total <= 32 && current <= total;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function formatSpeed(bytesPerSec: number): string {
  if (bytesPerSec <= 0) return "0 B/s";
  return `${formatBytes(bytesPerSec)}/s`;
}

export function formatETA(seconds: number): string {
  if (seconds < 0) return "--:--";
  if (seconds > 9 * 3600) return "> 9h";
  if (seconds > 3600) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function formatTransferred(current: number, total: number): string {
  if (isAlbumProgress(current, total)) {
    return `文件 ${Math.max(1, Math.ceil(current))}/${Math.round(total)}`;
  }
  return `${formatBytes(current)} / ${formatBytes(total)}`;
}
