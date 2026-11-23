# Copyright (C) 2026 Sacrappy

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import run_bench as rb  # noqa: E402

DATA = Path(__file__).parent / "testdata"
SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]
MESA_VERSION, AMDGPU_CARDS = rb.mesa_version, rb.amdgpu_cards   # (the real ones; the fixture below replaces them)


@pytest.fixture(autouse=True)
def _own_machine(tmp_path, monkeypatch):
    """No test touches the user's saves (the guard copies and puts back the real data folder otherwise), starts
    vulkaninfo or reads the real GPU: the data folder is the test's own, Mesa unknown, no amdgpu card."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(rb, "mesa_version", lambda: None)
    monkeypatch.setattr(rb, "amdgpu_cards", lambda sys_root=None: [])


def test_percentile_and_stats():
    s = rb.compute_stats([10.0] * 98 + [20.0, 100.0])
    ft = s["frame_time_ms"]
    assert s["frames"] == 100
    assert ft["p50"] == 10.0 and ft["max"] == 100.0
    assert 10.0 < ft["p99"] < 100.0
    assert s["fps"] == pytest.approx(1000.0 / ft["mean"])
    assert set(ft) == {"mean", "p50", "p95", "p99", "max"}


def test_empty_stats():
    assert rb.compute_stats([])["frames"] == 0


def test_hookshot_trace_skips_first_frame():
    rows = rb.parse_hookshot_trace(DATA / "hookshot_present_trace.csv")
    assert len(rows) == 9
    assert rows[0] == (pytest.approx(0.016), 16.0)
    assert rows[-1][1] == 36.0
    assert rb.compute_stats(rb.window(rows, 0))["frame_time_ms"]["max"] == 36.0


def test_hookshot_trace_truncated_line(tmp_path):
    f = tmp_path / "t.csv"
    f.write_text("frame,return_ns,interval_ns\n1,100,0\n2,200000100,200000000\n3,40")
    assert len(rb.parse_hookshot_trace(f)) == 1


def test_missing_files():
    assert rb.parse_hookshot_trace(Path("nope.csv")) == []
    assert rb.parse_presentmon_csv(Path("nope.csv")) == []
    assert rb.parse_hookshot_log(Path("nope.txt")) == []


def test_hookshot_log_windows():
    assert rb.parse_hookshot_log(DATA / "hookshot_log.txt") == [58.3, 12.5, 57.9]


def test_presentmon_v1():
    rows = rb.parse_presentmon_csv(DATA / "presentmon_v1.csv")
    assert [ms for _, ms in rows] == [16.7, 16.7, 33.4, 16.6]
    assert rows[0][0] == 0.0 and rows[2][0] == pytest.approx(0.05)


def test_presentmon_v2():
    rows = rb.parse_presentmon_csv(DATA / "presentmon_v2.csv")
    assert [ms for _, ms in rows] == [16.7, 16.7, 33.4]


def test_warmup_window():
    rows = rb.parse_presentmon_csv(DATA / "presentmon_v1.csv")
    assert rb.window(rows, 0.04) == [33.4, 16.6]


def test_watchdog_fires_and_is_capped():
    assert rb.Watchdog(10_000).hard_limit_s == 120.0
    dog = rb.Watchdog(1.0)
    p = subprocess.Popen(SLEEPER)
    dog.add(p)
    dog.start()
    t0 = time.monotonic()
    while p.poll() is None and time.monotonic() - t0 < 15:
        time.sleep(0.1)
    dog.stop()
    assert p.poll() is not None and dog.fired


def test_watchdog_kills_on_exception():
    p = subprocess.Popen(SLEEPER)
    dog = rb.Watchdog(100)
    dog.add(p)
    with pytest.raises(RuntimeError):
        try:
            dog.start()
            raise RuntimeError("boom")
        finally:
            dog.stop()
    assert p.poll() is not None


def test_lichii_without_presentmon_refuses_before_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(rb, "find_presentmon", lambda _x: None)
    args = rb.build_parser().parse_args(
        ["--exe", str(tmp_path / "lichii.exe"), "--scene", "s", "--out", str(tmp_path / "o")])
    with pytest.raises(SystemExit):
        rb.run(args)
    assert not (tmp_path / "o").exists()


def test_end_to_end_with_fake_hookshot(tmp_path):
    """A fake game writes a present trace; the harness must kill it and write bench.json."""
    game = tmp_path / "fake_game.py"
    game.write_text(
        "import os, time\n"
        "f = open(os.environ['HOS_PRESENT_TRACE'], 'w')\n"
        "f.write('frame,return_ns,interval_ns\\n')\n"
        "t = 0\n"
        "for i in range(1, 100000):\n"
        "    f.write('%d,%d,%d\\n' % (i, t, 0 if i == 1 else 10000000))\n"
        "    f.flush(); t += 10000000; time.sleep(0.01)\n")
    if sys.platform == "win32":
        exe = tmp_path / "hookshot.cmd"
        exe.write_text('@"%s" "%s"\n' % (sys.executable, game))
    else:
        exe = tmp_path / "hookshot.sh"
        exe.write_text('#!/bin/sh\nexec "%s" "%s"\n' % (sys.executable, game))
        exe.chmod(0o755)
    out = tmp_path / "out"
    t0 = time.monotonic()
    rc = rb.main(["--exe", str(exe), "--build", "hookshot", "--scene", "kokiri-idle", "--title", "input",
                  "--out", str(out), "--duration", "2", "--warmup-s", "0"])
    assert rc == 0 and time.monotonic() - t0 < 20
    data = json.loads((out / "bench.json").read_text())
    assert data["schema_version"] == 5 and data["frames"] > 20
    assert data["fps_1pct_low"] == pytest.approx(100.0) and data["fps_0_1pct_low"] == pytest.approx(100.0)
    assert data["frame_time_ms"]["p50"] == 10.0 and data["killed_by_harness"]
    # The fake game's return_ns is not the perf_counter clock, so the offset falls back
    # to the time its first row was seen: a small positive number, not the trace clock.
    assert data["warmup_origin"] == "launch" and 0.0 <= data["launch_offset_s"] < 10.0
    assert any("trace clock is not the launch clock" in w for w in data["warnings"])


def test_fake_hookshot_repeat_writes_spread(tmp_path):
    game = tmp_path / "fake_game.py"
    game.write_text(
        "import os, time\n"
        "f = open(os.environ['HOS_PRESENT_TRACE'], 'w')\n"
        "f.write('frame,return_ns,interval_ns\\n')\n"
        "t = 0\n"
        "for i in range(1, 100000):\n"
        "    f.write('%d,%d,%d\\n' % (i, t, 0 if i == 1 else 10000000))\n"
        "    f.flush(); t += 10000000; time.sleep(0.01)\n")
    if sys.platform == "win32":
        exe = tmp_path / "hookshot.cmd"
        exe.write_text('@"%s" "%s"\n' % (sys.executable, game))
    else:
        exe = tmp_path / "hookshot.sh"
        exe.write_text('#!/bin/sh\nexec "%s" "%s"\n' % (sys.executable, game))
        exe.chmod(0o755)
    out = tmp_path / "out"
    rc = rb.main(["--exe", str(exe), "--build", "hookshot", "--scene", "kokiri-idle", "--title", "input",
                  "--out", str(out), "--duration", "2", "--repeat", "2", "--warmup-s", "0.5"])
    assert rc == 0
    for i in (1, 2):
        data = json.loads((out / f"run{i}" / "bench.json").read_text())
        assert data["frames"] > 5 and data["warmup_s"] == 0.5
    rep = json.loads((out / "repeat.json").read_text())
    assert rep["runs"] and len(rep["runs"]) == 2 and rep["spread_pct"] is not None


# ------------------------------------------------------ TICK-009: warm-up, offset, repeat
def test_warmup_defaults_per_build():
    assert rb.resolve_warmup(None, "hookshot") == 60.0
    assert rb.resolve_warmup(None, "lichii") == 0.0
    assert rb.resolve_warmup(0.0, "hookshot") == 0.0
    assert rb.resolve_warmup(45, "hookshot") == 45.0
    assert rb.build_parser().parse_args(["--exe", "x", "--scene", "s", "--out", "o"]).warmup_s is None


def test_skip_counts_from_launch_when_offset_known():
    # 60 s after launch, first present 12 s after launch -> measure from 48 s after first present
    assert rb.skip_from_first_present(60, "launch", 12.0) == pytest.approx(48.0)
    # a warm-up shorter than the offset discards nothing
    assert rb.skip_from_first_present(5, "launch", 12.0) == 0.0
    # no offset, or the old origin: the warm-up is counted from the first present
    assert rb.skip_from_first_present(45, "launch", None) == 45.0
    assert rb.skip_from_first_present(45, "first_present", 12.0) == 45.0
    assert rb.cut_label(60, "launch", 12.0) == "60 s after launch"
    assert rb.cut_label(45, "first_present", None) == "45 s after first present"
    assert rb.cut_label(0, "launch", 12.0) == "whole capture"


def test_resolve_offset_from_matching_clock(tmp_path):
    trace = tmp_path / "t.csv"
    launch_ns = 5_000_000_000_000
    trace.write_text("frame,return_ns,interval_ns\n1,%d,0\n2,%d,16000000\n"
                     % (launch_ns + 12_500_000_000, launch_ns + 12_516_000_000))
    off, note = rb.resolve_launch_offset(trace, launch_ns, None)
    assert off == pytest.approx(12.5) and note is None


def test_resolve_offset_falls_back_to_first_seen(tmp_path):
    trace = tmp_path / "t.csv"
    trace.write_text("frame,return_ns,interval_ns\n1,100,0\n2,116000000,16000000\n")  # other clock
    launch_ns = 5_000_000_000_000
    off, note = rb.resolve_launch_offset(trace, launch_ns, launch_ns + 7_000_000_000)
    assert off == pytest.approx(7.0) and "first trace row" in note
    off, note = rb.resolve_launch_offset(trace, launch_ns, None)
    assert off is None and "unknown" in note


def test_trace_has_row_and_first_return(tmp_path):
    trace = tmp_path / "t.csv"
    assert not rb.trace_has_row(trace)
    trace.write_text("frame,return_ns,interval_ns\n")
    assert not rb.trace_has_row(trace) and rb.hookshot_first_return_ns(trace) is None
    trace.write_text("frame,return_ns,interval_ns\n1,123456789,0\n2,")
    assert rb.trace_has_row(trace) and rb.hookshot_first_return_ns(trace) == 123456789


def test_fps_spread_limit():
    s = rb.fps_spread([60.0, 62.0, 61.0])
    assert s["spread_pct"] == pytest.approx(100 * 2 / 61) and s["within_limit"]
    s = rb.fps_spread([50.0, 60.0])
    assert s["spread_pct"] == pytest.approx(100 * 10 / 55) and not s["within_limit"]
    s = rb.fps_spread([0.0, 0.0])
    assert s["spread_pct"] is None and not s["within_limit"]
    assert rb.fps_spread([58.0])["spread_pct"] is None


def _synthetic_trace(path: Path, seconds: int = 60, step_ns: int = 10_000_000) -> None:
    """Frames every 10 ms (100 fps) from a first present at a fixed return_ns."""
    base = 1_000_000_000_000
    lines = ["frame,return_ns,interval_ns"]
    n = seconds * 100
    for i in range(1, n + 1):
        lines.append("%d,%d,%d" % (i, base + (i - 1) * step_ns, 0 if i == 1 else step_ns))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_summarize_warmup_from_launch_and_override(tmp_path, capsys):
    import summarize as sm
    d = tmp_path / "hookshot-x"
    d.mkdir()
    _synthetic_trace(d / "present_trace.csv")  # first present at t=0, 60 s of frames
    (d / "bench.json").write_text(json.dumps({
        "build": "hookshot", "scene": "kokiri-walk", "warmup_s": 45.0,
        "warmup_origin": "launch", "launch_offset_s": 5.0}))
    # from launch: 45 s after launch = 40 s after first present -> frames at t>=40 s: 2000
    assert sm.main([str(d)]) == 0
    row = [ln for ln in capsys.readouterr().out.splitlines() if "hookshot-x" in ln][0]
    assert "45 s after launch" in row and "| 2000 |" in row and "**100.0**" in row
    # override: 45 s after first present -> 1500 frames
    assert sm.main(["--warmup-s", "45", "--origin", "first_present", str(d)]) == 0
    row = [ln for ln in capsys.readouterr().out.splitlines() if "hookshot-x" in ln][0]
    assert "45 s after first present" in row and "| 1500 |" in row


def test_summarize_old_layout_without_bench_json(tmp_path, capsys):
    import summarize as sm
    d = tmp_path / "lichii-y"
    d.mkdir()
    (d / "presentmon.csv").write_bytes((DATA / "presentmon_v2.csv").read_bytes())
    assert sm.main([str(d)]) == 0
    out = capsys.readouterr().out
    assert "Lichii" in out and "whole capture" in out and "| 3 |" in out


def test_summarize_rejects_empty_dir(tmp_path):
    import summarize as sm
    d = tmp_path / "empty"
    d.mkdir()
    with pytest.raises(SystemExit):
        sm.summarize_dir(d, None)


# ------------------------------------------------------ TICK-013: low fps and budget shares
def _frames(ms_list: list[float]) -> list[float]:
    return sorted(ms_list)


def test_low_fps_is_mean_of_slowest_share():
    # 1000 frames: 990 at 10 ms, 10 at 50 ms -> slowest 1% (10 frames) mean 50 ms -> 20 fps
    vals = _frames([10.0] * 990 + [50.0] * 10)
    assert rb.low_fps(vals, 0.01) == pytest.approx(20.0)
    # slowest 0.1% = 1 frame (50 ms) -> 20 fps
    assert rb.low_fps(vals, 0.001) == pytest.approx(20.0)
    # 1000 frames, one 100 ms frame among 10 ms: 0.1% low = 10 fps, 1% low (10 frames) mean 19 ms
    vals = _frames([10.0] * 999 + [100.0])
    assert rb.low_fps(vals, 0.001) == pytest.approx(10.0)
    assert rb.low_fps(vals, 0.01) == pytest.approx(1000.0 / ((100.0 + 9 * 10.0) / 10))


def test_low_fps_short_run_uses_at_least_one_frame():
    # 100 frames: 1% = 1 frame, 0.1% = 1 frame (the worst one)
    vals = _frames([10.0] * 99 + [40.0])
    assert rb.low_fps(vals, 0.01) == pytest.approx(25.0)
    assert rb.low_fps(vals, 0.001) == pytest.approx(25.0)
    assert rb.low_fps([], 0.01) == 0.0


def test_low_fps_is_ceiling_of_share():
    # 250 frames: 1% = ceil(2.5) = 3 frames
    vals = _frames([10.0] * 247 + [20.0, 30.0, 40.0])
    assert rb.low_fps(vals, 0.01) == pytest.approx(1000.0 / 30.0)


def test_over_budget_shares_120_and_144_and_60():
    vals = _frames([6.0, 7.0, 8.0, 8.5])
    assert rb.over_budget_pct(vals, rb.FRAME_BUDGET_120HZ_MS) == pytest.approx(25.0)  # only 8.5
    assert rb.over_budget_pct(vals, rb.FRAME_BUDGET_144HZ_MS) == pytest.approx(75.0)  # 7.0, 8.0, 8.5
    assert rb.over_budget_pct(vals, rb.FRAME_BUDGET_60HZ_MS) == 0.0
    # strictly above: a frame exactly on the budget is not over it
    assert rb.over_budget_pct([8.33], 8.33) == 0.0
    assert rb.over_budget_pct([], 8.33) == 0.0


def test_compute_stats_new_keys_on_synthetic_trace():
    # 120 Hz-ish capture: 90 frames at 8.0 ms, 10 frames at 12 ms
    s = rb.compute_stats([8.0] * 90 + [12.0] * 10)
    assert s["frames"] == 100
    assert s["over_8_33_ms_pct"] == 10.0          # the 12 ms frames only
    assert s["over_6_94_ms_pct"] == 100.0        # every frame is above 6.94 ms
    assert s["over_16_7_ms_pct"] == 0.0
    assert s["fps_1pct_low"] == pytest.approx(1000.0 / 12.0)
    assert set(rb.compute_stats([])) >= {"fps_1pct_low", "fps_0_1pct_low", "over_8_33_ms_pct", "over_6_94_ms_pct"}


def _write_trace(path: Path, intervals_ms: list[float]) -> None:
    """Hookshot-format trace: frame 1 (interval 0), then one row per interval in intervals_ms."""
    lines = ["frame,return_ns,interval_ns", "1,1000000000000,0"]
    t = 1_000_000_000_000
    for i, ms in enumerate(intervals_ms, start=2):
        t += int(round(ms * 1e6))
        lines.append("%d,%d,%d" % (i, t, int(round(ms * 1e6))))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_summarize_new_columns_on_synthetic_traces(tmp_path, capsys):
    import summarize as sm
    d = tmp_path / "hookshot-120"
    d.mkdir()
    # 100 frames: 99 at 8.0 ms and one at 40 ms (frame 1 has no interval and is skipped)
    _write_trace(d / "present_trace.csv", [8.0] * 98 + [40.0])
    (d / "bench.json").write_text(json.dumps({"build": "hookshot", "scene": "kokiri-idle",
                                              "schema_version": 3, "warmup_s": 0.0}))
    assert sm.main([str(d)]) == 0
    out = capsys.readouterr().out
    header = [ln for ln in out.splitlines() if ln.startswith("| Build")][0]
    for col in ("1% low", "0.1% low", "> 8.33 ms", "> 6.94 ms", "> 16.7 ms"):
        assert col in header
    row = [ln for ln in out.splitlines() if "hookshot-120" in ln][0]
    # 8.0 ms frames are inside the 120 Hz budget but outside the 144 Hz one; the 40 ms frame is over all
    assert "| 1.0% | 100.0% | 1% |" in row
    # 1% low and 0.1% low: the single 40 ms frame -> 25.0 fps
    assert "| 25.0 | 25.0 |" in row


# ------------------------------------------------------ the saves the run guards (Linux: the data folder)
@pytest.mark.skipif(sys.platform == "win32", reason="the Linux data folder")
def test_save_dirs_are_the_games(tmp_path, monkeypatch):
    exe = tmp_path / "game" / "hookshot"
    # (host/hos_main.c saves_dir: $XDG_DATA_HOME/heylisten when it is absolute, else ~/.local/share/heylisten)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    dirs = rb.save_dirs(exe)
    assert dirs[:2] == [tmp_path / "game" / "user" / "save", tmp_path / "user" / "save"]
    assert tmp_path / "xdg" / "heylisten" / "save" in dirs
    assert tmp_path / "xdg" / "hookshot" / "save" in dirs   # (builds before the HeyListen names)
    monkeypatch.setenv("XDG_DATA_HOME", "relative/data")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    dirs = rb.save_dirs(exe)
    assert tmp_path / "home" / ".local" / "share" / "heylisten" / "save" in dirs
    assert not any("relative" in str(d) for d in dirs)


@pytest.mark.skipif(sys.platform == "win32", reason="the Linux data folder")
def test_run_puts_every_save_folder_back(tmp_path):
    """A game that overwrites a save in the data folder and one beside it, and adds one: all as before after the run."""
    data_save = tmp_path / "xdg" / "heylisten" / "save" / "0100"
    data_save.mkdir(parents=True)
    (data_save / "slot0").write_bytes(b"the user's own save")
    game_dir = tmp_path / "game"
    beside = game_dir / "user" / "save"
    beside.mkdir(parents=True)
    (beside / "old").write_text("kept beside", encoding="utf-8")
    game = tmp_path / "fake_game.py"
    game.write_text(
        "import os, time\n"
        "d = os.path.join(os.environ['XDG_DATA_HOME'], 'heylisten', 'save', '0100')\n"
        "open(os.path.join(d, 'slot0'), 'wb').write(b'autosaved over')\n"
        "open(os.path.join(d, 'slot1'), 'wb').write(b'new')\n"
        "open(os.path.join('user', 'save', 'old'), 'w').write('changed')\n"
        "f = open(os.environ['HOS_PRESENT_TRACE'], 'w')\n"
        "f.write('frame,return_ns,interval_ns\\n')\n"
        "for i in range(1, 100000):\n"
        "    f.write('%d,%d,%d\\n' % (i, i * 10000000, 0 if i == 1 else 10000000)); f.flush(); time.sleep(0.01)\n")
    exe = game_dir / "hookshot.sh"
    exe.write_text('#!/bin/sh\nexec "%s" "%s"\n' % (sys.executable, game))
    exe.chmod(0o755)
    out = tmp_path / "out"
    assert rb.main(["--exe", str(exe), "--build", "hookshot", "--scene", "kokiri-idle", "--title", "input",
                    "--out", str(out), "--duration", "2", "--warmup-s", "0"]) == 0
    assert (data_save / "slot0").read_bytes() == b"the user's own save"
    assert not (data_save / "slot1").exists()
    assert (beside / "old").read_text(encoding="utf-8") == "kept beside"
    assert sorted(p.name for p in (out / "save-guard").iterdir()) == ["0", "0.txt", "1", "1.txt"]


# ------------------------------------------------------ the machine: environment and GPU busy (Linux)
def _fake_sys(root: Path) -> Path:
    for cpu, gov in (("cpu0", "performance"), ("cpu1", "performance"), ("cpu2", "powersave")):
        (root / "devices/system/cpu" / cpu / "cpufreq").mkdir(parents=True)
        (root / "devices/system/cpu" / cpu / "cpufreq/scaling_governor").write_text(gov + "\n")
    thp = root / "kernel/mm/transparent_hugepage"
    thp.mkdir(parents=True)
    (thp / "enabled").write_text("always [madvise] never\n")
    (thp / "shmem_enabled").write_text("always within_size [advise] never deny force\n")
    (root / "module/udmabuf/parameters").mkdir(parents=True)
    (root / "module/udmabuf/parameters/size_limit_mb").write_text("64\n")
    # two cards: an amdgpu one (with a connector beside it) and another driver's; drivers and devices are links, as in sysfs
    for name, pci, driver in (("card1", "0000:0b:00.0", "amdgpu"), ("card0", "0000:01:00.0", "i915")):
        dev = root / "devices/pci" / pci
        (dev).mkdir(parents=True)
        (root / "bus/drivers" / driver).mkdir(parents=True, exist_ok=True)
        (dev / "driver").symlink_to(root / "bus/drivers" / driver)
        (dev / "gpu_busy_percent").write_text("37\n")
        (root / "class/drm" / name).mkdir(parents=True)
        (root / "class/drm" / name / "device").symlink_to(dev)
    (root / "class/drm/card1-DP-1").mkdir()
    (root / "class/drm/card1-DP-1/device").symlink_to(root / "class/drm/card1")
    return root


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux sysfs")
def test_linux_environment_from_sysfs(tmp_path):
    sysr = _fake_sys(tmp_path / "sys")
    proc = tmp_path / "proc"
    (proc / "sys/kernel").mkdir(parents=True)
    (proc / "loadavg").write_text("1.50 0.75 0.25 2/900 12345\n")
    (proc / "sys/kernel/osrelease").write_text("6.17.1-test\n")
    env = rb.linux_environment(sysr, proc, mesa=lambda: "Mesa 25.2.4")
    assert env == {"cpu_governor": "performance,powersave", "thp_enabled": "madvise", "thp_shmem_enabled": "advise",
                   "udmabuf_size_limit_mb": 64, "loadavg_start": [1.5, 0.75, 0.25], "kernel": "6.17.1-test",
                   "mesa": "Mesa 25.2.4"}
    # (udmabuf not loaded, no THP, no cpufreq: unknown, not an error)
    empty = rb.linux_environment(tmp_path / "nosys", tmp_path / "noproc", mesa=lambda: None)
    assert empty == {"cpu_governor": None, "thp_enabled": None, "thp_shmem_enabled": None, "udmabuf_size_limit_mb": None,
                     "loadavg_start": None, "kernel": None, "mesa": None}


def test_mesa_version_from_vulkaninfo(monkeypatch):
    summary = ("Devices:\n========\nGPU0:\n\tdeviceName = AMD Radeon RX 9070 XT (RADV GFX1201)\n\tdriverName = radv\n"
               "\tdriverInfo = Mesa 25.2.4-arch1.1\nGPU1:\n\tdeviceName = llvmpipe (LLVM 20.1.8, 256 bits)\n"
               "\tdriverName = llvmpipe\n\tdriverInfo = Mesa 25.2.4-arch1.1 (LLVM 20.1.8)\n")
    monkeypatch.setattr(rb.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(rb.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, summary, ""))
    assert MESA_VERSION() == "Mesa 25.2.4-arch1.1"
    monkeypatch.setattr(rb.shutil, "which", lambda name: None)
    assert MESA_VERSION() is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux sysfs")
def test_gpu_busy_reads_the_amdgpu_card_only(tmp_path):
    sysr = _fake_sys(tmp_path / "sys")
    cards = AMDGPU_CARDS(sysr)
    assert [c[0] for c in cards] == ["0000:0b:00.0"]
    busy = rb.GpuBusy(cards)
    f = cards[0][1]
    for t, v in ((0.5, 99), (10.0, 20), (10.2, 40), (10.4, 60)):
        f.write_text(f"{v}\n")
        busy.sample(t)
    s = busy.summary(10.0)   # (the warm-up's samples left out)
    assert s["source"] == "amdgpu gpu_busy_percent" and s["from_s_after_launch"] == 10.0
    assert s["cards"] == [{"pci": "0000:0b:00.0", "samples": 3, "mean_pct": 40.0, "p50_pct": 40.0, "p95_pct": 58.0}]
    busy.write_csv(tmp_path / "gpu_busy.csv")
    assert (tmp_path / "gpu_busy.csv").read_text().splitlines()[:2] == ["t_s,pci,busy_pct", "0.500,0000:0b:00.0,99"]
    assert rb.GpuBusy([]).summary(0.0) is None


def test_summarize_environment_table(tmp_path, capsys):
    import summarize as sm
    d = tmp_path / "hookshot-linux"
    d.mkdir()
    _write_trace(d / "present_trace.csv", [8.0] * 50)
    (d / "bench.json").write_text(json.dumps({
        "build": "hookshot", "scene": "kokiri-idle", "schema_version": 4, "warmup_s": 0.0,
        "environment": {"cpu_governor": "performance", "thp_enabled": "always", "thp_shmem_enabled": "advise",
                        "udmabuf_size_limit_mb": 2147483647, "loadavg_start": [1.5, 0.75, 0.25],
                        "kernel": "6.17.1", "mesa": "Mesa 25.2.4"},
        "gpu_busy": {"source": "amdgpu gpu_busy_percent", "from_s_after_launch": 0.0,
                     "cards": [{"pci": "0000:0b:00.0", "samples": 3, "mean_pct": 40.0, "p50_pct": 40.0, "p95_pct": 58.0}]}}))
    old = tmp_path / "hookshot-old"
    old.mkdir()
    _write_trace(old / "present_trace.csv", [8.0] * 50)
    assert sm.main([str(d), str(old)]) == 0
    out = capsys.readouterr().out
    assert "| Dir | Governor | THP |" in out
    assert ("| hookshot-linux | performance | always | advise | 2147483647 | 1.50 0.75 0.25 | 6.17.1 | Mesa 25.2.4 "
            "| 40% / 58% |") in out
    assert "| hookshot-old | - | - | - | - | - | - | - | - |" in out
    # (no directory records the machine: the table alone, as before)
    assert sm.main([str(old)]) == 0
    assert "Governor" not in capsys.readouterr().out


# ------------------------------------------------------ the title: the game goes through it, the window starts at play
def _fake_game(tmp_path, play_after_s, wait_line=None):
    """A fake game writing a 100 fps trace on the harness's own clock; after play_after_s seconds it logs that play began
    (as host/hos_main.c does), or, with None, logs wait_line (the title helper giving up) and plays on in the title."""
    say = ("'[hos host] play: began at clock %d ns, 1.00 s after the process started' % now"
           if play_after_s is not None else repr("[hos host] " + wait_line))
    game = tmp_path / "fake_game.py"
    game.write_text(
        "import os, time\n"
        "f = open(os.environ['HOS_PRESENT_TRACE'], 'w')\n"
        "f.write('frame,return_ns,interval_ns\\n')\n"
        "assert os.environ.get('HL_SKIP_TITLE') == '1' and os.environ.get('HOS_INPUT_FROM_PLAY') == '1'\n"
        "t0 = time.perf_counter_ns(); logged = False\n"
        "for i in range(1, 100000):\n"
        "    now = time.perf_counter_ns()\n"
        "    f.write('%d,%d,%d\\n' % (i, now, 0 if i == 1 else 10000000)); f.flush()\n"
        "    if not logged and now - t0 > " + str(int((play_after_s or 0.3) * 1e9)) + ":\n"
        "        logged = True\n"
        "        print(" + say + ", flush=True)\n"
        "    time.sleep(0.01)\n")
    exe = tmp_path / ("hookshot.cmd" if sys.platform == "win32" else "hookshot.sh")
    if sys.platform == "win32":
        exe.write_text('@"%s" "%s"\n' % (sys.executable, game))
    else:
        exe.write_text('#!/bin/sh\nexec "%s" "%s"\n' % (sys.executable, game))
        exe.chmod(0o755)
    return exe


def test_window_starts_at_play(tmp_path):
    exe = _fake_game(tmp_path, 1.0)
    out = tmp_path / "out"
    rc = rb.main(["--exe", str(exe), "--build", "hookshot", "--scene", "kokiri-idle", "--out", str(out), "--duration", "3.5"])
    assert rc == 0
    data = json.loads((out / "bench.json").read_text())
    assert data["title"] == "skip" and data["warmup_origin"] == "play" and data["status"] == "ok"
    assert 0.8 < data["play_at_s"] < 2.0
    # the window starts where play began, counted from the first present
    assert data["window_start_s"] == pytest.approx(data["play_at_s"] - data["launch_offset_s"], abs=0.01)
    assert 100 < data["frames"] < 260   # (3.5 s of run minus about 1 s before play, at about 100 fps)


def test_a_run_that_never_reaches_play_is_failed_not_measured(tmp_path):
    exe = _fake_game(tmp_path, None, "skip title: no saved game yet: the title screen stays")
    out = tmp_path / "out"
    t0 = time.monotonic()
    rc = rb.main(["--exe", str(exe), "--build", "hookshot", "--scene", "castle-move", "--out", str(out), "--duration", "20"])
    assert rc == 2 and time.monotonic() - t0 < 12   # (it did not wait for the whole duration)
    data = json.loads((out / "bench.json").read_text())
    assert data["status"] == "failed" and data["frames"] == 0 and data["play_at_s"] is None
    assert "never reached play" in data["failure"]


def test_scene_scripts_in_play_and_the_old_ones():
    for name in ("kokiri-idle", "castle-move", "hyrule-walk"):
        assert name in rb.SKIP_SCENES and name in rb.HOOKSHOT_SCENES
        assert "A," not in rb.SKIP_SCENES[name] and ":A" not in rb.SKIP_SCENES[name]   # (no title presses)
    assert rb.SKIP_SCENES["kokiri-idle"] == ""
    assert rb.SKIP_SCENES["castle-move"].startswith("10:LUP+RLEFT/700,11:RLEFT/1900,")
    assert rb.HOOKSHOT_SCENES["castle-move"].startswith(rb.LOAD_SAVE)   # (the old scenes are unchanged)


def test_title_and_origin_defaults():
    parse = rb.build_parser().parse_args
    base = ["--exe", "x", "--scene", "castle-move", "--out", "o"]
    a = parse(base)
    assert rb.title_mode(a) == "skip" and rb.resolve_origin(a, "skip") == "play"
    a = parse(base + ["--title", "input"])
    assert rb.title_mode(a) == "input" and rb.resolve_origin(a, "input") == "launch"
    a = parse(base + ["--input-script", "300:A"])   # (an explicit script keeps the old clock and the old window)
    assert rb.title_mode(a) == "input" and rb.resolve_origin(a, "input") == "launch"
    a = parse(base + ["--warmup-origin", "launch"])
    assert rb.resolve_origin(a, "skip") == "launch"


def test_window_start_and_labels():
    assert rb.window_start_s("play", 0.0, 12.0, 31.5) == pytest.approx(19.5)
    assert rb.window_start_s("play", 2.0, 12.0, 31.5) == pytest.approx(21.5)
    assert rb.window_start_s("play", 0.0, 12.0, None) is None
    assert rb.window_start_s("launch", 60, 12.0, 31.5) == pytest.approx(48.0)
    assert rb.cut_label(0, "play", 12.0) == "from play"


def test_play_line_parsed_on_the_harness_clock(tmp_path):
    log = tmp_path / "game_log.txt"
    log.write_text("[hos host] x\n[hos main] play: began at clock 105500000000 ns, 31.40 s after the process started\n")
    assert rb.parse_play_line(log, 100_000_000_000) == (pytest.approx(5.5), None)
    at, note = rb.parse_play_line(log, 900_000_000_000)   # (another clock: the line's own seconds)
    assert at == pytest.approx(31.4) and note
    assert rb.parse_play_line(tmp_path / "none.txt", 1) == (None, None)


def test_summarize_starts_at_play(tmp_path, capsys):
    import summarize as sm
    d = tmp_path / "run"
    d.mkdir()
    (d / "present_trace.csv").write_text("frame,return_ns,interval_ns\n" + "".join(
        "%d,%d,%d\n" % (i, i * 10_000_000, 0 if i == 1 else 10_000_000) for i in range(1, 1001)))
    (d / "bench.json").write_text(json.dumps({
        "build": "hookshot", "scene": "castle-move", "warmup_s": 0.0, "warmup_origin": "play",
        "launch_offset_s": 1.0, "play_at_s": 6.0, "window_start_s": 5.0, "status": "ok"}))
    assert sm.summarize_dir(d, None)["frames"] == 500   # (the last 5 s of 10 at 100 fps)
    (d / "bench.json").write_text(json.dumps({
        "build": "hookshot", "scene": "castle-move", "warmup_s": 0.0, "warmup_origin": "play",
        "launch_offset_s": 1.0, "play_at_s": None, "window_start_s": None, "status": "failed"}))
    assert sm.summarize_dir(d, None)["frames"] == 0
