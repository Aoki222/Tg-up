import { ref } from "vue";
import { fetchIdentity, type IdentityInfo } from "../api";

/** 顶栏和设置页共用。null 表示还没读到，false 才显示未配置提示。 */
const configured = ref<boolean | null>(null);

export function useTelegramIdentity() {
  async function refresh(): Promise<IdentityInfo | null> {
    try {
      const info = await fetchIdentity();
      configured.value = Boolean(info.configured);
      return info;
    } catch {
      return null;
    }
  }

  return { configured, refresh };
}
