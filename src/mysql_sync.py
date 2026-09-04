"""MySQL 镜像:每轮抓取结束把 SQLite 全量同步到 MySQL(单向,SQLite 是主库)。

设计(2026-09-04 用户需求):数据先落 SQLite,MySQL(localhost:3306)自动
保持一致 —— 每轮 run() 结束做一次全量对账式 upsert(按主键 id 对齐,
INSERT … AS new ON DUPLICATE KEY UPDATE),幂等可重放:任何漂移/缺行
下一轮自动补齐。行 id 原样保留,account_id 关联关系不乱。

表结构按 db.py 的 SQLite schema 平移,列名一致:
  wechat_crawler_accounts / wechat_crawler_articles(表名可在 settings.yaml
  的 mysql: 节改)。dedup_key 是 canonical URL(实测最长 106 字符),取
  VARCHAR(191) —— utf8mb4 下唯一索引 191*4=764 字节,在 InnoDB 3072 字节
  键长限内。文本列一律存 SQLite 的原样字符串(日期/时间戳保持 TEXT 语义)。
crawl_runs 为运行簿记,不镜像(用户指定镜像 accounts/articles 两表)。
"""
from __future__ import annotations

import logging
import sqlite3

from .config import MysqlConfig


def _connect(cfg: MysqlConfig):
    """连接 MySQL 并确保库/表存在(延迟导入:未启用 MySQL 的环境不必装 pymysql)。"""
    import pymysql

    conn = pymysql.connect(host=cfg.host, port=cfg.port, user=cfg.user,
                           password=cfg.password, charset="utf8mb4",
                           autocommit=False)
    with conn.cursor() as c:
        c.execute(f"CREATE DATABASE IF NOT EXISTS `{cfg.database}` "
                  "DEFAULT CHARSET utf8mb4")
        c.execute(f"USE `{cfg.database}`")
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS `{cfg.table_accounts}` (
                id INT PRIMARY KEY,
                name VARCHAR(191) NOT NULL UNIQUE,
                last_crawled_at VARCHAR(32),
                max_publish_date VARCHAR(32)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS `{cfg.table_articles}` (
                id INT PRIMARY KEY,
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
    conn.commit()
    return conn


def sync_store(cfg: MysqlConfig, conn: sqlite3.Connection,
               log: logging.Logger | None = None) -> dict:
    """把 SQLite 连接里的 accounts/articles 全量 upsert 进 MySQL。

    返回 {accounts: n, articles: n};任何 pymysql 异常原样抛出,由调用方
    决定降级方式(编排层捕获后仅告警,不影响抓取主流程)。
    """
    accs = conn.execute(
        "SELECT id, name, last_crawled_at, max_publish_date "
        "FROM accounts ORDER BY id").fetchall()
    arts = conn.execute(
        "SELECT id, account_id, dedup_key, fallback_key, url, title, date_text, "
        "publish_date, url_status, created_at FROM articles ORDER BY id").fetchall()
    mysql = _connect(cfg)
    try:
        with mysql.cursor() as c:
            if accs:
                c.executemany(
                    f"INSERT INTO `{cfg.table_accounts}` "
                    "(id, name, last_crawled_at, max_publish_date) "
                    "VALUES (%s,%s,%s,%s) AS new "
                    "ON DUPLICATE KEY UPDATE name=new.name, "
                    "last_crawled_at=new.last_crawled_at, "
                    "max_publish_date=new.max_publish_date",
                    [tuple(r) for r in accs])
            if arts:
                c.executemany(
                    f"INSERT INTO `{cfg.table_articles}` "
                    "(id, account_id, dedup_key, fallback_key, url, title, "
                    "date_text, publish_date, url_status, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) AS new "
                    "ON DUPLICATE KEY UPDATE account_id=new.account_id, "
                    "dedup_key=new.dedup_key, fallback_key=new.fallback_key, "
                    "url=new.url, title=new.title, date_text=new.date_text, "
                    "publish_date=new.publish_date, url_status=new.url_status, "
                    "created_at=new.created_at",
                    [tuple(r) for r in arts])
        mysql.commit()
    finally:
        mysql.close()
    out = {"accounts": len(accs), "articles": len(arts)}
    if log:
        log.info("[MySQL] 已同步 %s:%s/%s: 账号%d 行 文章%d 行",
                 cfg.host, cfg.port, cfg.database, out["accounts"], out["articles"])
    return out


def sync_file(cfg: MysqlConfig, db_path, log: logging.Logger | None = None) -> dict:
    """独立入口:直接对 SQLite 文件做一次手动补同步(不动 Store)。"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return sync_store(cfg, conn, log=log)
    finally:
        conn.close()
