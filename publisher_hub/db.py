"""MySQL 封装：复用 newmedia 库，新建 hub_drafts 表。

只读 newmedia.posts；hub_drafts 是 publisher-hub 自己的草稿池。
"""
from __future__ import annotations

import logging
import threading

import pymysql

log = logging.getLogger('publisher_hub.db')


_HUB_DRAFTS_DDL = """
CREATE TABLE IF NOT EXISTS hub_drafts (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    user_id         VARCHAR(50)  NOT NULL,
    platform        ENUM('wechat','xhs','toutiao','douyin','youtube') NOT NULL,
    source_post_id  INT          NOT NULL,
    source_post_ids TEXT,
    title           TEXT,
    content         LONGTEXT,
    image_urls      TEXT,
    source_url      TEXT,
    status          ENUM('ready','processing','pushed','failed') DEFAULT 'ready',
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

_HUB_VIDEO_JOBS_DDL = """
CREATE TABLE IF NOT EXISTS hub_video_jobs (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    user_id          VARCHAR(50) NOT NULL,
    topic            TEXT,                    -- 用户输入的话题（可空，如果提供了 narrations）
    title            TEXT,                    -- LLM 生的或用户给的视频标题
    narrations_json  LONGTEXT,                -- JSON array of narration strings
    image_paths_json LONGTEXT,                -- JSON array of 绝对路径（用户上传 + 默认补足后的最终列表）
    voice            VARCHAR(60),             -- edge-tts voice preset key
    rate             VARCHAR(10),             -- 语速 e.g. "+5%"
    bgm_path         TEXT,                    -- 用户指定 BGM 绝对路径（可空，空 = 默认）
    bgm_volume       FLOAT DEFAULT 0.2,
    n_scenes         INT,                     -- 期望段数（用户没传 narrations 时生效）
    status           ENUM('pending','processing','done','failed') DEFAULT 'pending',
    output_path      TEXT,                    -- 最终 mp4 绝对路径
    duration_sec     FLOAT,
    file_size        BIGINT,
    error_msg        TEXT,
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user_status (user_id, status),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


class Database:
    """同步 pymysql 封装。

    并发安全：FastAPI 把 sync def 路由跑在 thread pool（默认 40 个并发），
    多线程**共享一个 pymysql 连接会导致协议帧交叉**（'Packet sequence number
    wrong'）。这里给每个 thread 独立连接（threading.local），互不干扰。
    """

    def __init__(self, config: dict):
        cfg = config['mysql']
        self.host     = cfg['host']
        self.port     = int(cfg.get('port', 3306))
        self.user     = cfg['user']
        self.password = cfg['password']
        self.database = cfg['database']
        self._local   = threading.local()

    # ── 连接管理 ──────────────────────────────────────────────────────

    def _new_conn(self):
        return pymysql.connect(
            host=self.host, port=self.port,
            user=self.user, password=self.password,
            database=self.database, charset='utf8mb4',
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10,
            read_timeout=60,
            write_timeout=30,
        )

    @property
    def _conn(self):
        """每个线程独立连接（thread-local），断了自动重连。"""
        c = getattr(self._local, 'conn', None)
        if c is not None:
            try:
                c.ping(reconnect=True)
                return c
            except Exception:
                try: c.close()
                except Exception: pass
                self._local.conn = None
        c = self._new_conn()
        self._local.conn = c
        log.info('[db] thread %s 新连接 %s:%d/%s',
                 threading.current_thread().name, self.host, self.port, self.database)
        return c

    def connect(self):
        """初始化主线程连接 + 建表（lifespan 启动调用）。"""
        _ = self._conn
        self._ensure_hub_drafts_table()

    def close(self):
        c = getattr(self._local, 'conn', None)
        if c is not None:
            try: c.close()
            except Exception: pass
            self._local.conn = None

    def _cur(self):
        return self._conn.cursor()

    def _ensure_hub_drafts_table(self):
        with self._cur() as cur:
            cur.execute(_HUB_DRAFTS_DDL)
            cur.execute(_HUB_VIDEO_JOBS_DDL)

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
        source_url: str = '',
    ) -> int:
        """插入草稿（同 user+platform+source_post_id 已存在则更新）。返回 id。"""
        try:
            with self._cur() as cur:
                cur.execute(
                    """
                    INSERT INTO hub_drafts
                        (user_id, platform, source_post_id, source_post_ids,
                         title, content, image_urls, source_url, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        title           = VALUES(title),
                        content         = VALUES(content),
                        image_urls      = VALUES(image_urls),
                        source_post_ids = VALUES(source_post_ids),
                        source_url      = VALUES(source_url),
                        status          = VALUES(status),
                        updated_at      = NOW()
                    """,
                    (user_id, platform, source_post_id, source_post_ids,
                     title, content, image_urls, source_url, status),
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

    def set_draft_status(self, draft_id: int, status: str, error_msg: str = ''):
        """通用 status 更新（youtube processing → pushed/failed 用）。"""
        try:
            with self._cur() as cur:
                if error_msg:
                    cur.execute(
                        "UPDATE hub_drafts SET status=%s, error_msg=%s WHERE id=%s",
                        (status, error_msg, draft_id),
                    )
                else:
                    cur.execute(
                        "UPDATE hub_drafts SET status=%s WHERE id=%s",
                        (status, draft_id),
                    )
        except Exception as e:
            log.warning('[db] set_draft_status 失败: %s', e)

    # list_drafts 用于列表展示，不需要 content/pushed_result 等大字段。
    # 跨公网 100 行 LONGTEXT 可能 100KB+，跳过显著降低传输时间
    _LIST_COLUMNS = (
        'id', 'user_id', 'platform', 'source_post_id', 'title',
        'image_urls', 'source_url', 'status', 'pushed_at', 'pushed_result',
        'error_msg', 'created_at', 'updated_at',
    )

    def list_drafts(
        self,
        user_id: str,
        platform: str | None = None,
        status: str | None = 'ready',
        limit: int = 50,
    ) -> list[dict]:
        cols = ', '.join(self._LIST_COLUMNS)
        sql = f"SELECT {cols} FROM hub_drafts WHERE user_id=%s"
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

    def count_all_drafts(self) -> dict[tuple[str, str], int]:
        """一次 SQL 拿全部 (user_id, platform) → count 映射。

        替代过去 `len(list_drafts(limit=999))` 的反 pattern：
        2 用户 × 3 平台 = 6 次跨机 SELECT * 拉全部行 ~3 秒
        → 一次 GROUP BY COUNT(*) < 100ms。
        """
        try:
            with self._cur() as cur:
                cur.execute(
                    "SELECT user_id, platform, COUNT(*) AS n FROM hub_drafts "
                    "GROUP BY user_id, platform"
                )
                return {(r['user_id'], r['platform']): int(r['n']) for r in cur.fetchall()}
        except Exception as e:
            log.warning('[db] count_all_drafts 失败: %s', e)
            return {}

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

    def delete_draft(self, draft_id: int) -> int:
        try:
            with self._cur() as cur:
                cur.execute("DELETE FROM hub_drafts WHERE id=%s", (draft_id,))
                return cur.rowcount
        except Exception as e:
            log.warning('[db] delete_draft 失败: %s', e)
            return 0

    def update_draft_fields(self, draft_id: int, **fields) -> int:
        """部分更新 hub_drafts 行。允许字段白名单。返回受影响行数。"""
        ALLOWED = {'title', 'content', 'image_urls', 'status', 'error_msg', 'source_url'}
        sets, params = [], []
        for k, v in fields.items():
            if k in ALLOWED:
                sets.append(f'{k}=%s')
                params.append(v)
            else:
                log.warning('[db] update_draft_fields 忽略未知字段: %s', k)
        if not sets:
            return 0
        params.append(draft_id)
        sql = f'UPDATE hub_drafts SET {", ".join(sets)} WHERE id=%s'
        try:
            with self._cur() as cur:
                cur.execute(sql, params)
                return cur.rowcount
        except Exception as e:
            log.warning('[db] update_draft_fields 失败: %s', e)
            return 0

    def get_post(self, post_id: int) -> dict | None:
        """读 newmedia.posts 一条（重生 narration 需要原帖内容）。"""
        try:
            with self._cur() as cur:
                cur.execute(
                    """SELECT id, platform, post_id, nickname, category,
                              title, content, translated_title, translated_content,
                              cover_url, cover_local_path, attachment_local_path,
                              cover_image_desc, discovered_at, published_at
                       FROM posts WHERE id=%s""",
                    (post_id,),
                )
                return cur.fetchone()
        except Exception as e:
            log.warning('[db] get_post 失败: %s', e)
            return None

    # ── hub_video_jobs 操作（短视频生成）────────────────────────────────

    def create_video_job(
        self,
        user_id: str,
        topic: str = '',
        narrations: list[str] | None = None,
        image_paths: list[str] | None = None,
        title: str = '',
        voice: str | None = None,
        rate: str | None = None,
        bgm_path: str | None = None,
        bgm_volume: float | None = None,
        n_scenes: int | None = None,
    ) -> int:
        """插入一条 pending 状态的视频 job，返回 job_id。"""
        import json as _json
        with self._cur() as cur:
            cur.execute(
                """INSERT INTO hub_video_jobs
                   (user_id, topic, title, narrations_json, image_paths_json,
                    voice, rate, bgm_path, bgm_volume, n_scenes, status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')""",
                (
                    user_id, topic or '', title or '',
                    _json.dumps(narrations or [], ensure_ascii=False),
                    _json.dumps(image_paths or [], ensure_ascii=False),
                    voice, rate, bgm_path,
                    bgm_volume if bgm_volume is not None else 0.2,
                    n_scenes,
                ),
            )
            return cur.lastrowid

    def get_video_job(self, job_id: int) -> dict | None:
        """读一条 job；narrations/image_paths 字段自动 JSON 反序列化。"""
        import json as _json
        with self._cur() as cur:
            cur.execute("SELECT * FROM hub_video_jobs WHERE id=%s", (job_id,))
            r = cur.fetchone()
        if not r:
            return None
        try:
            r['narrations'] = _json.loads(r.pop('narrations_json') or '[]')
        except (ValueError, TypeError):
            r['narrations'] = []
        try:
            r['image_paths'] = _json.loads(r.pop('image_paths_json') or '[]')
        except (ValueError, TypeError):
            r['image_paths'] = []
        return r

    def list_video_jobs(self, user_id: str, limit: int = 50) -> list[dict]:
        """列出用户最近的视频 job（最新在前）。narrations/image_paths 反序列化。"""
        import json as _json
        with self._cur() as cur:
            cur.execute(
                """SELECT id, user_id, topic, title, narrations_json, image_paths_json,
                          status, output_path, duration_sec, file_size, error_msg,
                          created_at, updated_at
                   FROM hub_video_jobs
                   WHERE user_id=%s ORDER BY id DESC LIMIT %s""",
                (user_id, limit),
            )
            rows = cur.fetchall() or []
        for r in rows:
            try:
                r['narrations'] = _json.loads(r.pop('narrations_json') or '[]')
            except (ValueError, TypeError):
                r['narrations'] = []
            try:
                r['image_paths'] = _json.loads(r.pop('image_paths_json') or '[]')
            except (ValueError, TypeError):
                r['image_paths'] = []
        return rows

    def update_video_job(self, job_id: int, **fields) -> None:
        """部分更新一条 job。允许的字段在白名单内；narrations/image_paths 自动 JSON 序列化。"""
        import json as _json
        if not fields:
            return
        ALLOWED = {
            'status', 'title', 'output_path', 'duration_sec', 'file_size',
            'error_msg', 'voice', 'rate', 'bgm_path', 'bgm_volume', 'n_scenes',
            'topic',
        }
        sets, params = [], []
        for k, v in fields.items():
            if k in ALLOWED:
                sets.append(f'{k}=%s')
                params.append(v)
            elif k == 'narrations':
                sets.append('narrations_json=%s')
                params.append(_json.dumps(v or [], ensure_ascii=False))
            elif k == 'image_paths':
                sets.append('image_paths_json=%s')
                params.append(_json.dumps(v or [], ensure_ascii=False))
            else:
                log.warning('[db] update_video_job 忽略未知字段: %s', k)
        if not sets:
            return
        params.append(job_id)
        sql = f'UPDATE hub_video_jobs SET {", ".join(sets)} WHERE id=%s'
        with self._cur() as cur:
            cur.execute(sql, params)

    def delete_video_job(self, job_id: int) -> int:
        try:
            with self._cur() as cur:
                cur.execute("DELETE FROM hub_video_jobs WHERE id=%s", (job_id,))
                return cur.rowcount
        except Exception as e:
            log.warning('[db] delete_video_job 失败: %s', e)
            return 0
