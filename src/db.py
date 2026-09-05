"""MySQL 持久层(主库,2026-09-05 起取代 SQLite):账号、文章(双键去重)、
水位线、运行记录。

2026-09-05 用户指令「先不写也不读 SQLite」:存储层整体切到 MySQL——直接
读写 `test` 库的 wechat_crawler_accounts / _articles / _runs 三张表(前两张
沿用原镜像表结构与全部历史行,id 不变;连接参数在项目根 .env,gitignore)。
库/表自动创建;历史镜像表的 id 列无 AUTO_INCREMENT(镜像期显式带 id 插入),
初始化时查 information_schema 后补 ALTER(纯元数据变更,不动任何数据)。

articles 的去重设计(与原 SQLite 版逐语义一致):
  * dedup_key UNIQUE —— 有 canonical URL 时即 canonical URL,否则 fallback_key;
  * fallback_key —— 标题|日期 哈希,列表扫描阶段用于"已见过?"判断,
    避免对已入库文章重复打开文章页。

MySQL 方言要点:
  * 占位符 %s;服务器时间 NOW()(本机库,与原 SQLite localtime 同一台机器);
  * 行访问用 DictCursor(保持 row["col"] 语义);
  * lastrowid 必须取自执行 INSERT 的那个 cursor(新开 cursor 恒为 0);
  * 唯一键冲突 → pymysql.err.IntegrityError(值超长的 DataError 同按冲突处理);
  * dedup_key 取 VARCHAR(191):utf8mb4 唯一索引 191*4=764 字节,在 InnoDB
    3072 字节键长限内(canonical 实测最长 106 字符)。
"""
from __future__ import annotations

import pymysql
import pymysql.err

from .config import MysqlConfig

_CONFLICT_ERRORS = (pymysql.err.IntegrityError, pymysql.err.DataError)


