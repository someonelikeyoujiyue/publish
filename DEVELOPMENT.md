# Publisher Hub — 开发计划 & 注意事项

> 多用户内容推送系统：消费 newmedia 抓取的原帖，仿写后按用户推送到各自的微信公众号 / 小红书。
>
> 最后更新：2026-05-07

---

## 1. 项目目标（一句话）

为运营团队搭建一个**无登录、按 URL 区分用户**的内容推送 Web 系统：
后台从 `newmedia.posts` 表按用户配置的来源筛选原帖 → 仿写 → 入库 → 前端展示给对应用户 →
用户点按钮推到自己的微信公众号草稿箱 / 用手机扫码确认发布小红书。

---

## 2. 与 newmedia 的关系（独立性边界）

| 维度 | 说明 |
|---|---|
| 代码 | **零依赖**：`pyproject.toml` 不引用 newmedia，仿写/微信发布全部独立实现（思路参考但不 import） |
| 数据库 | **共用**：连同一个 MySQL（`47.236.168.208:3306/newmedia`），只读 `posts` 表，新建 `hub_drafts` 表写入 |
| 抓取 | **不做**：publisher-hub 不爬数据，不用 Playwright/CDP，不用 newmedia 的 BrowserManager |
| 仿写 | **自己做**：调 LLM API 生成新草稿存入 `hub_drafts`，逻辑独立于 newmedia.RewritePipeline |
| 部署 | 独立进程、独立端口（:8900），可与 newmedia 并行运行 |

---

## 3. 架构数据流

```
┌─────────────────────────┐
│ newmedia.posts (只读)   │  ← newmedia 持续抓 youtube/facebook/xhs/douyin 写入
└──────────┬──────────────┘
           │
   按用户配置过滤
   (platform, category)
           │
┌──────────▼──────────────┐
│ publisher_hub.rewrite   │  ← LLM 仿写（per_post 或 batch 综合）
└──────────┬──────────────┘
           │
┌──────────▼──────────────┐
│ hub_drafts 表 (新建)     │  ← user_id × platform 维度的草稿池
└──────────┬──────────────┘
           │
┌──────────▼──────────────┐
│ FastAPI 前端展示         │  ← /{user_id}/wechat、/{user_id}/xhs
└──────────┬──────────────┘
           │
   ┌───────┴────────┐
   │                │
   ▼                ▼
微信草稿箱       myaibot 二维码
(独立实现)       (用户扫码)
   │                │
   └───────┬────────┘
           ▼
      飞书 webhook 通知
```

---

## 4. 关键决策（已敲定，不要再改）

| 项 | 决定 | 备注 |
|---|---|---|
| 项目位置 | `/Users/fengxiaoji/code/publisher-hub/` | |
| 端口 | `:8900` | 避开 newmedia 的 :8899 |
| 技术栈 | FastAPI + Jinja2 + HTMX + APScheduler + pymysql + httpx | 不上 Vue/React |
| 数据源 | `newmedia.posts` 表（只读 SQL，不 import newmedia） | |
| 数据落地 | 同库新建 `hub_drafts`（前缀 `hub_` 标记独立） | |
| 多用户 | yaml 配置；用户增删手动改文件 | 不做 UI 管理 |
| 用户视野 | 全部用户都能看到所有用户的草稿（共享视图） | 不做权限隔离 |
| 仿写来源 | `posts` 表，**不消费** `newmedia.rewrites` | |
| 仿写 LLM | `deepseek-reasoner` via valueclue（同 newmedia 的 key） | 慢但质量好 |
| 仿写模式 | 默认批量综合（10 条 → N 篇），可切 per_post | 用户配置 `rewrite_mode` |
| 推送时机 | 微信草稿、小红书二维码均**留 cron 接口但暂不启用** | 配置 `push_cron: ""` 跳过 |
| 飞书提醒 | 推送/仿写完成后 webhook 卡片 | |
| 登录 | 无 | URL 带 user_id 即个人页 |

---

## 5. 数据库 Schema

### 5.1 复用：`newmedia.posts`（只读）

关键字段（仅列 publisher-hub 用到的）：

```
id                   INT       主键
platform             VARCHAR   youtube/instagram/facebook/xiaohongshu/douyin
post_id              VARCHAR   平台侧 ID
nickname             VARCHAR   作者
category             VARCHAR   原帖分类（学校官方、泰国留学等）
title                TEXT
content              TEXT
translated_title     TEXT      泰文已翻译版本（外部平台）
translated_content   TEXT
cover_url            TEXT      逗号分隔多张 CDN URL
cover_local_path     VARCHAR   服务器本地路径
attachment_local_path VARCHAR  XHS 多图本地路径（逗号分隔）
discovered_at        DATETIME  抓取时间
published_at         DATETIME  原帖发布时间
```

### 5.2 新建：`hub_drafts`

```sql
CREATE TABLE hub_drafts (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    user_id         VARCHAR(50)  NOT NULL,         -- config.yaml 中的 user.id
    platform        ENUM('wechat','xhs') NOT NULL,
    source_post_id  INT          NOT NULL,         -- newmedia.posts.id
    source_post_ids TEXT,                          -- batch 模式：逗号分隔多个 post_id
    title           TEXT,
    content         LONGTEXT,
    image_urls      TEXT,                          -- 逗号分隔，前端展示和推送用
    status          ENUM('ready','pushed','failed') DEFAULT 'ready',
    pushed_at       DATETIME,
    pushed_result   TEXT,                          -- 微信 media_id 或 myaibot 响应 JSON
    error_msg       TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_platform_post (user_id, platform, source_post_id),
    INDEX idx_user_platform_status (user_id, platform, status),
    INDEX idx_created (created_at)
);
```

