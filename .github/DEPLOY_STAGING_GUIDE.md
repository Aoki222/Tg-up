# VPS 测试环境与分阶段发布操作指南

本项目采用 **“分阶段验证与发布（Staged Verification & Promotion）”** CI/CD 流水线：
1. **测试阶段**：代码推送到 `main` 分支后，GitHub Actions 仅构建并发布 `:staging` 镜像。
2. **验证阶段**：在测试 VPS 上拉取 `:staging` 镜像进行人工验证与功能测试。
3. **晋级发布**：测试通过后，在 GitHub Actions 页面手动触发 `Promote Staging to Latest` 工作流，秒级将 `:staging` 晋级为 `:latest`（及可选的版本号 Tag）。

---

## 一、首次配置：VPS 端 GHCR 鉴权

GitHub Container Registry (`ghcr.io`) 默认可能为私有（Private）。

### 1.1 如果 Package 设为 Private（私有）
需要在 VPS 上完成一次性登录：
1. 在 GitHub 个人设置中生成一个 Personal Access Token (Classic)：
   - 路径：`GitHub -> Settings -> Developer settings -> Personal access tokens (classic)`
   - 勾选权限：`read:packages`
2. 在 VPS 终端执行登录：
   ```bash
   echo "YOUR_GITHUB_TOKEN" | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
   ```
   > 提示：登录凭据会被保存在 `~/.docker/config.json`，以后无需重复登录。

### 1.2 如果 Package 设为 Public（公开）
可将 GHCR 包设置为公开：
- 路径：`https://github.com/users/Aoki222/packages/container/tg-up/settings`
- 在页面最下方的 **Danger Zone** 中将 Package visibility 修改为 **Public**。
- 设置为 Public 后，任何 VPS 均可免登录直接 `docker pull`。

---

## 二、VPS 测试环境部署与平滑更新

VPS 测试端已提供专门的配置文件：`docker-compose.staging.yml`。

### 2.1 首次部署
```bash
# 1. 准备配置文件（若已有 .env 请跳过此步）
cp .env.example .env
nano .env   # 填入 API_ID, API_HASH 等配置

# 2. 拉取 staging 镜像
docker compose -f docker-compose.staging.yml pull

# 3. 启动容器
docker compose -f docker-compose.staging.yml up -d
```

### 2.2 日常平滑更新（有新代码推送到 main 之后）
当 GitHub Actions 完成 `build-staging.yml` 构建后，在 VPS 运行以下命令即可无缝切换最新镜像：

```bash
# 1. 静默拉取最新 staging 镜像层
docker compose -f docker-compose.staging.yml pull

# 2. 平滑重启（仅在镜像 hash 发生变化时重建并替换容器）
docker compose -f docker-compose.staging.yml up -d --remove-orphans

# 3. 检查容器状态与健康检查（等待约 20s 进入 healthy 状态）
docker compose -f docker-compose.staging.yml ps

# 4. 查看实时运行日志
docker compose -f docker-compose.staging.yml logs -f --tail=100
```

### 2.3 测试环境回滚（如测试发现重大 Bug）
每次构建除了打 `:staging` 外，流水线还会打一个 SHA 追溯标签（例如 `:staging-7abc123`）。
若当前 `:staging` 有问题需要快速退回到上一版本，只需临时编辑 `docker-compose.staging.yml`：
```yaml
image: ghcr.io/aoki222/tg-up:staging-上一个提交短SHA
```
然后执行 `docker compose -f docker-compose.staging.yml up -d` 即可秒级回退。

---

## 三、正式晋级发布（Promotion）

当您在 VPS 上完成功能测试并确认当前 `:staging` 镜像工作正常后，即可将其晋级为正式版本：

1. 打开 GitHub 仓库页面：`https://github.com/Aoki222/Tg-up/actions`
2. 在左侧工作流列表中点击 **`Promote Staging to Latest`**。
3. 点击右侧的 **`Run workflow`** 下拉按钮：
   - **Branch**: 保持 `main`
   - **发布版本号 Tag (可选)**：
     - 若只想更新 `:latest`：直接留空。
     - 若同时需要固定版本号：输入版本号（例如 `v1.0.0` 或 `1.0.0`）。
4. 点击绿色的 **Run workflow** 按钮启动任务。

### 晋级原理解析
晋级工作流底层调用：
```bash
docker buildx imagetools create \
  --tag ghcr.io/aoki222/tg-up:latest \
  --tag ghcr.io/aoki222/tg-up:v1.0.0 \
  ghcr.io/aoki222/tg-up:staging
```
- **纯 Registry 端操作**：直接由 GHCR 复制并生成镜像 Manifest 指针，**耗时仅 3~5 秒**。
- **完全杜绝二次构建**：不拉取源码、不重新编译前端或打包 Python，确保正式生产环境运行的二进制内容与测试 VPS 上验证通过的内容 100% 字节一致。

---

## 四、生产 VPS 部署建议

正式生产环境使用项目自带的 `compose.yml`，镜像直接指向正式标签：
```yaml
services:
  uploader:
    image: ghcr.io/aoki222/tg-up:latest
    # 或者指定固定版本: ghcr.io/aoki222/tg-up:v1.0.0
    container_name: uploader
    restart: unless-stopped
    ports:
      - "8000:8000"
    ...
```
生产环境更新命令与测试环境相同：
```bash
docker compose pull && docker compose up -d --remove-orphans
```