def _connect(cfg: MysqlConfig):
    """连接并确保库/表存在(库不存在则建;表按需补 AUTO_INCREMENT)。"""
    conn = pymysql.connect(
        host=cfg.host, port=cfg.port, user=cfg.user, password=cfg.password,
        charset="utf8mb4", autocommit=False,
        cursorclass=pymysql.cursors.DictCursor, connect_timeout=5)
    with conn.cursor() as c:
        c.execute(f"CREATE DATABASE IF NOT EXISTS `{cfg.database}` "
                  "DEFAULT CHARSET utf8mb4")
        c.execute(f"USE `{cfg.database}`")
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS `{cfg.table_accounts}` (
                id INT PRIMARY KEY AUTO_INCREMENT,
                name VARCHAR(191) NOT NULL UNIQUE,
                last_crawled_at VARCHAR(32),
                max_publish_date VARCHAR(32)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS `{cfg.table_articles}` (
                id INT PRIMARY KEY AUTO_INCREMENT,
                account_id INT NOT NULL,
                dedup_key VARCHAR(191) NOT NULL UNIQUE,
                fallback_key VARCHAR(191) NOT NULL,
                url TEXT,
                title VARCHAR(512) NOT NULL,
                date_text VARCHAR(64),
                publish_date VARCHAR(32),
                url_status VARCHAR(16) NOT NULL DEFAULT 'pending',
                created_at VARCHAR(32),
                INDEX idx_articles_account (account_id),
                INDEX idx_articles_fallback (account_id, fallback_key)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS `{cfg.table_runs}` (
                id INT PRIMARY KEY AUTO_INCREMENT,
                started_at VARCHAR(32) NOT NULL,
                finished_at VARCHAR(32),
                ok_count INT NOT NULL DEFAULT 0,
                fail_count INT NOT NULL DEFAULT 0,
                new_count INT NOT NULL DEFAULT 0
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        for table in (cfg.table_accounts, cfg.table_articles, cfg.table_runs):
            c.execute(
                "SELECT EXTRA FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME='id'",
                (cfg.database, table))
            row = c.fetchone()
            if row and "auto_increment" not in (row["EXTRA"] or "").lower():
                # 镜像期遗留:id 显式赋值,无自增属性 → 补上(不动数据)
                c.execute(f"ALTER TABLE `{table}` "
                          "MODIFY COLUMN id INT NOT NULL AUTO_INCREMENT")
    conn.commit()
    return conn


class Store:
    def __init__(self, cfg: MysqlConfig):
        self.cfg = cfg
        self.conn = _connect(cfg)

    def close(self):
        self.conn.close()

    # ---- 账号 ----

    def get_or_create_account(self, name: str) -> int:
        with self.conn.cursor() as c:
            c.execute(f"SELECT id FROM `{self.cfg.table_accounts}` WHERE name=%s",
                      (name,))
            row = c.fetchone()
            if row:
                return row["id"]
            c.execute(f"INSERT INTO `{self.cfg.table_accounts}`(name) VALUES (%s)",
                      (name,))
            new_id = c.lastrowid
        self.conn.commit()
        return new_id

    def watermark(self, account_id: int) -> str | None:
        with self.conn.cursor() as c:
            c.execute("SELECT max_publish_date FROM "
                      f"`{self.cfg.table_accounts}` WHERE id=%s", (account_id,))
            row = c.fetchone()
        return row["max_publish_date"] if row else None

    def set_watermark(self, account_id: int, iso_date: str):
        with self.conn.cursor() as c:
            c.execute(f"UPDATE `{self.cfg.table_accounts}` "
                      "SET max_publish_date=GREATEST(COALESCE(max_publish_date,''), %s) "
                      "WHERE id=%s", (iso_date, account_id))
        self.conn.commit()

    def mark_crawled(self, account_id: int):
        with self.conn.cursor() as c:
            c.execute("UPDATE "
                      f"`{self.cfg.table_accounts}` "
                      "SET last_crawled_at=NOW() WHERE id=%s", (account_id,))
        self.conn.commit()

    # ---- 文章 ----

    def seen_fallbacks(self, account_id: int, status: str | None = None) -> set[str]:
        """该账号已入库的 fallback_key;status 过滤(如 'ok' → 仅成功提取过 URL 的)。"""
        sql = (f"SELECT fallback_key FROM `{self.cfg.table_articles}` "
               "WHERE account_id=%s")
        args: list = [account_id]
        if status:
            sql += " AND url_status=%s"
            args.append(status)
        with self.conn.cursor() as c:
            c.execute(sql, args)
            rows = c.fetchall()
        return {r["fallback_key"] for r in rows}

    def upsert_article(self, account_id: int, fallback_key: str, canonical_url: str | None,
                       title: str, date_text: str | None,
                       publish_date: str | None) -> str:
        """写入/升级一篇文章,返回 'new' | 'exists' | 'upgraded'。

        canonical 优先识别:先按 dedup_key=canonical 找已有行;fallback 文本
        漂移(如 标题|'' → 标题|日期)时 pending 行的 dedup_key 仍是旧
        fallback_key,再按「同账号同标题且 pending」识别为同一篇文章,
        升级而非重复插入。均未命中才走 fallback_key 查找。

        dedup_key 唯一约束冲突(critical:canonical 是全库唯一,转载/同文
        多账号抓取时,账号 B 的文章 canonical 会撞账号 A 的行)的处理:
          * 新增路径:INSERT dedup_key=canonical 撞约束 → 改用
            dedup_key=fallback_key(仅本账号会生成该键,本账号内唯一)再插
            一次,url 仍为 canonical、状态 ok → 返回 **'new'**(本账号行数
            +1,与计数语义一致;不触碰他账号的行)。fallback_key 也撞
            (同账号同标题同日的真重复)才回滚 → 'exists'。
          * 升级路径:UPDATE dedup_key=canonical 撞约束 → 原地只升
            url/url_status(/fallback_key 等),保留该行原 dedup_key →
            返回 **'upgraded'**(pending → ok,行数不变)。
        要点:每个账号对自己扫到的每篇文都要落一条**终态行**,fallback_key
        才会进 seen_fallbacks;否则该账号每轮都重开这篇的文章页(~40s,永不
        入库)。代价:同一 canonical 允许跨账号各有一行(dedup_key 退化为本
        账号的 fallback_key),属预期。canonical 命中的 ok 行若**标题不同**
        (同账号两条列表条目指向同一 URL),视作另一篇,同样按上述冲突路径
        落自己的行;标题相同才是同一篇(fallback 文本漂移场景)→ 'exists'。
        """
        t = self.cfg.table_articles
        if canonical_url:
            with self.conn.cursor() as c:
                c.execute(f"SELECT id, url_status, title FROM `{t}` "
                          "WHERE account_id=%s AND dedup_key=%s",
                          (account_id, canonical_url))
                row = c.fetchone()
                if row is None:
                    c.execute(f"SELECT id, url_status, title FROM `{t}` "
                              "WHERE account_id=%s AND title=%s "
                              "AND url_status='pending'", (account_id, title))
                    row = c.fetchone()
            if row is not None:
                if row["url_status"] == "pending":
                    try:
                        with self.conn.cursor() as c:
                            c.execute(f"UPDATE `{t}` SET dedup_key=%s, url=%s, "
                                      "url_status='ok', fallback_key=%s, title=%s, "
                                      "date_text=%s, publish_date=%s WHERE id=%s",
                                      (canonical_url, canonical_url, fallback_key,
                                       title, date_text, publish_date, row["id"]))
                        self.conn.commit()
                        return "upgraded"
                    except _CONFLICT_ERRORS:
                        self.conn.rollback()
                        return self._upgrade_keep_key(
                            row["id"], canonical_url, fallback_key, title,
                            date_text, publish_date)
                # ok 行且标题不同 = 同账号另一条列表条目指着同一 URL:
                # 不算同一篇,继续往下给本条目落自己的行(不 return exists)
                if row["title"] == title:
                    return "exists"
        with self.conn.cursor() as c:
            c.execute(f"SELECT id, url_status FROM `{t}` "
                      "WHERE account_id=%s AND fallback_key=%s",
                      (account_id, fallback_key))
            row = c.fetchone()
        if row is None:
            key = canonical_url or fallback_key
            try:
                with self.conn.cursor() as c:
                    c.execute(f"INSERT INTO `{t}`(account_id, dedup_key, fallback_key, "
                              "url, title, date_text, publish_date, url_status, "
                              "created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())",
                              (account_id, key, fallback_key, canonical_url, title,
                               date_text, publish_date,
                               "ok" if canonical_url else "pending"))
                self.conn.commit()
                return "new"
            except _CONFLICT_ERRORS:
                # dedup_key 撞已有 canonical(他账号同文/同账号另一篇)——
                # 仍要给本账号落一条终态行,否则 fallback_key 永不进 seen
                self.conn.rollback()
                if canonical_url:
                    try:
                        with self.conn.cursor() as c:
                            c.execute(f"INSERT INTO `{t}`(account_id, dedup_key, "
                                      "fallback_key, url, title, date_text, "
                                      "publish_date, url_status, created_at) "
                                      "VALUES (%s,%s,%s,%s,%s,%s,%s,'ok',NOW())",
                                      (account_id, fallback_key, fallback_key,
                                       canonical_url, title, date_text, publish_date))
                        self.conn.commit()
                        return "new"
                    except _CONFLICT_ERRORS:
                        self.conn.rollback()  # 同账号真重复(fallback_key 也撞)
                return "exists"
        if canonical_url and row["url_status"] == "pending":
            try:
                with self.conn.cursor() as c:
                    c.execute(f"UPDATE `{t}` SET dedup_key=%s, url=%s, "
                              "url_status='ok' WHERE id=%s",
                              (canonical_url, canonical_url, row["id"]))
                self.conn.commit()
                return "upgraded"
            except _CONFLICT_ERRORS:
                self.conn.rollback()
                return self._upgrade_keep_key(
                    row["id"], canonical_url, fallback_key, title, date_text,
                    publish_date)
        return "exists"

    def _upgrade_keep_key(self, row_id: int, canonical_url: str | None, fallback_key: str,
                          title: str, date_text: str | None,
                          publish_date: str | None) -> str:
        """canonical 被他行持有时原地升级:保留原 dedup_key,只升 URL/状态。"""
        try:
            with self.conn.cursor() as c:
                c.execute(f"UPDATE `{self.cfg.table_articles}` SET url=%s, "
                          "url_status='ok', fallback_key=%s, title=%s, date_text=%s, "
                          "publish_date=%s WHERE id=%s",
                          (canonical_url, fallback_key, title, date_text,
                           publish_date, row_id))
            self.conn.commit()
            return "upgraded"
        except _CONFLICT_ERRORS:
            self.conn.rollback()
            return "exists"

    # ---- 运行记录 ----

    def start_run(self) -> int:
        with self.conn.cursor() as c:
            c.execute(f"INSERT INTO `{self.cfg.table_runs}`(started_at) "
                      "VALUES (NOW())")
            run_id = c.lastrowid
        self.conn.commit()
        return run_id

    def finish_run(self, run_id: int, ok_count: int, fail_count: int, new_count: int):
        with self.conn.cursor() as c:
            c.execute(f"UPDATE `{self.cfg.table_runs}` SET finished_at=NOW(), "
                      "ok_count=%s, fail_count=%s, new_count=%s WHERE id=%s",
                      (ok_count, fail_count, new_count, run_id))
        self.conn.commit()