**约束含义**：
- `UNIQUE (user_id, platform, source_post_id)` —— 同一原帖不重复仿写给同一用户同一平台
- batch 模式下 `source_post_id` 取批次首条的 id（参考 newmedia 做法），完整列表存 `source_post_ids`

---

## 6. 配置文件

### 6.1 `config.yaml`

完整示例见 `config.example.yaml`，结构总览：

```yaml
mysql:           # 共用 newmedia 的 MySQL
llm:             # 仿写 LLM（valueclue + deepseek-reasoner）
myaibot:         # 小红书发布服务（共用一个 token）
feishu:          # 全局 webhook
users:           # 用户列表
  - id: alice
    wechat: {...}    # app_id/secret/proxy + sources/prompt/rewrite_cron/push_cron
    xhs: {...}       # display_name + sources/prompt/rewrite_cron/push_cron
```

### 6.2 `prompts.yaml`

独立维护一份，初始模板可参考 newmedia 的 `src/newmedia/pipeline/prompts.yaml`。
模板按名引用（`templates.wechat_article`、`templates.xhs_note`），`format()` 占位符保持一致：
- batch 模式：`{posts}` `{post_count}` `{article_index}`
- per_post 模式：`{title}` `{content}`

---

## 7. 开发计划

### Phase 1 — 后端基座（命令行可跑）

**目标**：跑 `python -m publisher_hub.rewrite alice` 能从 posts 仿写并写入 hub_drafts。

任务：
1. `pyproject.toml` 依赖：fastapi、uvicorn、jinja2、httpx、apscheduler、pyyaml、pymysql、markdown2、pillow、python-multipart
2. `publisher_hub/config.py` —— 加载 yaml；提供 `get_user(id)`、`list_users()`
3. `publisher_hub/db.py` —— 连接管理；自动 `CREATE TABLE IF NOT EXISTS hub_drafts`；
   提供 `get_posts_for_user(user_id, platform)`、`save_draft(...)`、`list_drafts(user_id, platform)`
4. `publisher_hub/rewrite.py` —— `RewriteEngine`：
   - `run_user(user_id, platform)` 入口
   - 内部按 `rewrite_mode` 分发到 `_per_post` / `_batch`
   - LLM 调用走 httpx，max_tokens 留 60000+ 给 reasoner
5. CLI：`python -m publisher_hub.rewrite <user_id> [<platform>]`

**验收**：跑命令后 `SELECT * FROM hub_drafts WHERE user_id='alice'` 能看到新行。

---

### Phase 2 — 前端展示（看到内容，无推送按钮）

**目标**：浏览器打开 `http://localhost:8900/alice/wechat` 能看草稿列表。

任务：
6. `app.py` —— FastAPI 入口；启动时建表 + 装路由
7. `templates/base.html` + `home.html` + `wechat_list.html` + `xhs_list.html`
8. `routes/home.py` —— `/` 用户卡片，`/{user_id}` 重定向到 wechat
9. `routes/wechat.py` —— `/{user_id}/wechat` 列表 + `/{user_id}/wechat/{draft_id}` 详情
10. `routes/xhs.py` —— `/{user_id}/xhs` 列表 + 详情

**验收**：UI 能看到 status='ready' 的草稿；详情页能展示 markdown 渲染的内容和图片。

---

### Phase 3 — 推送（按钮可用）

**目标**：点"推送到草稿箱"能在微信公众号后台看到草稿；点"生成二维码"能 modal 弹出可扫码。

任务：
11. `publisher_hub/wechat.py` —— 独立精简版微信草稿推送：
    - access_token 缓存（per app_id）
    - SOCKS5 代理（`httpx.Client(proxy=...)`）
    - 上传封面 → `/material/add_material`
    - 上传内嵌图 → `/media/uploadimg`（压缩到 1080px / 900KB）
    - md → 内联 HTML（不要 RSU 品牌耦合，用通用配色）
    - 创建草稿 → `/draft/add`
    - 不直接发布（auto_publish 永远 False）
12. `publisher_hub/xhs.py` —— myaibot 调用：
    - POST `https://www.myaibot.vip/api/rednote/publish-with-upload`
    - 提取响应里的 `qrcode_url` / `qr_url`
13. `publisher_hub/feishu.py` —— webhook 卡片（推送成功 / 失败）
14. 前端按钮 + HTMX 局部刷新；二维码 modal

**验收**：
- 微信：进 `mp.weixin.qq.com` 草稿箱能看到；状态变 `pushed`
- 小红书：modal 弹二维码 URL；用绑定的小红书 App 扫码确认；状态变 `pushed`
- 飞书群里能收到通知卡片

---

### Phase 4 — 定时调度（可选启用）

**目标**：APScheduler 启动后自动按 cron 跑仿写，推送 cron 留接口但不启用。

任务：
15. `publisher_hub/scheduler.py` ——
    - 应用启动时遍历所有用户，注册 `rewrite_cron`（空字符串跳过）
    - 同样注册 `push_cron` 但**默认全部留空** —— 代码里 `if cron: scheduler.add_job(...)` 自动跳过
    - 仿写任务：调 `RewriteEngine.run_user(user_id, platform)`
    - 推送任务（暂不启用，但代码就位）：从 ready 队列取 1 条 → push → 飞书通知
16. 加 `--cron-test` CLI：手动触发某用户的某 cron 一次

**验收**：日志显示按 cron 触发；`hub_drafts` 自动新增行。

---

## 8. 注意事项 / 坑（很重要）

### 8.1 与 newmedia 共用 MySQL
- 永远不要 `DROP` 或 `TRUNCATE` 任何不带 `hub_` 前缀的表
- `hub_drafts` 是唯一允许写的表
- newmedia 可能随时新增 `posts` 字段，SQL 用具体列名而非 `SELECT *`

