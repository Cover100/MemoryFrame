"""install.sh's fan step: gpio-fan overlay added once, with a backup."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINE = "dtoverlay=gpio-fan,gpiopin=14,temp=60000"


def run(cfg):
    return subprocess.run(["bash", str(ROOT / "deploy" / "fan-overlay.sh")], capture_output=True, text=True,
                          env={"MEMORYFRAME_BOOT_CONFIG": str(cfg), "PATH": "/usr/bin:/bin"}, timeout=10)


def test_run_twice_gives_one_line_and_one_backup(tmp_path):
    cfg = tmp_path / "config.txt"
    cfg.write_text("[all]\ndtparam=audio=on")          # no trailing newline
    assert run(cfg).returncode == 0 and run(cfg).returncode == 0
    lines = cfg.read_text().splitlines()
    assert lines.count(LINE) == 1 and lines[:2] == ["[all]", "dtparam=audio=on"]
    baks = list(tmp_path.glob("config.txt.memoryframe-bak.*"))
    assert len(baks) == 1 and baks[0].read_text() == "[all]\ndtparam=audio=on"


def test_existing_different_fan_line_left_alone(tmp_path):
    cfg = tmp_path / "config.txt"
    cfg.write_text("dtoverlay=gpio-fan,gpiopin=18\n")
    run(cfg)
    assert cfg.read_text() == "dtoverlay=gpio-fan,gpiopin=18\n"


def test_missing_config_is_skipped(tmp_path):
    r = run(tmp_path / "nope.txt")
    assert r.returncode == 0 and not (tmp_path / "nope.txt").exists()


def test_install_calls_fan_step():
    assert "deploy/fan-overlay.sh" in (ROOT / "deploy" / "install.sh").read_text()
