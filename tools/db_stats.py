"""数据库巡检:python tools/db_stats.py [data/crawler.db]"""
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

db = Path(sys.argv[1] if len(sys.argv) > 1 else "data/crawler.db")
if not db.exists():
    sys.exit(f"数据库不存在: {db}")
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row
print("== 账号 ==")
for r in conn.execute(
        "SELECT name, last_crawled_at, max_publish_date FROM accounts ORDER BY name"):
    print(f"  {r['name']}: 水位线={r['max_publish_date']} 最近抓取={r['last_crawled_at']}")
print("== 文章(按账号) ==")
for r in conn.execute(
        "SELECT a.name, COUNT(*) n, SUM(s.url_status='ok') ok "
        "FROM articles s JOIN accounts a ON a.id=s.account_id GROUP BY a.name"):
    print(f"  {r['name']}: {r['n']} 篇(有URL {r['ok']})")
print("== 最近 10 篇 ==")
for r in conn.execute(
        "SELECT a.name, s.title, s.publish_date, s.url_status, substr(s.url,1,60) u "
        "FROM articles s JOIN accounts a ON a.id=s.account_id "
        "ORDER BY s.id DESC LIMIT 10"):
    print(f"  [{r['name']}] {r['publish_date'] or '?'} ({r['url_status']}) "
          f"{r['title'][:36]}\n      {r['u'] or ''}")
print("== 运行记录(最近 5 轮) ==")
for r in conn.execute(
        "SELECT started_at, finished_at, ok_count, fail_count, new_count "
        "FROM crawl_runs ORDER BY id DESC LIMIT 5"):
    print(f"  {r['started_at']} → {r['finished_at']}: "
          f"成功{r['ok_count']} 失败{r['fail_count']} 新增{r['new_count']}")