### 8.2 LLM 调用（valueclue 代理）
**实测最终方案：`deepseek-v4-pro` + `max_tokens=50000` + `stream=True`**

- valueclue 代理对**非流式请求** 16.8s 后强制断连（`Empty reply from server`）；
  reasoning 类模型推理时间普遍 60-180s，所以**必须用 stream=True**（每个 chunk 重置 idle）
- `deepseek-reasoner` 在 valueclue 已被代理停用（`No provider configured`）；改用 `deepseek-v4-pro`
- `deepseek-v4-pro` 也是带 reasoning 的模型，响应里有 `delta.reasoning_content` 流，
  解析时**只拼接 `delta.content`**，跳过 reasoning_content
- `max_tokens` 给到 **50000+**（reasoning + 正文都需要）
- httpx timeout 设到 **300s**
- 不要传 `temperature` 给 reasoner 类（会 400）；当前 `if 'reasoner' not in model: temperature=0.8`，
  v4-pro 名字里没 reasoner，会带 temperature —— 实测不报错，但若切回纯 reasoner 模型要调整
- 失败要跳过当前条不卡整批

### 8.3 微信公众号 API
- `digest` 字段长度上限 **54 个汉字**，超过报 errcode 45004 —— 需要 `_extract_digest(content, max_len=54)`
- `thumb_media_id` 必须用永久素材（`/material/add_material`），不能用临时素材
- 内嵌图片 `/media/uploadimg` 大小约 10MB 上限，但要预先压缩到 1080px / 900KB 以内（避免 errcode 40009）
- API 出口要走 **SOCKS5 代理**（IP 白名单），httpx：`httpx.Client(proxy='socks5h://...')`，需要装 `socksio` 依赖
- access_token 7200 秒过期，按 app_id 缓存，提前 60 秒续期

### 8.4 myaibot.vip 小红书发布
- token 是**全用户共享一个**（不是每用户一个 token，而是同一个 token 服务所有用户的发布请求）
- 每次发布返回 base64 二维码（`data.qrcode`），用绑定的小红书 App 扫码确认
- 绑定关系在 myaibot 平台后台维护，publisher-hub 只调发布 API

**两个端点（消耗调用次数不同）**：
- `POST /api/rednote/publish` —— **消耗 1 次**调用次数；要求 images 全部为**公网可达** URL
- `POST /api/rednote/publish-with-upload` —— **消耗 2 次**调用次数；自动转存临时/防盗链链接

publisher-hub 默认用 `/publish`（我们的图片都来自 47.236.168.208:8899 公网可达）。
若以后图片来源变成临时 CDN 或带 referer 防盗链，再切到 `/publish-with-upload`。

**请求格式**（认证在 body 不在 Header）：
```json
POST {api_url}
Content-Type: application/json
{
  "api_key": "rn_xxx",
  "type":    "normal",       // normal=图文 / video=视频
  "title":   "...",          // ≤ 20 字（中文1、ASCII 0.5、emoji 多字符）
  "content": "...",          // ≤ 1000 字
  "images":  ["http://..."]  // 必须非空
}
```

**响应格式**：
```json
{
  "success": true,
  "data": {
    "id":      "uuid",
    "url":     "https://www.myaibot.vip/rednote/publish/<uuid>",   // 发布页 URL
    "qrcode":  "data:image/png;base64,..."                          // 真正的二维码图
  }
}
```

**title 长度算法**（实测 myaibot 错误信息）：
中文 1 字、ASCII 0.5 字、emoji 段（U+1F000–U+1FFFF, U+2600–U+27BF）每 codepoint 算 2 字。
`xhs.py._truncate_xhs_title()` 实现按此规则截断。

**前端二维码渲染**：
`_qr_modal.html` 检测 `qr_url` 开头：
- `data:image` → `<img src="...">` 直接渲染 base64 二维码
- `https://` → `<iframe>` 嵌入发布页（兜底）

**HTMX 防双击**：
所有推送按钮加 `hx-disabled-elt="this"`（HTMX 1.9+ 真 disabled 属性）+ `hx-confirm`，
配合 CSS `.htmx-request.btn { pointer-events:none; cursor:wait; }` 三道防线。

### 8.5 push_cron 留白机制
所有用户配置中 `push_cron: ""` 留空。`scheduler.py` 注册任务时：
```python
if cron and cron.strip():
    scheduler.add_job(...)
else:
    log.info(f"[scheduler] {user_id}/{platform} push_cron 留空，跳过")
```
未来开启时只需改 yaml，无需改代码。

### 8.6 不用 Playwright / CDP
publisher-hub 完全不爬数据，所以：
- 不需要 Chrome 浏览器
- 不需要 Cookie 维护
- 不需要 stealth.js
- 部署只需要 Python + MySQL

### 8.7 httpx 代理坑
- 用 SOCKS5 必须 `pip install socksio`（pyproject 里加 `httpx[socks]`）
- 调本地 myaibot/feishu 等公网 API 时不要走代理
- 代理只在调微信 API 时启用（per-call 创建 `httpx.Client(proxy=...)`，不要全局）

### 8.8 飞书 webhook
- webhook 全局一个；用户级别可在 `users[*].feishu_webhook` 覆盖（兼容未来需求，初版可不实现）
- 卡片用 markdown 模板，包含：用户、平台、标题、推送时间、外链（如有）
- 失败时也发一条（错误类型 + error_msg），便于排查

### 8.9 仿写去重
`UNIQUE (user_id, platform, source_post_id)` 保证同一原帖不会给同一用户同一平台重复仿写。
仿写 SQL 取数时要 `LEFT JOIN hub_drafts` 排除已仿写过的：

