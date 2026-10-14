/**
 * 界面语言。默认英文。只有用户点过切换才写入 localStorage，两个浏览器可以不同。
 * 不读 upload.toml，后端 detail 仍按原文显示。
 */

import { computed, ref, watch } from "vue";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import enLocale from "element-plus/es/locale/lang/en";
import { en, type MessageKey } from "./en";
import { zh } from "./zh";

export type { MessageKey };
export type Locale = "en" | "zh";

const STORAGE_KEY = "uploader_lang";

export function resolveInitialLocale(stored: string | null): Locale {
  if (stored === "en" || stored === "zh") return stored;
  return "en";
}

function readStored(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export const locale = ref<Locale>(resolveInitialLocale(readStored()));

export function setLocale(next: Locale): void {
  locale.value = next;
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // 无痕模式写不进去时，这次会话里仍然切换。
  }
}

watch(
  locale,
  (value) => {
    if (typeof document !== "undefined") {
      document.documentElement.lang = value === "zh" ? "zh-CN" : "en";
    }
  },
  { immediate: true, flush: "sync" },
);

const catalogs = { en, zh };

export function t(key: MessageKey, params?: Record<string, string | number>): string {
  let text: string = catalogs[locale.value][key];
  if (params) {
    for (const [name, value] of Object.entries(params)) {
      text = text.replaceAll(`{${name}}`, String(value));
    }
  }
  return text;
}

export const elementLocale = computed(() => (locale.value === "zh" ? zhCn : enLocale));
