"""The performance profile's CPU sampler thread is gone before the process ends (gpu/vk/profile_run.cpp, perf_report.cpp).

The sampler was a file-scope std::thread. Closing the window calls ExitProcess, which runs the renderer DLL's static
destructors with the sampler thread still joinable: ~thread calls std::terminate and the player's log ends with
"display: window closed, exiting" then "libc++abi: terminating" (v0.4.3, report 148766). Now perf_report_final stops
and joins the sampler (profile_stop) and the file-scope holder detaches a thread that is somehow still joinable.

python -I -B tests/test_profile_exit.py
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def main():
    run = (ROOT / "gpu" / "vk" / "profile_run.cpp").read_text(encoding="utf-8")
    if re.search(r"^std::thread\s+sampler\s*;", run, re.M):
        raise SystemExit("FAIL: the sampler is a bare file-scope std::thread again: its destructor terminates the process")
    m = re.search(r"struct SamplerThread\s*\{(.*?)\n\};", run, re.S)
    if not m or "joinable()" not in m.group(1) or ".detach()" not in m.group(1) or "~SamplerThread" not in m.group(1):
        raise SystemExit("FAIL: the sampler's holder does not detach a joinable thread in its destructor")
    stop = run[run.index("void profile_stop() {"):]
    stop = stop[:stop.index("\n}\n")]
    if "sampler.join()" not in stop:
        raise SystemExit("FAIL: profile_stop does not join the sampler")
    rep = (ROOT / "gpu" / "vk" / "perf_report.cpp").read_text(encoding="utf-8")
    fin = rep[rep.index("void perf_report_final(int event) {"):]
    fin = fin[:fin.index("\n}\n")]
    if "profile_stop();" not in fin or fin.index("profile_queue(true)") > fin.index("profile_stop();"):
        raise SystemExit("FAIL: perf_report_final does not stop the sampler after the profile's last batch")
    if "std::lock_guard lk(mu)" in fin or ("lk.unlock()" in fin and fin.index("lk.unlock()") > fin.index("profile_stop();")):
        raise SystemExit("FAIL: perf_report_final joins the sampler with the report's lock held")
    print("PASS: the profile sampler is stopped before the process ends, and a joinable one is detached, never terminated")


if __name__ == "__main__":
    main()
