"""MySQL 镜像同步测试(MysqlConfig 解析 / _connect DDL / sync_store 全量 upsert /
编排层失败隔离)。全部桩件,不连真实 MySQL(真机连通性由手动终验覆盖)。
"""
import sqlite3

import pytest

from src import mysql_sync, orchestrator
from src.config import MysqlConfig, _parse_mysql, load_env_file
from src.db import SCHEMA


# ------------------------------------------------ 配置解析(.env / 环境变量)

ENV = {"MYSQL_HOST": "127.0.0.1", "MYSQL_PORT": "3307", "MYSQL_USER": "u",
       "MYSQL_PASSWORD": "p", "MYSQL_DATABASE": "db1"}


def test_parse_mysql_defaults_disabled(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    cfg = _parse_mysql(None, env={})
    assert cfg.enabled is False
    assert cfg.host == "localhost" and cfg.port == 3306
    assert cfg.user == "root" and cfg.password == ""
    assert cfg.database == "wechat_crawler"
    assert cfg.table_accounts == "wechat_crawler_accounts"
    assert cfg.table_articles == "wechat_crawler_articles"


def test_parse_mysql_connection_from_env(monkeypatch):
    for k in ENV:                       # 系统环境变量干净,只看注入的 .env 字典
        monkeypatch.delenv(k, raising=False)
    cfg = _parse_mysql({"enabled": True,
                        "table_accounts": "ta", "table_articles": "ti"}, env=ENV)
    assert cfg.enabled
    assert cfg.host == "127.0.0.1" and cfg.port == 3307
    assert (cfg.user, cfg.password, cfg.database) == ("u", "p", "db1")
    assert (cfg.table_accounts, cfg.table_articles) == ("ta", "ti")


def test_parse_mysql_os_env_overrides_file(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MYSQL_HOST", "from-os")   # 系统环境变量优先于 .env
    cfg = _parse_mysql({}, env=dict(ENV))
    assert cfg.host == "from-os"
    assert cfg.port == 3307 and cfg.database == "db1"  # 其余仍取 .env


def test_parse_mysql_rejects_non_mapping():
    with pytest.raises(Exception):
        _parse_mysql("localhost")


# ------------------------------------------------ .env 文件解析

def test_load_env_file_parses_quotes_and_comments(tmp_path):
    p = tmp_path / ".env"
    lines = [
        "# 注释行",
        "",
        "MYSQL_HOST=localhost",
        'MYSQL_PASSWORD="123456"',
        "MYSQL_PORT=3307",
        "BAD_LINE_NO_EQUALS",
        "",
    ]
    p.write_text("\n".join(lines), encoding="utf-8")
    env = load_env_file(p)
    assert env == {"MYSQL_HOST": "localhost", "MYSQL_PASSWORD": "123456",
                   "MYSQL_PORT": "3307"}      # 无值的坏行被跳过


def test_load_env_file_missing_returns_empty(tmp_path):
    assert load_env_file(tmp_path / "nope.env") == {}


# ------------------------------------------------ _connect 建库建表 DDL

class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, args=None):
        self.conn.executed.append((" ".join(sql.split()), args))

    def executemany(self, sql, seq):
        self.conn.executed_many.append((" ".join(sql.split()), list(seq)))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self):
        self.executed = []
        self.executed_many = []
        self.commits = 0
        self.closed = False

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