```sql
SELECT p.* FROM posts p
LEFT JOIN hub_drafts d
  ON d.source_post_id = p.id AND d.user_id = ? AND d.platform = ?
WHERE p.platform IN (?, ?, ?)        -- 用户配置的 platforms
  AND p.category IN (?, ?)            -- 用户配置的 categories
  AND d.id IS NULL                    -- 排除已仿写
ORDER BY p.discovered_at DESC
LIMIT ?
```

### 8.10 LLM 输出解析（"标题：xxx" 残留 bug）
- LLM 按 prompt 要求输出 `标题：xxx\n\n正文...`，但**全角冒号 `：` 必须显式列在 character class 里**
- 容易踩的坑：`[::]` 看起来像"半角冒号 + 全角冒号"，但视觉相同实则两个都是半角 `:`（U+003A）
- 正确写法：`[:：]`（U+003A + U+FF1A），或写成 `(?::|：)`
- `_parse_response` 还要兼容 markdown 装饰（`**标题：xxx**`、`# 标题：xxx`），
  策略：先 `_strip_md` 剥外层 `*#`>`` ``，再 regex 匹配 `^标题[\s*]*[:：][\s*]*(.+)$`，
  最后给 fallback 路径加保险层强制剥离

### 8.11 时区
所有时间字段统一 UTC+8（datetime 用 `datetime.now(timezone(timedelta(hours=8)))`）。
日志格式 `%Y-%m-%d %H:%M:%S`。

### 8.12 不要主动跑测试 / 不要启动服务
- 写完代码告诉用户运行什么命令，不要 `uv run` / `python -m`
- 不要尝试 `uvicorn app:app --reload`
- 不要主动建数据库表（让代码启动时自动建，但实现完不要触发它）

---

## 9. URL 路由设计

```
GET  /                         → 用户卡片首页
GET  /{user_id}                → 重定向到 /{user_id}/wechat
GET  /{user_id}/wechat         → 微信草稿列表
GET  /{user_id}/wechat/{id}    → 微信草稿详情（HTML 预览）
POST /{user_id}/wechat/{id}/push → 推送到微信草稿箱（HTMX 局部刷新）
GET  /{user_id}/xhs            → 小红书待发列表
GET  /{user_id}/xhs/{id}       → 小红书详情
POST /{user_id}/xhs/{id}/push  → 调 myaibot 生成二维码（返回 qr_url）
POST /{user_id}/rewrite/refresh → 手动触发一次仿写（HTMX）
```

---

## 10. 启动命令（Phase 完成后才能跑）

```bash
cd /Users/fengxiaoji/code/publisher-hub
uv venv && uv pip install -e .
cp config.example.yaml config.yaml      # 填入真实 app_id/secret/token
uv run python -m publisher_hub.rewrite alice    # Phase 1 验证
uv run uvicorn app:app --host 0.0.0.0 --port 8900 --reload  # Phase 2 起前端
```

---

## 11. 进度日志

### 2026-05-07 — Phase 1 完成 ✓

**实现**：
- ✓ 项目脚手架（pyproject.toml / config.example.yaml / prompts.yaml / DEVELOPMENT.md / README.md）
- ✓ `publisher_hub/__init__.py` `config.py` `db.py` `rewrite.py`
- ✓ `hub_drafts` 表自动建表（首次连接时 CREATE TABLE IF NOT EXISTS）
- ✓ CLI `python -m publisher_hub.rewrite <user_id> [<platform>]`

**验证（alice）**：

| 平台 | 模式 | 取数 | 生成 | 标题示例 | 正文长度 |
|------|------|------|------|---------|---------|
| wechat | batch | 10 → 2 篇 | 2 ✓ | "很多人以为出国读书很贵，但他们没算过这笔账" | 5400/5100 字符 |
| xhs | per_post | 5 → 5 篇 | 5 ✓ | "朱拉留学到底香不香？学姐用3年经验说点大" | 1100-1800 字符 |

每条草稿都关联了 `image_urls`（来自原帖 cover_url），`status='ready'`。

**踩过的坑（已写进 §8）**：
1. `deepseek-reasoner` 模型在 valueclue 代理已停用 → 切 `deepseek-v4-pro`
2. valueclue 代理 16.8s 硬超时 → 用 `stream=True` 绕过
3. v4-pro 也是 reasoning 模型，响应含 `reasoning_content` → 只拼接 `delta.content`
4. `max_tokens` 太小（3k/8k）reasoning 没跑完 → 给到 50000
5. character class `[::]` 实际是两个半角冒号（U+003A），匹配不到全角 `：` → 改 `[:：]`

**配置最终值**：
```yaml
llm:
  model: deepseek-v4-pro
  max_tokens: 50000
  timeout_seconds: 300
```
代码里 `_call_llm` 已切到 stream 模式（rewrite.py:206-261）。

**下一步**：Phase 2 前端展示（FastAPI + Jinja2 + HTMX）。

### 2026-05-07 — Phase 2 完成 ✓

**实现**：
- ✓ `app.py` —— FastAPI 入口，lifespan 注入 config/prompts/db 到 `app.state`
- ✓ `publisher_hub/routes/{home,wechat,xhs}.py` —— 所有 GET / POST 路由
- ✓ `templates/{base,home,list,_list_partial,detail}.html` —— Jinja2 + HTMX
- ✓ 端口 :8900；首页 `/` 用户卡片；`/{user_id}/wechat`、`/{user_id}/xhs` 列表/详情；HTMX 局部刷新

**视觉**：teal-700 主色 + amber-600 点缀 + slate 中性灰；卡片网格 + 图片画廊；不依赖任何 CSS framework，纯 base.html 内联样式。

