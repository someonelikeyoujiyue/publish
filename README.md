# Publisher Hub

多用户内容推送系统：消费 newmedia 抓取的原帖，AI 仿写后按用户推送到各自的微信公众号草稿箱 / 小红书（扫码确认）。

- 无登录，URL 带 `user_id` 即个人页（如 `/alice/wechat`）
- 全部用户共享视野，按用户配置抓取条件独立仿写
- 与 newmedia **代码独立**，仅共用 MySQL

详细架构、决策记录、注意事项见 [DEVELOPMENT.md](./DEVELOPMENT.md)。

---

## 快速开始（Phase 完成后）

```bash
# 1. 安装依赖
uv venv && uv pip install -e .

# 2. 配置
cp config.example.yaml config.yaml
# 编辑 config.yaml 填入 mysql / llm / myaibot / 用户列表

# 3. 单用户仿写测试（Phase 1）
uv run python -m publisher_hub.rewrite alice wechat

# 4. 启动 Web 服务（Phase 2 之后）
uv run uvicorn app:app --host 0.0.0.0 --port 8900 --reload
```

访问：
- `http://localhost:8900/` — 用户首页
- `http://localhost:8900/alice/wechat` — Alice 的公众号草稿
- `http://localhost:8900/alice/xhs` — Alice 的小红书待发

---

## 目录结构

```
publisher-hub/
├── DEVELOPMENT.md          # 开发计划 & 注意事项（必读）
├── pyproject.toml
├── config.example.yaml
├── prompts.yaml
├── app.py                  # FastAPI 入口
├── publisher_hub/
│   ├── config.py           # yaml 加载
│   ├── db.py               # MySQL 封装（hub_drafts 表）
│   ├── rewrite.py          # 仿写引擎（per_post / batch）
│   ├── wechat.py           # 微信草稿推送
│   ├── xhs.py              # myaibot 调用
│   ├── feishu.py           # webhook 通知
│   ├── scheduler.py        # APScheduler
│   └── routes/             # FastAPI 路由
└── templates/              # Jinja2 模板（HTMX 交互）
```
