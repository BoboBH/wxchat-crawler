"""数据库巡检:python tools/db_stats.py(MySQL 主库,2026-09-05 起)。

只读:连接参数在 .env(WXCHAT_CRAWLER_*),表名在 settings.yaml 的 mysql:
节;不建库建表(连不上/表不存在时直接报错退出)。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymysql  # noqa: E402
import pymysql.cursors  # noqa: E402

from src.config import ConfigError, load_config  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    cfg = load_config().mysql
except ConfigError as exc:
    sys.exit(f"配置无效: {exc}")

try:
    conn = pymysql.connect(host=cfg.host, port=cfg.port, user=cfg.user,
                           password=cfg.password, database=cfg.database,
                           charset="utf8mb4",
                           cursorclass=pymysql.cursors.DictCursor)
except Exception as exc:
    sys.exit(f"MySQL 连接失败({cfg.user}@{cfg.host}:{cfg.port}/{cfg.database}): {exc}")

print(f"== 库 {cfg.database}@{cfg.host}:{cfg.port} ==")
print("== 账号 ==")
with conn.cursor() as c:
    c.execute(f"SELECT name, last_crawled_at, max_publish_date "
              f"FROM `{cfg.table_accounts}` ORDER BY name")
    for r in c.fetchall():
        print(f"  {r['name']}: 水位线={r['max_publish_date']} "
              f"最近抓取={r['last_crawled_at']}")
print("== 文章(按账号) ==")
with conn.cursor() as c:
    c.execute(f"SELECT a.name, COUNT(*) n, SUM(s.url_status='ok') ok "
              f"FROM `{cfg.table_articles}` s "
              f"JOIN `{cfg.table_accounts}` a ON a.id=s.account_id GROUP BY a.name")
    for r in c.fetchall():
        print(f"  {r['name']}: {r['n']} 篇(有URL {r['ok']})")
print("== 最近 10 篇 ==")
with conn.cursor() as c:
    c.execute(f"SELECT a.name, s.title, s.publish_date, s.url_status, "
              f"substr(s.url,1,60) u FROM `{cfg.table_articles}` s "
              f"JOIN `{cfg.table_accounts}` a ON a.id=s.account_id "
              f"ORDER BY s.id DESC LIMIT 10")
    for r in c.fetchall():
        print(f"  [{r['name']}] {r['publish_date'] or '?'} ({r['url_status']}) "
              f"{r['title'][:36]}\n      {r['u'] or ''}")
print("== 运行记录(最近 5 轮) ==")
try:
    with conn.cursor() as c:
        c.execute(f"SELECT started_at, finished_at, ok_count, fail_count, "
                  f"new_count FROM `{cfg.table_runs}` ORDER BY id DESC LIMIT 5")
        for r in c.fetchall():
            print(f"  {r['started_at']} → {r['finished_at']}: "
                  f"成功{r['ok_count']} 失败{r['fail_count']} 新增{r['new_count']}")
except pymysql.err.ProgrammingError:
    print(f"  (表 {cfg.table_runs} 尚未创建 —— 首轮抓取后自动建)")
conn.close()
