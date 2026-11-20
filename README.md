# ISO Splitter

根据 DVD / 蓝光 ISO 镜像，**无损识别并分割**为 MKV，完整保留所有音轨、字幕、章节。带 WebUI，支持 TMDB / 豆瓣刮削匹配片名与集数。

## 功能

- 多挂载点：容器启动时可挂载任意多个目录，页面内浏览选择
- ISO 扫描：递归扫描所选目录中的 `.iso`
- 光盘识别：MakeMKV 解析 title（DVD 的 VTS / 蓝光的 playlist）、时长、章节、音轨、字幕（语言+编码）
- 刮削：从文件名/目录名推断片名 → TMDB（支持反代/代理）+ 豆瓣网页 → 手动输入片名重搜 → 失败转手动
- 分割预览：按 title 列出「原文件 → 建议新名」，可勾选、改集数、改文件名
- 输出：原地（ISO 同目录 `_split` 文件夹）或指定路径
- 任务队列 + 实时进度 + 日志 + 历史
- **MakeMKV Key 一键获取**：从官方论坛抓取最新 beta key，点一下立即生效，无需重启
- **周期自动更新 Key**：后台按设定间隔自动检查并替换过期 key

## 关于 MakeMKV Key（重要）

- **DVD**：无加密，**不需要 key**，开箱即用。
- **蓝光**：AACS 加密，**需要有效的 MakeMKV key**。免费 beta key 会过期（官网论坛定期更新）。

### 推荐：WebUI 一键获取 / 自动更新（v1.1.0 新增）

在**设置页 → 「MakeMKV Key（蓝光解密）」**面板：

- 点「一键获取最新 Key」→ 自动从 MakeMKV 官方论坛抓取最新 beta key，写入 `/root/.MakeMKV/settings.conf` 并同步到 `/config/keydata.txt`，**立即生效、无需重启**。
- 勾选「周期自动更新」并设置间隔（默认 72 小时）→ 后台线程定期检查，key 过期无感替换。
- 面板显示当前 key（脱敏）、有效期、来源、最后更新时间。
- 抓取会复用设置页配置的代理；容器所在网络访问不了论坛时，先配好代理即可。

### 备选：手动放置

- 方式一：把 key 内容保存到 `config/keydata.txt`，重启容器（entrypoint 会写入 `/root/.MakeMKV/settings.conf`）。
- 方式二：设置环境变量 `MAKEMKV_KEY`（整行 key 字符串）。
- key 获取来源：MakeMKV 官网论坛的 beta key 帖（搜索 "MakeMKV beta key"）。

## 构建与启动

```bash
cd iso-splitter
mkdir -p config
# 蓝光用户可跳过：现在推荐在 WebUI 设置页一键获取 key
docker compose build
docker compose up -d
```

打开 `http://服务器IP:8088`。

从 ghcr 部署（无需本地构建）见 [`docker-compose.ghcr.yml`](docker-compose.ghcr.yml)：

```bash
docker compose -f docker-compose.ghcr.yml up -d
```

## 使用流程

1. **目录管理**：确认挂载点（创建容器时用 `-v` 挂载），可浏览目录，点选加入扫描目录。
2. **ISO 扫描**：点「扫描 ISO」→ 找到所有 iso。
3. **识别**：点单行「识别」或「批量识别」→ 得到 title 列表与轨道信息。
4. **刮削**：点「刮削匹配」→ 选择正确片名/剧集（自动加载分集分配集数）；识别不准可手动输入片名重搜；都不行就「手动分割」。
5. **预览**：勾选要导出的 title，可改输出文件名、选输出方式。
6. **分割**：点「开始分割」→ 到「任务进度」看实时状态。

## 设置项

- TMDB API Key / API 地址（可填反代，解决网络不通）/ 代理 / 语言 / 豆瓣开关
- 剧集与电影命名模板（变量：`{title}` `{year}` `{season}` `{episode}` `{epname}`）
- 最小时长（过滤预告/花絮）、默认输出方式
- MakeMKV Key：一键获取、周期自动更新开关与间隔（小时）

## 目录约定

- `/config`：配置、扫描缓存、任务历史、日志、keydata.txt、key_state.json
- 其余目录由你通过 `-v` 挂载，容器内路径任意（页面会自动发现挂载点）

## 版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| `v1.1.3` | 2026-11-20 | 识别失败页新增 ISO 结构预检（BDMV/VIDEO_TS/ISO9660/UDF），直接判断数据盘/二次打包/损坏镜像 |
| `v1.1.2` | 2026-11-20 | 修正识别失败诊断：区分「未加密盘但无 title」（ISO 结构问题）与真正的 key 错误 |
| `v1.1.1` | 2026-11-20 | 识别失败时显示 MakeMKV 原始错误与实时 key 状态，移除误导性的固定 key 提示 |
| `v1.1.0` | 2026-11-20 | 新增 MakeMKV Key 一键获取与周期自动更新；抓取复用已配置代理 |
| `v1.0.0` | 2026-11-20 | 初始版本（对应 commit `55a86c5`，未单独打标签） |

镜像标签：

- `ghcr.io/songboxiang/splitter:latest` — 默认分支最新构建
- `ghcr.io/songboxiang/splitter:1.1.3` — 固定语义化版本（打 `v*` 标签触发）
- `ghcr.io/songboxiang/splitter:1.1.0` — 固定语义化版本（打 `v*` 标签触发）
- `ghcr.io/songboxiang/splitter:sha-<commit>` — 每次提交对应版本

生产环境建议固定到具体版本标签，避免 `latest` 意外变更。

## 备注

- MakeMKV 为 beta 软件，Dockerfile 固定版本 2.0.0；若 key 过期导致蓝光识别失败，更新 key 即可，新版 MakeMKV 也会重置 60 天试用。
- 分割是**无损 remux**，不转码，速度取决于磁盘 IO，通常远快于实时。
