"""pytest 共享夹具:MySQL 测试库(2026-09-05 存储迁 MySQL 主库后引入)。

存储层只有 MySQL 一条通道;单测统一打到本机 MySQL 的**独立测试库**
`wxchat_crawler_test`(表名固定 wt_*,与主库 `test` 的 wechat_crawler_* 完全
隔离;连接参数复用 .env/系统环境变量,与生产同一套)。行级隔离靠每测唯一
的账号名/URL token(测试表常驻不清理 —— 绝不 DROP/TRUNCATE,见全局红线)。
本机 MySQL 不可达时,用到库的用例经 mysql_ready 夹具整体 skip,纯逻辑用例照跑。
"""
import os
import uuid
from dataclasses import replace

import pytest

from src.config import MysqlConfig, WXCHAT_CRAWLER_ENV_KEYS, load_env_file


def _test_mysql_config() -> MysqlConfig:
    """连接参数走 .env/系统环境变量(与生产同一套),库与表名固定为测试专用。"""
    env = dict(load_env_file())
    env.update({k: v for k, v in os.environ.items()
                if k in WXCHAT_CRAWLER_ENV_KEYS.values() and v != ""})

    def ev(field: str) -> str:
        return env.get(WXCHAT_CRAWLER_ENV_KEYS[field], "").strip()

    return MysqlConfig(
        host=ev("host") or "localhost",
        port=int(ev("port") or 3306),
        user=ev("user") or "root",
        password=ev("password"),
        database="wxchat_crawler_test",
        table_accounts="wt_accounts",
        table_articles="wt_articles",
        table_runs="wt_runs")


@pytest.fixture(scope="session")
def mysql_cfg() -> MysqlConfig:
    return _test_mysql_config()


@pytest.fixture(scope="session")
def mysql_ready(mysql_cfg) -> MysqlConfig:
    """会话级探活:连不上即 skip 整批存储类用例(不挂红)。"""
    from src.db import Store
    try:
        s = Store(mysql_cfg)
    except Exception as e:
        pytest.skip(f"本机 MySQL 不可达,跳过存储类用例({e})")
    s.close()
    return mysql_cfg


@pytest.fixture()
def store(mysql_ready):
    """每测独立的 Store 连接(测试库,表常驻,行靠唯一名隔离)。"""
    from src.db import Store
    s = Store(mysql_ready)
    yield s
    s.close()


@pytest.fixture()
def rows(store):
    """裸 SQL 查询助手:rows("SELECT ... WHERE x=%s", (v,)) → [dict, ...]。"""

    def _rows(sql, args=()):
        with store.conn.cursor() as c:
            c.execute(sql, args)
            return c.fetchall()

    return _rows


@pytest.fixture()
def make_name():
    """每测唯一的账号名/URL token:dedup_key 全库唯一、测试表跨运行留存,
    一切入库主键都得带 uuid 后缀,否则重跑测试会撞上一轮的行。"""

    def _make(prefix="测试号"):
        return f"{prefix}{uuid.uuid4().hex[:8]}"

    return _make


@pytest.fixture()
def make_canon():
    """每测唯一的 canonical URL(dedup_key 全库唯一约束,常量 URL 重跑必撞);
    同一测试内多次调用各不相同,同文跨账号场景取一次存变量复用。"""

    def _make(biz="x"):
        return (f"https://mp.weixin.qq.com/s?__biz={biz}&mid=1&idx=1"
                f"&sn={uuid.uuid4().hex[:12]}")

    return _make
