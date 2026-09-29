/**
 * 会话元数据。打开添加弹窗、以及增删账号之后才读，不跟 Worker 的 1 秒轮询串在一起。
 */
import { ref } from "vue";
import { fetchSessionMeta } from "../api";
import type { SessionAccount, SessionMeta } from "../types";

const accounts = ref<SessionAccount[]>([]);
const names = ref<string[]>([]);
let inflight: Promise<SessionMeta> | null = null;

function remember(meta: SessionMeta): SessionMeta {
  accounts.value = meta.accounts ?? [];
  names.value = meta.items ?? [];
  return meta;
}

export function refreshSessionAccounts(): Promise<SessionMeta> {
  if (inflight) return inflight;
  const pending = fetchSessionMeta()
    .then(remember)
    .finally(() => {
      inflight = null;
    });
  inflight = pending;
  return pending;
}

/** 等正在进行的读取结束后再读一次，避免增删刚完成却拼到旧结果上。 */
export async function reloadSessionAccounts(): Promise<SessionMeta> {
  const pending = inflight;
  if (pending) {
    try {
      await pending;
    } catch {
      // 上一轮失败不挡住这次重读。
    }
  }
  return refreshSessionAccounts();
}

export function useSessionAccounts() {
  return { accounts, names, refreshSessionAccounts, reloadSessionAccounts };
}
