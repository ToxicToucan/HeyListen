# Copyright (C) 2026 Sacrappy

"""A romfs folder without the game's heap sizes is told at boot, not crashed on (host/hos_main.c romfs_notice, issue 55).

The game sizes the heap of each module from System/HeapSize/Product.Nin_Ounce_NVN2.HeapDef.byml.zs. Without it the third
module has no heap and the game wrote through a null pointer ("exception 0xc0000005 (write 0x100000000000, guest 0)" in the
guest's memset, since v0.4.1, on several PCs). romfs_boot_file_present and romfs_notice are cut into a fixture with
GetFileAttributesA, hos_log and MessageBoxA as spies: the file there (compressed or plain) lets the game start, absent or empty
it does not (a log line and one message), HOS_ROMFS_CHECK=0 and HOS_HEADLESS keep their meaning. The source is checked for the
call at boot.

    python -I -B tests/test_romfs_boot_notice.py [--cc gcc]
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def block(text, marker):
    if text.count(marker) != 1:
        raise SystemExit("marker not found once: " + marker)
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


FIXTURE = r"""
#define _GNU_SOURCE
#define _CRT_SECURE_NO_WARNINGS
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#define MAX_PATH 4096
#define SEP_S "/"
typedef unsigned DWORD;
#define INVALID_FILE_ATTRIBUTES 0xFFFFFFFFu
#define FILE_ATTRIBUTE_DIRECTORY 0x10
#define MB_OK 0
#define MB_ICONERROR 0x10
#define MB_SETFOREGROUND 0x10000
static int boxes, logs;
static void hos_log(const char *fmt, ...) { (void)fmt; logs++; }
static int MessageBoxA(void *w, const char *text, const char *caption, unsigned type) {
    (void)w, (void)caption, (void)type;
    if (!strstr(text, "HeapDef")) printf("FAIL: the message does not name the file\n"), exit(1);
    return ++boxes;
}
static DWORD GetFileAttributesA(const char *p) {
    struct stat st;
    if (stat(p, &st)) return INVALID_FILE_ATTRIBUTES;
    return (st.st_mode & S_IFDIR) ? FILE_ATTRIBUTE_DIRECTORY : 0x80;
}
int hos_sdl_load(void) { return 0; }
@@CODE@@
int main(int argc, char **argv) {
    /* romfs_notice(root, quiet) must return argv[3], and make argv[4] message boxes */
    const char *root = argc > 1 && strcmp(argv[1], "-") ? argv[1] : NULL;
    const int got = romfs_notice(root, argc > 2 && atoi(argv[2]));
    printf("returned %d, boxes %d\n", got, boxes);
    return got == atoi(argv[3]) && boxes == atoi(argv[4]) ? 0 : 1;
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="gcc")
    args = parser.parse_args()
    src = (ROOT / "host" / "hos_main.c").read_text(encoding="utf-8")
    boot = src.index('hos_log("boot: image %zu MB')
    call = src.find("romfs_notice(hos_romfs_dir", boot)
    if call < 0 or call > src.index("hos_mods_init(image);", boot):
        raise SystemExit("FAIL: the game does not check its romfs at boot")
    if "HOS_ROMFS_CHECK" not in src:
        raise SystemExit("FAIL: no off switch")
    defines = "\n".join(line for line in src.splitlines() if line.startswith(("#define HOS_ROMFS_SHOWN", "#define HOS_ROMFS_BOOT_FILE")))
    code = "\n".join((defines, block(src, "static int romfs_file_ok("), block(src, "static int romfs_boot_file_present("),
                      block(src, "static int romfs_notice(")))
    with tempfile.TemporaryDirectory(prefix="romfs-notice-") as tmp:
        c, exe = Path(tmp) / "fixture.c", Path(tmp) / ("fixture.exe" if __import__("os").name == "nt" else "fixture")
        c.write_text(FIXTURE.replace("@@CODE@@", code), encoding="utf-8")
        r = subprocess.run([args.cc, "-std=gnu11", "-O1", "-Wno-unused-function", str(c), "-o", str(exe)], capture_output=True,
                           text=True, timeout=120)
        if r.returncode:
            print(r.stdout + r.stderr)
            raise SystemExit("the fixture did not build")
        romfs = Path(tmp) / "romfs"
        heap = romfs / "System" / "HeapSize"
        zs = heap / "Product.Nin_Ounce_NVN2.HeapDef.byml.zs"
        plain = heap / "Product.Nin_Ounce_NVN2.HeapDef.byml"
        romfs.mkdir()

        def run(label, root, quiet, want, boxes, **env):
            e = {k: v for k, v in __import__("os").environ.items() if not k.startswith("HOS_")}
            e.update(env)
            r = subprocess.run([str(exe), root, str(quiet), str(want), str(boxes)], capture_output=True, text=True, timeout=30, env=e)
            print("%-28s %s" % (label, r.stdout.strip()))
            if r.returncode:
                raise SystemExit("FAIL: " + label)

        run("no file", str(romfs), 0, 0, 1)
        run("no file, builder's run", str(romfs), 1, 0, 0)
        run("no file, HOS_HEADLESS", str(romfs), 0, 0, 0, HOS_HEADLESS="1")
        run("no file, HOS_ROMFS_CHECK=0", str(romfs), 0, 1, 0, HOS_ROMFS_CHECK="0")
        run("no romfs folder given", "-", 0, 1, 0)
        heap.mkdir(parents=True)
        zs.write_bytes(b"")
        run("empty file", str(romfs), 0, 0, 1)
        zs.write_bytes(b"(\xb5/\xfd")
        run("compressed file there", str(romfs), 0, 1, 0)
        zs.unlink()
        plain.write_bytes(b"YB")
        run("plain file there", str(romfs), 0, 1, 0)
    print("PASS: a romfs folder without the heap-size file is refused with a message; HOS_ROMFS_CHECK=0 and HOS_HEADLESS keep their meaning")


if __name__ == "__main__":
    main()
