"""向真实钉钉群发送一条测试消息,验证 webhook / 加签 / 关键词 / @ 配置。

用法: .venv/Scripts/python.exe tools/test_notify.py [文章URL]
URL 缺省用示例链接;配置读 config/settings.yaml 的 notify 段。
退出码 0=成功 1=失败。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import notify  # noqa: E402
from src.config import ConfigError, load_config  # noqa: E402


def main() -> int:
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"配置无效: {exc}")
        return 1
    art = {"account": "爬虫测试", "title": "钉钉推送连通性测试",
           "url": sys.argv[1] if len(sys.argv) > 1 else "https://example.com/",
           "date_text": "今天"}
    ok, msg = notify.send_article(cfg.notify, art)
    if ok:
        n = cfg.notify
        targets = [x for x in [f"@{n.at_robot_name}" if n.at_robot_name else "",
                               f"userId={n.at_user_ids}" if n.at_user_ids else "",
                               f"手机号={n.at_mobiles}" if n.at_mobiles else "",
                               "@所有人" if n.at_all else ""] if x]
        print("发送成功,请到钉钉群确认消息与@效果" +
              (f"(@目标: {' '.join(targets)})" if targets else "(未配置@目标)"))
        return 0
    print(f"发送失败: {msg}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
