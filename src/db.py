"""SQLite 持久层:账号、文章(双键去重)、水位线、运行记录。

articles 的去重设计:
  * dedup_key UNIQUE —— 有 canonical URL 时即 canonical URL,否则 fallback_key;
  * fallback_key —— 标题|日期 哈希,列表扫描阶段用于"已见过?"判断,
    避免对已入库文章重复打开文章页。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    last_crawled_at TEXT,
    max_publish_date TEXT
);
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    dedup_key TEXT NOT NULL UNIQUE,
    fallback_key TEXT NOT NULL,
    url TEXT,
    title TEXT NOT NULL,
    date_text TEXT,
    publish_date TEXT,
    url_status TEXT NOT NULL DEFAULT 'pending' CHECK (url_status IN ('ok', 'pending')),
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_articles_account ON articles(account_id);
CREATE INDEX IF NOT EXISTS idx_articles_fallback ON articles(account_id, fallback_key);
CREATE TABLE IF NOT EXISTS crawl_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    finished_at TEXT,
    ok_count INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0,
    new_count INTEGER NOT NULL DEFAULT 0
);
"""


class Store:
    def __init__(self, db_path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---- 账号 ----

    def get_or_create_account(self, name: str) -> int:
        cur = self.conn.execute("SELECT id FROM accounts WHERE name=?", (name,))
        row = cur.fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute("INSERT INTO accounts(name) VALUES (?)", (name,))
        self.conn.commit()
        return cur.lastrowid

    def watermark(self, account_id: int) -> str | None:
        row = self.conn.execute("SELECT max_publish_date FROM accounts WHERE id=?",
                                (account_id,)).fetchone()
        return row["max_publish_date"] if row else None

    def set_watermark(self, account_id: int, iso_date: str):
        self.conn.execute(
            "UPDATE accounts SET max_publish_date=MAX(IFNULL(max_publish_date,''), ?) "
            "WHERE id=?", (iso_date, account_id))
        self.conn.commit()

    def mark_crawled(self, account_id: int):
        self.conn.execute(
            "UPDATE accounts SET last_crawled_at=datetime('now','localtime') WHERE id=?",
            (account_id,))
        self.conn.commit()

    # ---- 文章 ----

    def seen_fallbacks(self, account_id: int, status: str | None = None) -> set[str]:
        """该账号已入库的 fallback_key;status 过滤(如 'ok' → 仅成功提取过 URL 的)。"""
        sql = "SELECT fallback_key FROM articles WHERE account_id=?"
        args: list = [account_id]
        if status:
            sql += " AND url_status=?"
            args.append(status)
        rows = self.conn.execute(sql, args).fetchall()
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
        if canonical_url:
            row = self.conn.execute(
                "SELECT id, url_status, title FROM articles WHERE account_id=? AND dedup_key=?",
                (account_id, canonical_url)).fetchone()
            if row is None:
                row = self.conn.execute(
                    "SELECT id, url_status, title FROM articles "
                    "WHERE account_id=? AND title=? AND url_status='pending'",
                    (account_id, title)).fetchone()
            if row is not None:
                if row["url_status"] == "pending":
                    try:
                        self.conn.execute(
                            "UPDATE articles SET dedup_key=?, url=?, url_status='ok', "
                            "fallback_key=?, title=?, date_text=?, publish_date=? WHERE id=?",
                            (canonical_url, canonical_url, fallback_key, title, date_text,
                             publish_date, row["id"]))
                        self.conn.commit()
                        return "upgraded"
                    except sqlite3.IntegrityError:
                        self.conn.rollback()
                        return self._upgrade_keep_key(
                            row["id"], canonical_url, fallback_key, title,
                            date_text, publish_date)
                # ok 行且标题不同 = 同账号另一条列表条目指着同一 URL:
                # 不算同一篇,继续往下给本条目落自己的行(不 return exists)
                if row["title"] == title:
                    return "exists"
        row = self.conn.execute(
            "SELECT id, url_status FROM articles WHERE account_id=? AND fallback_key=?",
            (account_id, fallback_key)).fetchone()
        if row is None:
            key = canonical_url or fallback_key
            try:
                self.conn.execute(
                    "INSERT INTO articles(account_id, dedup_key, fallback_key, url, title, "
                    "date_text, publish_date, url_status) VALUES (?,?,?,?,?,?,?,?)",
                    (account_id, key, fallback_key, canonical_url, title, date_text,
                     publish_date, "ok" if canonical_url else "pending"))
                self.conn.commit()
                return "new"
            except sqlite3.IntegrityError:
                # dedup_key 撞已有 canonical(他账号同文/同账号另一篇)——
                # 仍要给本账号落一条终态行,否则 fallback_key 永不进 seen
                self.conn.rollback()
                if canonical_url:
                    try:
                        self.conn.execute(
                            "INSERT INTO articles(account_id, dedup_key, fallback_key, url, "
                            "title, date_text, publish_date, url_status) "
                            "VALUES (?,?,?,?,?,?,?,'ok')",
                            (account_id, fallback_key, fallback_key, canonical_url,
                             title, date_text, publish_date))
                        self.conn.commit()
                        return "new"
                    except sqlite3.IntegrityError:
                        self.conn.rollback()  # 同账号真重复(fallback_key 也撞)
                return "exists"
        if canonical_url and row["url_status"] == "pending":
            try:
                self.conn.execute(
                    "UPDATE articles SET dedup_key=?, url=?, url_status='ok' WHERE id=?",
                    (canonical_url, canonical_url, row["id"]))
                self.conn.commit()
                return "upgraded"
            except sqlite3.IntegrityError:
                self.conn.rollback()
                return self._upgrade_keep_key(
                    row["id"], canonical_url, fallback_key, title, date_text, publish_date)
        return "exists"

    def _upgrade_keep_key(self, row_id: int, canonical_url: str | None, fallback_key: str,
                          title: str, date_text: str | None,
                          publish_date: str | None) -> str:
        """canonical 被他行持有时原地升级:保留原 dedup_key,只升 URL/状态。"""
        try:
            self.conn.execute(
                "UPDATE articles SET url=?, url_status='ok', fallback_key=?, "
                "title=?, date_text=?, publish_date=? WHERE id=?",
                (canonical_url, fallback_key, title, date_text, publish_date, row_id))
            self.conn.commit()
            return "upgraded"
        except sqlite3.IntegrityError:
            self.conn.rollback()
            return "exists"

    # ---- 运行记录 ----

    def start_run(self) -> int:
        cur = self.conn.execute("INSERT INTO crawl_runs DEFAULT VALUES")
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, ok_count: int, fail_count: int, new_count: int):
        self.conn.execute(
            "UPDATE crawl_runs SET finished_at=datetime('now','localtime'), "
            "ok_count=?, fail_count=?, new_count=? WHERE id=?",
            (ok_count, fail_count, new_count, run_id))
        self.conn.commit()
