"""MySQL 封装：复用 newmedia 库，新建 hub_drafts 表。

只读 newmedia.posts；hub_drafts 是 publisher-hub 自己的草稿池。
"""
from __future__ import annotations

import logging

import pymysql

log = logging.getLogger('publisher_hub.db')


_HUB_DRAFTS_DDL = """
CREATE TABLE IF NOT EXISTS hub_drafts (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    user_id         VARCHAR(50)  NOT NULL,
    platform        ENUM('wechat','xhs','toutiao') NOT NULL,
    source_post_id  INT          NOT NULL,
    source_post_ids TEXT,
    title           TEXT,
    content         LONGTEXT,
    image_urls      TEXT,
    status          ENUM('ready','pushed','failed') DEFAULT 'ready',
    pushed_at       DATETIME,
    pushed_result   TEXT,
    error_msg       TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_platform_post (user_id, platform, source_post_id),
    INDEX idx_user_platform_status (user_id, platform, status),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


class Database:
    """同步 pymysql 封装；进程内单实例。"""

    def __init__(self, config: dict):
        cfg = config['mysql']
        self.host     = cfg['host']
        self.port     = int(cfg.get('port', 3306))
        self.user     = cfg['user']
        self.password = cfg['password']
        self.database = cfg['database']
        self._conn    = None

    # ── 连接管理 ──────────────────────────────────────────────────────

    def connect(self):
        self._conn = pymysql.connect(
            host=self.host, port=self.port,
            user=self.user, password=self.password,
            database=self.database, charset='utf8mb4',
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
        )
        self._ensure_hub_drafts_table()
        log.info('[db] 已连接 %s:%d/%s', self.host, self.port, self.database)

    def close(self):
        if self._conn and self._conn.open:
            self._conn.close()
        self._conn = None

    def _cur(self):
        try:
            self._conn.ping(reconnect=True)
        except Exception:
            self.connect()
        return self._conn.cursor()

    def _ensure_hub_drafts_table(self):
        with self._cur() as cur:
            cur.execute(_HUB_DRAFTS_DDL)

    # ── 读 newmedia.posts（按用户配置过滤）────────────────────────────

    def get_posts_for_user(
        self,
        user_id: str,
        platform: str,
        sources: dict,
        limit: int = 10,
        pick_strategy: str = 'latest',
        recent_pool: int = 50,
    ) -> list[dict]:
        """从 newmedia.posts 取该用户该平台未仿写过的原帖。

        通过 LEFT JOIN hub_drafts 排除已仿写过的（按 user_id+platform 维度）。

        Args:
            sources: {platforms, categories}
            pick_strategy:
                'latest'        — discovered_at DESC LIMIT N（最新）
                'random'        — ORDER BY RAND() LIMIT N（全池随机；xhs 用）
                'recent_random' — 取最近 recent_pool 条 → Python random.sample N（wechat 用）
        """
        import random
        platforms  = sources.get('platforms') or []
        categories = sources.get('categories') or []
        if not platforms:
            log.warning('[db] %s/%s sources.platforms 为空，跳过', user_id, platform)
            return []

        ph_p = ','.join(['%s'] * len(platforms))
        cat_clause = ''
        if categories:
            ph_c = ','.join(['%s'] * len(categories))
            cat_clause = f'AND p.category IN ({ph_c})'

        # 排序 + LIMIT 由策略决定
        if pick_strategy == 'random':
            order_clause = 'ORDER BY RAND()'
            sql_limit    = limit
        elif pick_strategy == 'recent_random':
            order_clause = 'ORDER BY p.discovered_at DESC'
            sql_limit    = max(recent_pool, limit)        # 先拿大池
        else:
            order_clause = 'ORDER BY p.discovered_at DESC'
            sql_limit    = limit

        sql = f"""
            SELECT p.id, p.platform, p.post_id, p.nickname, p.category,
                   p.title, p.content, p.translated_title, p.translated_content,
                   p.cover_url, p.cover_local_path, p.attachment_local_path,
                   p.cover_image_desc,
                   p.discovered_at, p.published_at
            FROM posts p
            LEFT JOIN hub_drafts d
              ON d.source_post_id = p.id
             AND d.user_id  = %s
             AND d.platform = %s
            WHERE p.platform IN ({ph_p})
              {cat_clause}
              AND d.id IS NULL
              AND ((p.content            IS NOT NULL AND p.content            != '')
                OR (p.translated_content IS NOT NULL AND p.translated_content != ''))
            {order_clause}
            LIMIT %s
        """
        params: list = [user_id, platform]
        params.extend(platforms)
        params.extend(categories)
        params.append(sql_limit)

        try:
            with self._cur() as cur:
                cur.execute(sql, params)
                rows = list(cur.fetchall())
        except Exception as e:
            log.warning('[db] get_posts_for_user 失败: %s', e)
            return []

        if pick_strategy == 'recent_random' and len(rows) > limit:
            rows = random.sample(rows, limit)

        log.info('[db] %s/%s pick=%s pool=%d -> %d 条',
                 user_id, platform, pick_strategy, len(rows), min(limit, len(rows)))
        return rows[:limit]

    def update_post_image_desc(self, post_id: int, desc: str) -> None:
        """写入 newmedia.posts.cover_image_desc（识图缓存）。"""
        try:
            with self._cur() as cur:
                cur.execute(
                    'UPDATE posts SET cover_image_desc=%s WHERE id=%s',
                    (desc, post_id),
                )
        except Exception as e:
            log.warning('[db] update_post_image_desc 失败: %s', e)


    # ── hub_drafts 操作 ──────────────────────────────────────────────

    def save_draft(
        self,
        user_id: str,
        platform: str,
        source_post_id: int,
        title: str,
        content: str,
        image_urls: str = '',
        source_post_ids: str = '',
        status: str = 'ready',
    ) -> int:
        """插入草稿（同 user+platform+source_post_id 已存在则更新）。返回 id。"""
        try:
            with self._cur() as cur:
                cur.execute(
                    """
                    INSERT INTO hub_drafts
                        (user_id, platform, source_post_id, source_post_ids,
                         title, content, image_urls, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        title           = VALUES(title),
                        content         = VALUES(content),
                        image_urls      = VALUES(image_urls),
                        source_post_ids = VALUES(source_post_ids),
                        status          = VALUES(status),
                        updated_at      = NOW()
                    """,
                    (user_id, platform, source_post_id, source_post_ids,
                     title, content, image_urls, status),
                )
                if cur.lastrowid:
                    return cur.lastrowid
                cur.execute(
                    "SELECT id FROM hub_drafts "
                    "WHERE user_id=%s AND platform=%s AND source_post_id=%s",
                    (user_id, platform, source_post_id),
                )
                row = cur.fetchone()
                return row['id'] if row else 0
        except Exception as e:
            log.warning('[db] save_draft 失败: %s', e)
            return 0

    def list_drafts(
        self,
        user_id: str,
        platform: str | None = None,
        status: str | None = 'ready',
        limit: int = 50,
    ) -> list[dict]:
        sql = "SELECT * FROM hub_drafts WHERE user_id=%s"
        params: list = [user_id]
        if platform:
            sql += " AND platform=%s"
            params.append(platform)
        if status:
            sql += " AND status=%s"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)
        try:
            with self._cur() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())
        except Exception as e:
            log.warning('[db] list_drafts 失败: %s', e)
            return []

    def get_draft(self, draft_id: int) -> dict | None:
        try:
            with self._cur() as cur:
                cur.execute("SELECT * FROM hub_drafts WHERE id=%s", (draft_id,))
                return cur.fetchone()
        except Exception as e:
            log.warning('[db] get_draft 失败: %s', e)
            return None

    def mark_pushed(self, draft_id: int, pushed_result: str = ''):
        try:
            with self._cur() as cur:
                cur.execute(
                    """UPDATE hub_drafts
                       SET status='pushed', pushed_at=NOW(), pushed_result=%s
                       WHERE id=%s""",
                    (pushed_result, draft_id),
                )
        except Exception as e:
            log.warning('[db] mark_pushed 失败: %s', e)

    def mark_failed(self, draft_id: int, error_msg: str = ''):
        try:
            with self._cur() as cur:
                cur.execute(
                    """UPDATE hub_drafts
                       SET status='failed', error_msg=%s
                       WHERE id=%s""",
                    (error_msg, draft_id),
                )
        except Exception as e:
            log.warning('[db] mark_failed 失败: %s', e)
