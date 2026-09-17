# 发版流程

> 真相源：根 `package.json` 的 `version`。发版 = 改一个数字 + push。

## 一句话

```bash
# 1. 改根 package.json 的 version，比如 0.1.0 → 0.1.1
# 2. 把版本号分发到各消费点（可选，CI 也会做；本地打包前必须做）
npm run sync-version
# 3. 提交 + push 到 main
git add -A && git commit -m "chore: bump to 0.1.1" && git push
```

CI 摸到 `package.json` 有改动 → **自动打包 → 自动建 tag `v0.1.1` → 自动发 Release**。不需要手打 tag。

## 版本号去哪儿了

根 `package.json` 是唯一手改点。`npm run sync-version` 把它分发到 5 个地方——**这 5 个文件不要手改**，改了会被下次同步覆盖：

| 文件 | 谁读 | 什么时候读 |
|---|---|---|
| `electron/package.json` | electron-builder | 构建时（决定 Setup.exe 文件名、exe 属性） |
| `app/core/config.py` | 后端 `/health`、OpenAPI | **运行时**（所以打包前必须同步，PyInstaller 冻结的是同步后的源码） |
| `pyproject.toml` | uv | 构建时 |
| `uv.lock` | uv（`--frozen` 会校验它与 pyproject 一致） | 构建时——**不同步会让 CI 直接失败** |
| `frontend/package.json` | 当前无人读 | — |

## 触发条件

Release workflow 只在 **`push` 到 main 且 `package.json` 有改动**时跑。平时改代码不会误发版。

## 幂等

同一版本号重复触发不会重复发版：

- **Release 已存在** → 整个 job 跳过。
- 若上次「打包成功、tag 已推、但建 Release 挂了」→ 远程剩个孤儿 tag，下次重跑会重新构建并补上 Release（判据是 Release 不是 tag）。

## 手动打包（不发版）

Actions 页面点 `Release` → `Run workflow`：

- `version` 填目标版本号（留空 = 读根 `package.json` 的当前值）
- **不勾** `create_release` → 只打包，产物走 Artifacts，不对外发版

勾了 `create_release` 才建正式 Release。

## 本地打包

本地 `npm run build`（在 `electron/`）**不会**自动同步版本号——先手动跑一次：

```bash
npm run sync-version    # 仓库根
```

否则打出来的安装包文件名/exe 属性用的还是上次的值，跟根 `package.json` 对不上。

**别手改 `uv.lock`**：同步脚本会改它（uv 的 `--frozen` 要求它与 `pyproject.toml` 版本一致）。若手工改了 `pyproject.toml` 却没同步，跑 `uv lock --check` 会报「needs to be updated」，CI 同理会因此失败。

## 换下一个版本

`v0.1.0` 已发过。**必须 bump 到 `v0.1.1` 才能触发**——CI 看到 `v0.1.0` 的 Release 已存在会直接跳过，不会报错（这是幂等闸门，不是故障）。
