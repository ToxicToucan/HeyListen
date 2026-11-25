"""QueryThreadCycleTime on Linux: a thread's CPU time in nanoseconds (issue 133: the stall watch in host/hos_jobtrace.c
called it and the Linux link failed with an undefined symbol). Run python -I -B tests/test_posix_thread_cycle_time.py.

Builds host/posix/win32_posix.c with a small program: a thread that spins for 200 ms must show 50-400 ms of CPU time, and a
bad handle must fail without writing the value. Linux only."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

RUN_ALL_ARGS = ["--cc", "cc"]
RUN_ALL_SKIP = {"windows": "tests the Linux implementation in host/posix"}
ROOT = Path(__file__).resolve().parents[1]

PROGRAM = r"""
#include <windows.h>
#include <stdio.h>
static volatile int stop;
static DWORD WINAPI spin(LPVOID p) { (void)p; volatile unsigned long x = 0; while (!stop) x++; return 0; }
int main(void) {
    HANDLE h = CreateThread(NULL, 0, spin, NULL, 0, NULL);
    ULONG64 a = 0, b = 0;
    if (!h || !QueryThreadCycleTime(h, &a)) { puts("FAIL: first query"); return 1; }
    Sleep(200);
    if (!QueryThreadCycleTime(h, &b)) { puts("FAIL: second query"); return 1; }
    stop = 1;
    WaitForSingleObject(h, INFINITE);
    printf("cpu ns over 200 ms of spinning: %llu\n", (unsigned long long)(b - a));
    if (b <= a || b - a < 50000000ULL || b - a > 400000000ULL) { puts("FAIL: not about 200 ms of CPU in ns"); return 1; }
    ULONG64 c = 7;
    if (QueryThreadCycleTime(NULL, &c) || c != 7) { puts("FAIL: a bad handle must fail and leave the value"); return 1; }
    puts("OK");
    return 0;
}
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cc", default="cc")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as d:
        src, exe = Path(d) / "t.c", Path(d) / "t"
        src.write_text(PROGRAM)
        cmd = [args.cc, "-O1", "-I", str(ROOT / "host" / "posix"), "-I", str(ROOT / "host"), "-I", str(ROOT),
               str(ROOT / "host" / "posix" / "win32_posix.c"), str(src), "-o", str(exe), "-lpthread"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            print(r.stdout + r.stderr)
            print("FAILED: the program did not build")
            return 1
        r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
        print(r.stdout.strip())
        return r.returncode


if __name__ == "__main__":
    sys.exit(main())
