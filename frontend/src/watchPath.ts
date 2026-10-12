import type { ObserverRootItem } from "./types";

/** 新加的监听根默认暂停。打开这一行的开关之后才会扫描。 */
export function pausedObserverRoot(path: string): ObserverRootItem {
  return {
    path,
    enabled: false,
    ok: true,
    error: "",
  };
}
