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

## 关于 MakeMKV Key（重要）

- **DVD**：无加密，**不需要 key**，开箱即用。
- **蓝光**：AACS 加密，**需要有效的 MakeMKV key**。免费 beta key 会过期（官网论坛定期更新）。
  - 方式一：把 key 内容保存到 `config/keydata.txt`，重启容器（entrypoint 会写入 `/root/.MakeMKV/settings.conf`）。
  - 方式二：设置环境变量 `MAKEMKV_KEY`（整行 key 字符串）。
  - key 获取：MakeMKV 官网论坛的 beta key 帖（搜索 "MakeMKV beta key"）。
  - 正式使用环境可联网时，也可自行写脚本定期更新 key 文件。

## 构建与启动

```bash
cd iso-splitter
mkdir -p config
# 蓝光用户: 把 key 放进 config/keydata.txt
docker compose build
docker compose up -d
```

打开 `http://服务器IP:8088`。

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

## 目录约定

- `/config`：配置、扫描缓存、任务历史、日志、keydata.txt
- 其余目录由你通过 `-v` 挂载，容器内路径任意（页面会自动发现挂载点）

## 备注

- MakeMKV 为 beta 软件，Dockerfile 固定版本 2.0.0；若 key 过期导致蓝光识别失败，更新 key 即可，新版 MakeMKV 也会重置 60 天试用。
- 分割是**无损 remux**，不转码，速度取决于磁盘 IO，通常远快于实时。