**验证（curl 全路由）**：

| URL | 返回 | 检查项 |
|-----|------|-------|
| `GET /` | 200 | Alice/Bob 卡片 + 草稿数 (2/5/0/0) |
| `GET /alice` | 302 → /alice/wechat | 重定向 |
| `GET /alice/wechat` | 200 | 列出 2 条 wechat 草稿 |
| `GET /alice/xhs` | 200 | 列出 5 条 xhs 草稿（含 emoji 🇹🇭🙋‍♀️） |
| `GET /alice/wechat/14` | 200 | markdown 渲染 18 个 `<p>` + 图片画廊 |
| `GET /alice/xhs/5` | 200 | 纯文本展示 + emoji 完整 |
| `GET /alice/wechat/9999` | 404 | 草稿不存在 |
| `GET /unknown` | 302 → / | 用户不存在 |
| `GET /bob/wechat` | 200 | 空状态卡片 ("暂无公众号草稿") |

**踩过的坑（追加到 §8）**：
- starlette 1.0+ 改了 `TemplateResponse` 签名为 `(request, name, context)`；
  旧式 `TemplateResponse(name, context_with_request)` 会报 `TypeError: unhashable type: 'dict'`。
- MySQL 连接偶尔被 server 端关闭（`Bad file descriptor` / `MySQL server has gone away`），
  pymysql.ping(reconnect=True) 自动恢复，请求层透明重试。

**HTMX 接入点**：
- 列表页"立即仿写一批"按钮 → `POST /{user}/{platform}/refresh` → 替换 `#list-area` 内容
- 推送按钮 → `POST /{user}/{platform}/{id}/push` → 当前返回 "Phase 3 待开发" 占位

**启动**：
```bash
cd /Users/fengxiaoji/code/publisher-hub
uv run uvicorn app:app --host 0.0.0.0 --port 8900
# 浏览器打开 http://localhost:8900/
```

**下一步**：Phase 3 推送（微信草稿箱 / myaibot 二维码 / 飞书通知）。

### 2026-05-07 — 图片来源修复

**问题**：
之前 `_collect_images` 直接用 `posts.cover_url`（CDN 链接），存在三个问题：
- CDN URL 会过期（XHS / FB / IG 的图片有签名时效）
- 防盗链 referer 检查会让浏览器加载失败
- newmedia 已经把图片下载到服务器并 HTTP 挂载，根本不需要再走 CDN

**修复**：
- `config.yaml.mysql` 段加 `image_server_url` / `cover_dir` / `attachment_dir`
- `RewriteEngine._collect_images` 改用本地路径转 HTTP：
  优先 `attachment_local_path` → `cover_local_path` → cover_url CDN（兜底）
- `_local_to_http(path)`：`/data/newmedia/covers/...` → `{server}/img/cover/...`；
  `/data/newmedia/attachments/...` → `{server}/img/attach/...`
- 一次性脚本回填了已有 7 条草稿的 `image_urls`

**验证**：抽 3 个修复后的图片 URL，2 个 200，1 个测试时手动截断 URL 导致 404；
详情页 `<img src>` 全部 `http://47.236.168.208:8899/img/cover/...`，CDN 链接残留 0。

**注意**：
- newmedia 入库 posts 后会异步 SSH 下载封面（几秒延迟），刚抓的新帖可能 `cover_local_path` 为空，
  此时 `_collect_images` 会 fallback 到 `cover_url` CDN —— 后台任务跑过一段时间后再仿写就有了。

### 2026-05-07 — Phase 3 完成 ✓

**实现**：
- ✓ `publisher_hub/wechat.py` —— 独立微信草稿推送（access_token 缓存 + SOCKS5 代理 + 图片压缩 +
  md→内联 HTML + 草稿创建）；不依赖 newmedia.publisher.wechat，无 RSU 品牌耦合
- ✓ `publisher_hub/xhs.py` —— myaibot.vip 调用，从响应里提取 qrcode_url
- ✓ `publisher_hub/feishu.py` —— webhook 卡片（推送成功 / 失败）；webhook 含 'xxxxxxxx' 占位则跳过不报错
- ✓ `routes/wechat.py` 和 `routes/xhs.py` 的 push 端点接通真实 API
- ✓ `templates/_qr_modal.html` —— 二维码 modal（HTMX 替换按钮）
- ✓ `config.yaml` 中 alice 改成 newmedia 已绑定的真实公众号配置（wx803d7483f868c2f0）

**用户配置**：
- **alice** = `RSU 中文服务` 真实公众号 + 共享 myaibot token
- **bob** 仍占位（app_id=wxYYY...），用于 UI 测试不会触发实际推送

**手动测试方式**（用户操作）：
1. 浏览器打开 `http://localhost:8900/alice/wechat`
2. 任一草稿点 `📤 推送到草稿箱` → 弹 confirm → 大约 30-60s 后按钮变绿色 badge
3. 进 `mp.weixin.qq.com` 草稿箱看新草稿（可删除）
4. 小红书：`/alice/xhs` 点 `📱 生成二维码` → modal 弹出二维码 → 用绑定的小红书 App 扫码
5. 飞书 webhook 没配的情况下不发卡片但不报错；要启用就改 `config.yaml.feishu.webhook`

**关键设计**：
- `_TOKEN_CACHE` 模块级 dict，按 `app_id` 索引，多用户互不干扰
- 内嵌图最多取 4 张（避免请求过多），每 2 个 ## 章节插一张
- 所有图片 URL 已是 `/img/cover/` 服务器本地，下载快
- xhs `pushed_result` 存 JSON（含 qr_url + raw 响应），方便排查
- push 失败会 `mark_failed` + 飞书发红色卡片

