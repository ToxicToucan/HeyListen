# Copyright (C) 2026 Sacrappy

"""HL_STALL_WATCH=<ms> (host/hos_jobtrace.c, STALL1 22 Nov 2026), a diagnostic: the job trace's records kept in a ring all the time,
and a watcher thread that dumps the records of every gap between presents of <ms> or more, with each thread's CPU time, where it
was running, the process's page faults and its working-set change. No game here: the wiring in the source and the dump's
reader (tools/bench/jobtrace.py reads the same format).

    python -I -B tests/test_stall_watch.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check(ok, what):
    if not ok:
        print("FAIL:", what)
        sys.exit(1)
    print("PASS:", what)


def main():
    jt = (ROOT / "host/hos_jobtrace.c").read_text(encoding="utf-8")
    hos = (ROOT / "host/hos.h").read_text(encoding="utf-8")
    nvn = (ROOT / "host/nvn_native.c").read_text(encoding="utf-8")
    check("getenv(\"HL_STALL_WATCH\")" in jt and "getenv(\"HL_STALL_WATCH_DIR\")" in jt, "the switch and the dump folder are read")
    check("(JT_CAP & (JT_CAP - 1)) == 0" in jt or "#define JT_CAP (4u << 20)" in jt, "the ring's size is a power of two (the mask wraps it)")
    check("if (sw_ring) i &= JT_CAP - 1;" in jt, "the records wrap in ring mode and only then")
    check("void hos_sw_present(void);" in hos, "hos.h declares the present hook")
    check("hos_sw_present();" in nvn and nvn.index("present_thread = hos_self();") < nvn.index("hos_sw_present();"),
          "the presenting thread calls it at each present")
    check("\"# jobtrace window %lld %lld\\n\"" in jt and jt.count("# jobtrace window") >= 2, "a dump has the job trace's header, readable by jobtrace.py")
    sus = jt.index("SuspendThread(hs[i])")
    mid = jt[sus:jt.index("ResumeThread(hs[i])")]
    check("hos_log" not in mid and "fprintf" not in mid and "malloc" not in mid, "nothing is logged or allocated while a thread is suspended")
    check("dumped < max_dumps" in jt and "HL_STALL_WATCH_MAX" in jt and "HL_STALL_WATCH_AFTER" in jt, "at most 80 dumps (HL_STALL_WATCH_MAX), none before the start-up's end")
    # a dump reader: the format, one record
    sample = "# jobtrace window 100 900\nT 0 0000000000000001 Presentation_Thread\n150 15 0 1234 83abcd00\n"
    rec = [l.split() for l in sample.splitlines() if l[0] not in "#T"]
    check(rec == [["150", "15", "0", "1234", "83abcd00"]], "a record is <ns> <type> <thread> <key hex> <lr hex>")
    print("PASS")


if __name__ == "__main__":
    main()
