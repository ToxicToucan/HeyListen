# Copyright (C) 2026 Sacrappy

"""The log line's format (host/hos_thread.c log_format): "[hos <thread>] <message>" as always, and with HOS_LOG_QPC=1 the
same line ending in " @qpc <ticks>", the QueryPerformanceCounter value PresentMon's TimeInQPC column counts in, so that a
line can be put on the frames of a capture. Also checks that hos_log takes the stamp before it formats the message (the
time the line was logged, not the time a long format ended), and that the vcore escape line names how long each holder
has held its core.

python -I -B tests/test_log_qpc.py [--cxx clang++]
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def block(text, marker):
    start = text.index(marker)
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cxx', default='g++')
    parser.add_argument('--source', type=Path, default=ROOT / 'host/hos_thread.c')
    args = parser.parse_args()
    text = args.source.read_text(encoding='utf-8')
    fmt = block(text, 'static int log_format(')
    log = block(text, 'void hos_log(const char *fmt')
    if log.index('log_qpc_now()') > log.index('vsnprintf('):
        raise SystemExit('FAIL: hos_log reads the clock after formatting the message')
    if 'log_format(line, sizeof line' not in log:
        raise SystemExit('FAIL: hos_log does not format its line with log_format')
    acquire = block(text, 'static void vc_acquire(')
    if 'held %.0f ms' not in acquire or 'vc_took' not in acquire:
        raise SystemExit('FAIL: the escape line does not say how long each holder has held its core')
    where = block(text, 'static void vc_where(')
    if 'vc_where(h, at' not in acquire or where.index('ResumeThread') > where.index('GetModuleHandleExA'):
        raise SystemExit('FAIL: the escape line does not say where a long holder is, or calls out while it is stopped')
    if 't->vc_took = hos_now_ns();' not in block(text, 'static void vc_take('):
        raise SystemExit('FAIL: vc_take does not note when the core was taken')
    fixture = r'''
#include <cstdio>
#include <cstdlib>
#include <cstring>
#define CHECK(ok) do { if (!(ok)) { std::fprintf(stderr, "failed line %d\n", __LINE__); std::exit(1); } } while (0)
''' + fmt + r'''
int main() {
    char line[1152];
    int n = log_format(line, sizeof line, "host", "vk: status: 600 frames", -1);
    CHECK(!std::strcmp(line, "[hos host] vk: status: 600 frames\n") && n == (int)std::strlen(line));
    log_format(line, sizeof line, "sead::MainThread", "hid: the game read no controller for 2.0 s", 3550541980881LL);
    CHECK(!std::strcmp(line, "[hos sead::MainThread] hid: the game read no controller for 2.0 s @qpc 3550541980881\n"));
    log_format(line, sizeof line, "host", "x", 0);   // (0 is a time like any other: stamped)
    CHECK(!std::strcmp(line, "[hos host] x @qpc 0\n"));
    // the longest message hos_log formats (1023 characters) and a 47-character thread name still end in the stamp and a newline
    char msg[1024], name[48];
    std::memset(msg, 'm', sizeof msg - 1), msg[sizeof msg - 1] = 0;
    std::memset(name, 'n', sizeof name - 1), name[sizeof name - 1] = 0;
    n = log_format(line, sizeof line, name, msg, 9223372036854775807LL);
    CHECK(n < (int)sizeof line);
    CHECK(!std::strcmp(line + n - 26, " @qpc 9223372036854775807\n"));
    std::puts("PASS: log lines as before, and with the QPC stamp at their end when asked");
}
'''
    with tempfile.TemporaryDirectory(prefix='log-qpc-') as tmp:
        cpp = Path(tmp) / 'fixture.cpp'
        exe = Path(tmp) / 'fixture.exe'
        cpp.write_text(fixture, encoding='utf-8')
        subprocess.run([args.cxx, '-std=c++20', '-Wall', '-Wextra', '-Werror', str(cpp), '-o', str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    main()