**Phase 3 完成后整体可用流程**：
```
仿写（CLI 或 UI 按钮）→ 草稿入 hub_drafts → 前端列表展示
                                              ↓
                                          点推送按钮
                                              ↓
                              wechat: 草稿箱      xhs: 二维码 modal
                                              ↓
                                      飞书卡片通知
```

**下一步**：Phase 4 调度（APScheduler 自动跑 rewrite_cron；push_cron 留接口暂不启用）。

### 2026-05-07 — 用户反馈修复 3 件

#### 1. myaibot API 认证方式变了
之前用 `Authorization: Bearer <token>` 报 `HTTP 500: 无法解析请求体或缺少 api_key 参数`。
curl 抓包确认现在 myaibot 要求：
- `api_key` 在 **body**（不是 header）
- 必须带 `type` 字段（`normal` 图文 / `video` 视频）
- 图文笔记 `images` 数组**必须非空**

**修复**：`publisher_hub/xhs.py` 改用 body 认证：
```python
payload = {
    'api_key': self.token,
    'type':    'normal',
    'title':   title,
    'content': content,
    'images':  images,        # 非空检查
}
headers = {'Content-Type': 'application/json'}
```

#### 2. 推送按钮加转圈进度
HTMX 的 `.htmx-request` class 会在请求中自动加到按钮上。利用这点切换 `.btn-text` / `.btn-loading`：

```html
<button class="btn btn-primary" hx-post="..." hx-swap="outerHTML">
  <span class="btn-text">📤 推送到草稿箱</span>
  <span class="btn-loading"><span class="spinner"></span> 推送中…</span>
</button>
```

base.html 加 `@keyframes pub-spin` + `.spinner` 圆环旋转，请求中按钮 `cursor:wait` + 半透明禁用。

#### 3. RSU 素材库 + 智能选图
- 复制 `newmedia/assets/` → `publisher-hub/assets/`（42 张 RSU 校园照片，本地物理备份）
- 抽 `publisher_hub/rsu.py`：清单 + tags + `pick_random(n, prefer_tags, exclude)`
- `RewriteEngine.collect_images_for_draft(post_imgs, category)`：原帖图不足 `min_images` 张时，
  按 category 文本匹配 RSU tag（`'泰国' / '泰式' / '文化' → culture`、`'学校' / '官方' / '校园' → campus` 等）
  从 RSU 池随机抽不重复的图片补到 4 张
- config 加 `assets.rsu_base_url` / `assets.min_images`
- 一次性回填 7 条历史草稿：xhs (cat=泰国留学) 各补 3 张 culture 主题图；wechat 已经够无需补

**RSU 图片 URL 用 newmedia 服务器（公网可达）**，不用 localhost —— 微信 / 小红书都需要外网下载。
本地 `assets/` 目录纯备份，代码不引用其本地路径。

**服务器侧改动**（不在本仓库代码内，但 publisher-hub 依赖它）：
- 服务器 `/data/newmedia/web.py` 原本只挂载了 `/img/attach`、`/img/cover`，**没挂 `/img/rsu`**
  （NEWMEDIA_PROJECT_NOTES.md 文档说挂了但实际没挂，文档过时）
- 通过 SSH 在 `/img/cover` mount 之后加了：
  ```python
  _rsu_dir = Path("/data/assets/rsu-internal")
  if _rsu_dir.exists():
      app.mount("/img/rsu", StaticFiles(directory=str(_rsu_dir)), name="rsu")
  ```
- `systemctl restart newmedia-web` 后 8899 端口被旧手动启动的 uvicorn 占用（不归 systemd 管），
  必须先 `lsof -ti:8899 | xargs kill -9` 再 systemctl start
- 备份了原 web.py 到 `/data/newmedia/web.py.bak.<ts>`

**RSU 图片体积问题**：服务器上原图很大（4-12MB），小红书 myaibot 侧下载可能慢；
微信侧 publisher-hub 自己压缩到 1080px/900KB 不受影响。

### 2026-05-07 — XHS title 长度精确截断

**问题**：myaibot 报 `title 最多20个字符（中文占1，英文/数字占0.5），当前：22`。
原标题 `专科直升硕士，泰国这条路到底有多香？🇹🇭` —— 末尾国旗 emoji 🇹🇭 是 2 个 codepoint，
myaibot 算法把每个 emoji codepoint 算 2 字，整体 18+4=22 超限。

**修复**：`xhs.py` 加 `_truncate_xhs_title(s, limit=20)`，按 codepoint 类别精确算 budget：
- ASCII（数字/英文/标点）→ 0.5 字
- 一般 CJK / 中文标点 → 1 字
- emoji 段（U+1F000–U+1FFFF, U+2600–U+27BF）→ 2 字（保守）
- 累加超限即停止；保留 0.5 字余量

实测 5 个标题，含国旗 emoji 的从 22 字截到 18 字（剥掉 🇹🇭），其它不含 emoji 的不变。

### 待解决：XHS 图片"内容相关"

用户反馈：当前 RSU 池随机/tag 匹配选图不够精准，希望"图片与文字内容相关"。

**实测结论**：
- `qwen3.6-plus` 不是图像生成模型（实测它自己说 "I cannot directly generate images"），
  它只能做文本生成 + 视觉理解
- `gemini-2.5-flash-image` 当前 valueclue token 没权限（401 Invalid token）

**3 个备选方案**（待用户决定）：

1. **LLM 智能选 RSU 图（推荐）**：
   把仿写好的标题+正文摘要 + RSU 池全部 desc 列表丢给 deepseek，让它选 N 张最相关的 file 名。
   优点：成本低（几分钱 / 篇）、快、图片真实存在；
   缺点：仍局限于现有 RSU 40 张，主题覆盖有限。

