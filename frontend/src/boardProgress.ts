/**
 * 看板实时进度怎么并进已有行。纯函数，方便单测，不碰轮询和 ghost。
 */
import { isAlbumProgress } from "./format";
import type { BoardTask, UploadProgress } from "./types";

export function slicePartChanged(
  existing: Pick<BoardTask, "part_index">,
  progress: Pick<UploadProgress, "part_index">,
): boolean {
  // 第一次看到段号只记下，不把已经显示的字节打回 0。
  if (progress.part_index == null || existing.part_index == null) return false;
  return progress.part_index !== existing.part_index;
}

function hasByteProgress(task: BoardTask): boolean {
  return task.total > 32 && !isAlbumProgress(task.current, task.total) && task.current > 0;
}

export function keepByteProgress(existing: BoardTask, current: number, total: number): boolean {
  // 文件个数和更旧的字节数不能把已经显示的字节进度打回去。
  if (!hasByteProgress(existing)) return false;
  if (isAlbumProgress(current, total)) return true;
  return current < existing.current;
}

/** 同一条任务上合并一条上传中的进度。成功、失败和 ghost 仍由调用方处理。 */
export function mergeLiveBytes(existing: BoardTask, progress: UploadProgress): BoardTask {
  const nextStatus =
    existing.status === "oversized"
      ? existing.status
      : progress.stage === "uploading"
        ? "uploading"
        : progress.stage === "flood_wait"
          ? "pending"
          : existing.status;

  // 仍在上传中时，更旧的字节或相册回调不能把条打回去。
  // 任务已经回到等待后再开始，新的 0 是另一次上传，要接受。
  // 切片换到下一段时，字节从这段的开头重新计。
  const partChanged = slicePartChanged(existing, progress);
  const restart =
    progress.current <= 0 && existing.status !== "uploading" && existing.status !== "assigned";
  if (keepByteProgress(existing, progress.current, progress.total) && !restart && !partChanged) {
    return {
      ...existing,
      status: nextStatus,
      assigned_worker: progress.worker_name || existing.assigned_worker,
      message: progress.message || existing.message,
      part_index: progress.part_index ?? existing.part_index,
      part_count: progress.part_count ?? existing.part_count,
    };
  }

  return {
    ...existing,
    status: nextStatus,
    percent: progress.percent,
    current: progress.current,
    total: progress.total,
    speed_bps: progress.speed_bps ?? 0,
    eta_seconds: progress.eta_seconds ?? -1,
    stage: progress.stage,
    message: progress.message,
    assigned_worker: progress.worker_name || existing.assigned_worker,
    part_index: progress.part_index ?? existing.part_index,
    part_count: progress.part_count ?? existing.part_count,
  };
}
