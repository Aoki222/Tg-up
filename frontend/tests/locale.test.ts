import { beforeEach, expect, test } from "vitest";
import { locale, resolveInitialLocale, setLocale, t } from "../src/i18n";

beforeEach(() => {
  localStorage.removeItem("uploader_lang");
  locale.value = "en";
});

test("首次打开用英文，只有记住的选择才生效", () => {
  expect(resolveInitialLocale(null)).toBe("en");
  expect(resolveInitialLocale("")).toBe("en");
  expect(resolveInitialLocale("fr")).toBe("en");
  expect(resolveInitialLocale("en")).toBe("en");
  expect(resolveInitialLocale("zh")).toBe("zh");
});

test("t 默认英文，切换后插值和页面语言一起变", () => {
  expect(t("header.settings")).toBe("Settings");
  expect(t("format.file", { n: 1, total: 3 })).toBe("File 1/3");
  expect(document.documentElement.lang).toBe("en");

  setLocale("zh");
  expect(t("header.settings")).toBe("设置");
  expect(t("format.file", { n: 1, total: 3 })).toBe("文件 1/3");
  expect(localStorage.getItem("uploader_lang")).toBe("zh");
  expect(document.documentElement.lang).toBe("zh-CN");

  setLocale("en");
  expect(document.documentElement.lang).toBe("en");
  expect(localStorage.getItem("uploader_lang")).toBe("en");
});
