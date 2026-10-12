import { test as base, expect } from "@playwright/test";
import { createWorld, installApi, type World } from "./helpers/world";

export const test = base.extend<{ world: World }>({
  // 每个用例都要拦住 /api，否则请求会打到 Vite 代理并得到 500。
  world: [async ({ page }, use) => {
    await page.addInitScript(() => {
      localStorage.removeItem("uploader_api_token");
      localStorage.removeItem("uploader.success-scope");
    });
    const world = createWorld();
    await installApi(page, world);
    await use(world);
    world.settle();
  }, { auto: true }],
});

export { expect };
