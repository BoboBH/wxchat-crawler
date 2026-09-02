"""version_check 纯决策逻辑测试(不依赖真实微信进程)。"""
from src.version_check import build_report, parse_version, version_matches


def test_parse_version():
    assert parse_version("4.1.12.55") == (4, 1, 12, 55)
    assert parse_version("4.1") == (4, 1)
    assert parse_version("abc") == ()
    assert parse_version(None) == ()


def test_version_matches():
    assert version_matches("4.1.12.55", "4.1") is True
    assert version_matches("4.1.12.55", "4.1.12") is True
    assert version_matches("4.2.0.1", "4.1") is False
    assert version_matches(None, "4.1") is False
    assert version_matches("4.1", "4.1.12") is False  # 前缀不能比版本本身长


def test_report_not_running():
    rep = build_report("Weixin.exe", "C:\\x\\Weixin.exe", "4.1", [], "4.1.12.55")
    assert rep["ok"] is False
    assert "未发现进程" in rep["message"]


def test_report_running_version_ok():
    rep = build_report("Weixin.exe", "C:\\x\\Weixin.exe", "4.1",
                       [100, 200], "4.1.12.55")
    assert rep["ok"] is True
    assert rep["pids"] == [100, 200]
    assert "4.1.12.55" in rep["message"]


def test_report_running_version_unknown():
    rep = build_report("Weixin.exe", "C:\\x\\Weixin.exe", "4.1", [100], None)
    assert rep["ok"] is True
    assert "版本未知" in rep["message"]


def test_report_version_mismatch():
    rep = build_report("Weixin.exe", "C:\\x\\Weixin.exe", "4.1", [100], "5.0.0.1")
    assert rep["ok"] is True
    assert "不同" in rep["message"]
