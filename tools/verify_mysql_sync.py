"""对账工具:SQLite ↔ MySQL 逐行比对 + 展示今日入库文章。

注意 pymysql.fetchall() 返回 tuple、sqlite3 返回 list,
必须各自转 list 再比(tuple/list 类型不同恒不等)。
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 控制台 GBK 环境下打印中文标题(可能含 \\xa0 等)会崩,统一 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pymysql

from src.config import load_config

m = load_config().mysql
my = pymysql.connect(host=m.host, port=m.port, user=m.user,
                     password=m.password, database=m.database, charset="utf8mb4")
sl = sqlite3.connect("data/crawler.db")

art_sql = ("SELECT id, account_id, dedup_key, COALESCE(url,''), title, "
           "COALESCE(publish_date,''), url_status, COALESCE(created_at,'') "
           "FROM {t} ORDER BY id")
acc_sql = ("SELECT id, name, COALESCE(last_crawled_at,''), "
           "COALESCE(max_publish_date,'') FROM {t} ORDER BY id")

srows = sl.execute(art_sql.format(t="articles")).fetchall()
s_acc = sl.execute(acc_sql.format(t="accounts")).fetchall()
with my.cursor() as c:
    c.execute(art_sql.format(t=m.table_articles))
    mrows = c.fetchall()
    c.execute(acc_sql.format(t=m.table_accounts))
    m_acc = c.fetchall()

srows = [tuple(r) for r in srows]
mrows = [tuple(r) for r in mrows]
s_acc = [tuple(r) for r in s_acc]
m_acc = [tuple(r) for r in m_acc]

print("文章: SQLite", len(srows), "行 / MySQL", len(mrows), "行 / 逐行一致:",
      srows == mrows)
print("账号: SQLite", len(s_acc), "行 / MySQL", len(m_acc), "行 / 逐行一致:",
      s_acc == m_acc)
if srows != mrows:
    for _a, _b in zip(srows, mrows):
        if _a != _b:
            print("首个差异行 SQLite:", _a)
            print("首个差异行 MySQL :", _b)
            print("差异字段:", [(i, repr(x), repr(y)) for i, (x, y)
                                 in enumerate(zip(_a, _b)) if x != y])
            break
print()
print("今天(09-04)入库的文章,MySQL 实查:")
with my.cursor() as c:
    c.execute(f"SELECT created_at, publish_date, url_status, title "
              f"FROM {m.table_articles} WHERE created_at LIKE '2026-09-04%' "
              f"ORDER BY id DESC LIMIT 5")
    for r in c.fetchall():
        print(" ", r[0], r[1], r[2], r[3][:26])
my.close()
sl.close()