2. **真图像生成**：
   接 DALL-E / SiliconFlow Stable Diffusion / 阿里云通义万相等。
   优点：完全匹配内容；缺点：每条几毛钱、生成慢（10-30s）、需要新 API key。

3. **从 newmedia.posts 跨主题选图**：
   按文章主题在 posts 表（不限当前用户的 sources）里找最相关帖子的图。
   优点：素材丰富；缺点：可能误用别人帖子的图（版权/相关性）。

### 2026-05-08 — XHS 接入 wan2.7-image-pro 生图

**实现**：
- 新建 `publisher_hub/image_gen.py` —— `ImageGenerator.generate(prompt, n)` 调
  valueclue `/v1/images/generations`，返回阿里 OSS 临时 URL（24h 有效）
- `RewriteEngine._collect_xhs_images(post_imgs, title, content)`：
  小红书每条草稿固定 **1 张原帖封面 + 1 张 wan 生成图**（共 2 张）
- wechat 模式不变（原帖图 + RSU 兜底，目标 4 张）

**valueclue 16.8s 限制坑（再次踩到）**：
- `/v1/images/generations` 不支持 stream，本地直连必 16.8s 超时返空
- 解决：`config.image_gen.proxy` 默认填 SOCKS5（`socks5h://...@47.236.168.208:20356`）
  让本地能跑通；**部署到 47.236.168.208 服务器后改为 ""** 即可（服务器路径无超时，
  实测 27-30s 出图）

**性能**：
- 单条 xhs 仿写 ≈ 60s（LLM 31s + wan 26s）
- 每用户每次 batch 2 条 ≈ 2 分钟

**配置**：
```yaml
image_gen:
  enabled: true
  model:    wan2.7-image-pro
  size:     1024*1024
  proxy:    "socks5h://...@47.236.168.208:20356"   # 服务器部署改 ""
  timeout_seconds: 180
```

**仿写量调整**：每用户每次仿写 2 条
- alice/bob.xhs.rewrite_batch: 5/3 → **2**
- bob.wechat.articles_per_batch: 1 → **2**（alice 已是 2）

### 2026-05-09 — 部署到 newmedia 服务器

**位置**：`/data/publisher-hub/`（与 newmedia 共享同一台 47.236.168.208）

**步骤**：
1. `curl -LsSf https://astral.sh/uv/install.sh | sh` 装 uv（自动管理 Python 3.11）
2. `git clone https://github.com/someonelikeyoujiyue/publish.git /data/publisher-hub`
3. `cd /data/publisher-hub && uv venv && uv pip install -e .`
4. SCP 本地 `config.yaml` 到服务器，sed 改两个值：
   - `mysql.host`: `47.236.168.208` → `127.0.0.1`（服务器本地连库快）
   - `image_gen.proxy`: `socks5h://...` → `""`（服务器路径无 valueclue 16.8s 限制）
   - 其他不动（wechat.proxy 仍要 SOCKS5 走 IP 白名单；image_server_url 保持公网）
5. systemd 服务 `/etc/systemd/system/publisher-hub.service`：
```ini
[Service]
WorkingDirectory=/data/publisher-hub
ExecStart=/data/publisher-hub/.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8900
Restart=always
After=network.target newmedia-web.service
```
6. `systemctl daemon-reload && enable && start`

**自动判别本地写图**：
`image_gen.py._is_running_on_server()` 用 socket 解析 `mysql.host`（即 server_host），
匹配本机 IP 时跳过 scp 直接 `shutil.copy()` → 写 `/data/assets/hub-generated/<draft_id>.jpg`。
本地开发仍走 scp。

**端口分配**（最终）：
- `:8899` → newmedia-web（统计 + /img/cover/attach/rsu/hub-gen 静态托管）
- `:8900` → publisher-hub（多用户工作台）

**访问**：http://47.236.168.208:8900/
**日志**：`journalctl -u publisher-hub -f`
**重启**：`systemctl restart publisher-hub`

### 2026-05-09 — 用户增删改 + 默认值机制 + 热加载

**目标**：首页"+ 新增用户"按钮 → modal 表单 → 写 yaml → 自动热加载，无需 systemctl restart。

**配置结构**：
```yaml
_default_user:                    # 全用户共享默认值
  wechat:
    proxy:      "..."             # 微信 IP 白名单代理
    sources:    {...}
    prompt:     "wechat_article"
    rewrite_cron: "0 8 * * *"     # 每天 8:00 全用户仿写
    push_cron:  ""
  xhs:
    sources:    {...}
    rewrite_cron: "0 13 * * *"    # 每天 13:00 全用户仿写
    ...

users:                            # 用户只填差异字段
  - id: alice
    name: "RSU 中文服务"
    wechat:
      app_id:     "wx..."
      app_secret: "..."
```

`get_user(config, user_id)` 自动深度合并 `_default_user` + 用户记录；`xhs.display_name` 派生自 `name`。

**写文件保留注释**：用 `ruamel.yaml`（不是 `yaml.safe_dump`）。
关键设置：`_ruamel.allow_unicode = True`，否则中文被转义成 `\xXX` 看着像乱码。

**端点**（`publisher_hub/routes/users.py`）：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET  | `/users/new` | 渲染新增表单 modal |
| POST | `/users` | 创建用户 → 写 yaml → reload → `HX-Redirect: /` |
| GET  | `/users/{id}/edit` | 编辑表单（user_id readonly，app_secret 留空保持不变） |
| POST | `/users/{id}` | 更新（部分字段可空） |
| POST | `/users/{id}/delete` | 删除条目（不动 hub_drafts；同 ID 重建可恢复草稿） |

