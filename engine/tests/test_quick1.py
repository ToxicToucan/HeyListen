# Copyright (C) 2026 Sacrappy

"""QUICK1: the five cheap ideas from Rekarina's notes, each behind its own switch and off until measured.

Source-shape checks (nothing is built or run): every switch is read where it must be, off without its variable, and the
invariants that keep each one safe are in the code.

  1. RX_VK_COMPUTE_ONLY_IMAGES  vk_texture.cpp: a shader-image colour image first looked up for sampling is made without
                                COLOR_ATTACHMENT; texture_get(need_attach) makes it again when a pass binds it as a target
  2. HL_NV_CACHE_DIR            hos_main.c: the NVIDIA driver's shader cache beside the game, before Vulkan loads
  3. HOS_SHADOW_LADDER          nvn_native.c + shadow_slot.h: rungs 128 ... 1920 for the reported storage of shadow maps
  4. HOS_SHORT_SLEEP_PAUSE=2    hos_thread.c: one clock read per eight pauses;
     HOS_KWAKE_AFTER_UNLOCK,    hos_svc.c: threads woken after klock is released; a plain svcSignalToAddress with no
     HOS_ADDR_SIGNAL_FAST       address waiter skips klock (the waiter counts itself before it reads the word)

python -I -B tests/test_quick1.py
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
fails = []


def src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def need(cond, what):
    if not cond:
        fails.append(what)


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start)
    depth = 0
    while True:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
        if depth == 0:
            return text[start:end]


tex = src("gpu/vk/vk_texture.cpp")
need('getenv("RX_VK_COMPUTE_ONLY_IMAGES")' in tex and "atoi(getenv(\"RX_VK_COMPUTE_ONLY_IMAGES\")) != 0" in tex,
     "1: RX_VK_COMPUTE_ONLY_IMAGES is read, off without it")
need("NVN_TEXTURE_FLAGS_IMAGE" in tex and "compute_only_on() && !as_target" in tex,
     "1: only a shader-image texture first looked up for sampling is a candidate")
need("x.no_attach = true;" in tex and "else {\n            x.usage |= VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;" in tex,
     "1: a candidate goes without COLOR_ATTACHMENT, any other keeps it")
need("!attach_known(t)" in tex and "attach_seen.insert(attach_key(t))" in tex,
     "1: a shape bound as a target is remembered: the next one is made with the usage")
get = block(tex, "Texture* texture_get(const nvn_tex* t, bool for_sampling, bool need_attach)")
need("need_attach && it->second->no_attach" in get and "destroy_later(*old);" in get and "forget_gpu(old);" in get,
     "1: texture_get makes a compute-only image again when a target needs it (the old image goes through destroy_later)")
draw = src("gpu/vk/vk_draw.cpp")
need("texture_get(t, false, true)" in draw, "1: targets() asks for the attachment usage")
xfer = src("gpu/vk/vk_xfer.cpp")
need("!whole && s.tex->no_attach" in xfer, "1: a region clear (a pass) asks for the attachment usage")
hdr = src("gpu/vk/vk_texture.h")
need("bool no_attach" in hdr and "bool need_attach = false" in hdr, "1: Texture::no_attach and texture_get's need_attach")
need("Copyright (C) 2026 Sacrappy" in src("tests/test_quick1.py"), "this test has the copyright line")

main = src("host/hos_main.c")
nv = block(main, "static void nv_cache_env(void)")
need('getenv("HL_NV_CACHE_DIR")' in nv and '!strcmp(want, "0")' in nv, "2: HL_NV_CACHE_DIR unset or 0 is off")
need("nvidia_driver_present()" in nv, "2: NVIDIA only")
need("__GL_SHADER_DISK_CACHE_PATH" in nv and "__GL_SHADER_DISK_CACHE_SIZE" in nv and "__GL_SHADER_DISK_CACHE_SKIP_CLEANUP" in nv,
     "2: the three driver variables")
need("4294967296" in nv, "2: 4 GB")
need("nv_shader_cache" in nv, "2: the folder beside the game")
need(nv.index('if (getenv("__GL_SHADER_DISK_CACHE_PATH"))') < nv.index("hos_game_path_a"), "2: a path the environment sets is kept")
for m in re.finditer(r"\n(?:int main|static int game_main)\(int argc, char \*\*argv\) \{", main):
    body = main[m.end():m.end() + 2500]
    # (the order thread's default, DECOUPLE, may sit between: it only decides RX_VK_ORDER_THREAD, nothing the cache reads)
    need("load_hookshot_env();\n    nv_cache_env();" in body or "load_hookshot_env();\n    order_thread_default();\n    nv_cache_env();" in body,
         "2: both mains set the cache right after hookshot_env.txt (and the order thread's default)")
need(main.index("static void nv_cache_env") < main.index("static void load_hookshot_env(void)"), "2: defined before use")

shadow = src("host/shadow_slot.h")
need("hos_shadow_slot_side_ladder" in shadow and "128, 192, 256, 384, 512, 768, 1152, 1536, 1920" in shadow,
     "3: the ladder's rungs")
need('getenv("HOS_SHADOW_LADDER")' in src("host/nvn_native.c"), "3: HOS_SHADOW_LADDER")

th = src("host/hos_thread.c")
need("pause_spin >= 2" in th and 'atoi(getenv("HOS_SHORT_SLEEP_PAUSE"))' in th, "4: HOS_SHORT_SLEEP_PAUSE=2 is the eight-pause loop")

svc = src("host/hos_svc.c")
need('getenv("HOS_KWAKE_AFTER_UNLOCK")' in svc and 'getenv("HOS_ADDR_SIGNAL_FAST")' in svc, "4: both kernel switches")
for fn in ("static uint32_t arbitrate_unlock(", "static uint32_t wait_key(", "static void signal_key(", "static uint32_t signal_address("):
    b = block(svc, fn)
    need("flush_wakes(&wk);" in b, "4: %s wakes after the unlock" % fn.split("(")[0].split()[-1])
    need(b.index("flush_wakes(&wk);") > b.index("ReleaseSRWLockExclusive(&klock);"), "4: %s flushes after klock is released" % fn)
wa = block(svc, "static uint32_t wait_address(")
need(wa.index("__atomic_add_fetch(&addr_waiting") < wa.index("case 0:"), "4: a waiter counts itself before it reads the word")
need(wa.count("__atomic_sub_fetch(&addr_waiting") == 3, "4: every path that does not wait takes the count back")
need("on_key == 2) __atomic_sub_fetch(&addr_waiting" in svc, "4: a removed address waiter leaves the count")
sa = block(svc, "static uint32_t signal_address(")
need("__atomic_thread_fence(__ATOMIC_SEQ_CST);" in sa and "type == 0" in sa and "!__atomic_load_n(&addr_waiting" in sa,
     "4: only a plain signal skips the lock, behind a fence, when nobody waits")

if fails:
    for f in fails:
        print("FAIL: " + f)
    raise SystemExit("test_quick1: failed")
print("PASS: QUICK1's five switches are wired and each is off without its variable")