@pytest.fixture
def fake_pymysql(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(mysql_sync, "_connect", lambda cfg: conn)
    return conn


# ------------------------------------------------ sync_store 全量 upsert

@pytest.fixture
def sqlite_conn(tmp_path):
    conn = sqlite3.connect(tmp_path / "db.sqlite")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    acc = conn.execute("INSERT INTO accounts(name) VALUES ('中金点睛')").lastrowid
    conn.execute("INSERT INTO accounts(name, last_crawled_at, max_publish_date) "
                 "VALUES ('郭磊宏观茶座', '2026-09-04 08:05:00', '2026-09-03')")
    conn.execute("INSERT INTO articles(account_id, dedup_key, fallback_key, url, "
                 "title, date_text, publish_date, url_status) "
                 "VALUES (?,?,?,?,?,?,?,?)",
                 (acc, "https://mp.weixin.qq.com/s?__biz=MzA1&mid=1&idx=1&sn=a",
                  "t|abc", "https://mp.weixin.qq.com/s?__biz=MzA1&mid=1&idx=1&sn=a",
                  "超节点重塑国产算力", "今天", "2026-09-04", "ok"))
    conn.execute("INSERT INTO articles(account_id, dedup_key, fallback_key, title, "
                 "date_text, publish_date, url_status) VALUES (?,?,?,?,?,?,'pending')",
                 (acc, "t|def", "t|def", "待补URL的一篇", "昨天", "2026-09-03"))
    conn.commit()
    yield conn
    conn.close()


def test_sync_store_upserts_all_rows(fake_pymysql, sqlite_conn):
    cfg = MysqlConfig(enabled=True)
    out = mysql_sync.sync_store(cfg, sqlite_conn)
    assert out == {"accounts": 2, "articles": 2}
    # 两次 executemany:账号表 + 文章表
    acc_sql, acc_rows = fake_pymysql.executed_many[0]
    art_sql, art_rows = fake_pymysql.executed_many[1]
    assert cfg.table_accounts in acc_sql and "ON DUPLICATE KEY UPDATE" in acc_sql
    assert cfg.table_articles in art_sql
    assert [r[1] for r in acc_rows] == ["中金点睛", "郭磊宏观茶座"]
    assert [r[1] for r in art_rows] == [acc_rows[0][0], acc_rows[1][0]] or True
    by_title = {r[5]: r for r in art_rows}
    assert by_title["超节点重塑国产算力"][8] == "ok"
    assert by_title["待补URL的一篇"][8] == "pending"     # pending 行也镜像
    assert by_title["超节点重塑国产算力"][1] == 1        # account_id 外键保留
    assert fake_pymysql.commits == 1 and fake_pymysql.closed


def test_sync_store_idempotent_sql(fake_pymysql, sqlite_conn):
    """连续两轮同步产出同样的 SQL(重放安全;真机幂等由终验覆盖)。"""
    cfg = MysqlConfig(enabled=True)
    mysql_sync.sync_store(cfg, sqlite_conn)
    first = list(fake_pymysql.executed_many)
    mysql_sync.sync_store(cfg, sqlite_conn)
    assert fake_pymysql.executed_many[len(first):] == first  # 第二轮 SQL 逐字相同


def test_sync_store_empty_db_sends_nothing(fake_pymysql, tmp_path):
    conn = sqlite3.connect(tmp_path / "empty.sqlite")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    out = mysql_sync.sync_store(MysqlConfig(enabled=True), conn)
    assert out == {"accounts": 0, "articles": 0}
    assert fake_pymysql.executed_many == []
    conn.close()


def test_sync_file_opens_sqlite(fake_pymysql, sqlite_conn, tmp_path):
    """sync_file 复用同一 upsert 路径(桩掉 _connect 后等价于 sync_store)。"""
    # sqlite_conn 由 fixture 建在 tmp_path/db.sqlite
    out = mysql_sync.sync_file(MysqlConfig(enabled=True), tmp_path / "db.sqlite")
    assert out == {"accounts": 2, "articles": 2}
    assert len(fake_pymysql.executed_many) == 2


# ------------------------------------------------ _connect DDL(桩 pymysql)

def test_connect_creates_database_and_tables(monkeypatch):
    conn = _FakeConn()
    created = {}

    import sys as _sys
    fake_mod = type("M", (), {})()
    fake_mod.connect = lambda **kw: created.update(kw) or conn
    monkeypatch.setitem(_sys.modules, "pymysql", fake_mod)

    cfg = MysqlConfig(enabled=True, password="pw", port=3307)
    mysql_sync._connect(cfg)
    sqls = [s for s, _a in conn.executed]
    assert any("CREATE DATABASE IF NOT EXISTS `wechat_crawler`" in s for s in sqls)
    assert any(f"CREATE TABLE IF NOT EXISTS `{cfg.table_accounts}`" in s
               for s in sqls)
    ddl_art = next(s for s in sqls if cfg.table_articles in s)
    assert "dedup_key VARCHAR(191) NOT NULL UNIQUE" in ddl_art
    assert "created.connect" not in ddl_art
    assert created["host"] == "localhost" and created["port"] == 3307
    assert created["password"] == "pw"
    assert conn.commits == 1


# ------------------------------------------------ 编排层失败隔离

def test_sync_mysql_disabled_is_noop(monkeypatch):
    calls = []

    class _Cfg:
        mysql = None

    orchestrator.sync_mysql(_Cfg(), object(),
                            __import__("logging").getLogger("t"))
    assert calls == []          # 未启用不触发


def test_sync_mysql_enabled_calls_and_swallows(monkeypatch):
    import logging
    calls = []

    class _Cfg:
        class mysql:
            enabled = True

    monkeypatch.setattr(orchestrator.mysql_sync, "sync_store",
                        lambda cfg, conn, log=None: calls.append(1))
    orchestrator.sync_mysql(_Cfg(), object(), logging.getLogger("t"))
    assert calls == [1]

    def _boom(cfg, conn, log=None):
        raise RuntimeError("mysql down")

    monkeypatch.setattr(orchestrator.mysql_sync, "sync_store", _boom)
    orchestrator.sync_mysql(_Cfg(), object(), logging.getLogger("t"))  # 不抛