**注册顺序**：`users.router` 必须**先于** `home.router`，否则 `/{user_id}` 通配会拦截 `/users/...`。

**user_id 校验**：`^[a-z0-9_-]{1,32}$`，且不能 `_` 开头（保留给系统配置）。

**热加载**：`reload_app_state(app)` 写完 yaml 后调用，仅替换 `app.state.config / prompts`，
单 worker uvicorn 进程内即时生效；正在执行的请求不受影响。

**前端**：
- 首页右上角 `+ 新增用户` 按钮（HTMX `hx-get="/users/new"` `hx-target="#modal-area"`）
- 每张用户卡片右上角 ✏️/🗑 按钮
- modal 表单顶部红色 IP 白名单提示：`47.236.168.208`
- 表单含 `hx-disabled-elt="this"` 防双击 + spinner

**坑（已记）**：
- curl `-d` 不 urlencode 中文导致 latin-1 解码乱码 → 用 `--data-urlencode` 或浏览器 form 提交（标准 UTF-8 percent-encode）。
- ruamel.yaml 默认 `allow_unicode=False` 把非 ASCII 转成 `\xXX` 看像 mojibake。

### 2026-05-09 — Phase 4 完成 ✓ 定时调度

**触发**：每天 **08:00 Asia/Shanghai**（一个 cron 跑全部）

**daily_run 流程**（`publisher_hub/scheduler.py`）：
```
for user in list_users(config):
  ┌─ wechat 阶段 ──────────────────────────────────
  │ 1. RewriteEngine.run_user(uid, 'wechat')       → 仿写 N 篇（默认 batch=10→2 篇）
  │ 2. 取所有 status='ready' 的 wechat 草稿
  │ 3. WeChatPublisher.push 推到草稿箱（永久素材封面+内嵌图+md→html+digest）
  │ 4. mark_pushed + FeishuBot.push_success
  │   → 飞书卡片 "alice 公众号草稿: media_id xxx，进 mp.weixin.qq.com 手动群发"
  └─ xhs 阶段 ─────────────────────────────────────
    1. RewriteEngine.run_user(uid, 'xhs')           → 仿写 N 条（默认 2 条）
    2. 取所有 status='ready' 的 xhs 草稿
    3. XhsPublisher.push（myaibot /publish 1 次扣费）
    4. 二维码 base64 写入 hub_drafts.pushed_result
    5. FeishuBot.push_success
      → 飞书卡片 "alice 小红书二维码已生成: https://...，及时扫码"
```

**实现细节**：
- 用 `BackgroundScheduler`（独立线程跑）而不是 `AsyncIOScheduler`，避免阻塞 FastAPI event loop
- `_run_lock = threading.Lock()` 防重入：上一轮还在跑下一轮就 skip
- `misfire_grace_time=3600`：服务重启时若错过几小时内的触发会补跑一次
- 单用户单平台失败不影响其它（每段 try/except）
- wechat 推送后 db.mark_pushed(media_id)；xhs 写 `{"qr_url": "data:image/png;base64,..."}`

**Admin 端点**（`publisher_hub/routes/admin.py`）：
| 路径 | 用途 |
|---|---|
| `POST /admin/run-now` body `user_id=xxx` | 立即触发 daily_run（后台线程，可指定单用户）|
| `GET /admin/scheduler-status` | 看 cron + next_run_time |

**默认值改动**（`_HARDCODED_DEFAULTS` + yaml `_default_user`）：
- `xhs.sources.categories: []` —— 空=不限制 category，所有 xhs/douyin posts 都可仿（fxj 实测可仿写 999+）
- `xhs.rewrite_cron: '0 8 * * *'` —— 与 wechat 同 cron 一起跑
- 删除 `push_cron` 字段（推送随仿写一起跑，不分两个 cron）

**测试方式**：
- 等明早 8:00 自动触发
- 或 `curl -X POST http://47.236.168.208:8900/admin/run-now -d "user_id=fxj"`
- 进度看 `journalctl -u publisher-hub -f`

### 2026-05-07 — 失败状态可重试

**问题**：推送失败 `mark_failed` 后，模板里 `{% if d.status == 'ready' %}` 不再显示按钮，
用户被卡住看不到重试入口；HTMX 响应也只是死 badge，没法再点。

**修复**：
- `_list_partial.html` / `detail.html`：状态判断改成 `if status in ('ready', 'failed')`，
  failed 显示 `🔄 重试推送` / `🔄 重试生成` 文案 + inline 错误信息
- `_push_failed.html` 新模板：失败响应 = 错误展示 + 重试按钮（HTMX outerHTML 替换原 button 为这个 div）
- `routes/wechat.py` / `routes/xhs.py` 失败时返回这个 partial 而不是死 badge

**效果**：
- 失败卡片有红色横条显示错误（限 140 字）
- 重试按钮带 spinner，可立刻再点
- 重试再失败 → 同样换一组重试按钮 + 新错误
- 重试成功 → 正常变 pushed badge / 二维码 modal

**注意 cron 仿写时的 RSU 兜底机制**：
- newmedia 抓帖后异步下载封面（几秒延迟），刚抓的新帖 `cover_local_path` 可能空
- 此时 `_collect_images` 走 cover_url CDN（兜底），但更多情况下原帖图就是少
- `collect_images_for_draft` 会用 RSU 补到 `min_images=4` 张，保证微信草稿和小红书都有图发

---

## 12. 后续可选扩展（不在本次范围）

- 用户级飞书 webhook 覆盖
- 草稿编辑（前端改标题/正文后再推）
- 推送历史页（status='pushed' 的列表）
- 多 myaibot token 池（不同用户用不同 token）
- 仿写质量反馈（用户标记好/差，反哺 prompt 调优）
- 真正启用 push_cron 自动推
