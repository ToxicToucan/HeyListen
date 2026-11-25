# Copyright (C) 2026 Sacrappy

"""Issue 97: dead code elimination keeps the warp-wide operations (VOTE, ballot, shuffles), in particular the VoteAny that the
pseudo-operation fold leaves without a reader in R2P with mask 0x40. Runs the real passes.

python -I -B tests/test_dce_convergent_ops.py [--cxx g++]
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDEN = ROOT / "gpu/vk/eden"
SR = EDEN / "shader_recompiler"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    sources = [SR / "frontend/ir" / n for n in ("basic_block.cpp", "condition.cpp", "ir_emitter.cpp", "microinstruction.cpp",
                                                 "opcodes.cpp", "patch.cpp", "type.cpp", "value.cpp")]
    sources += [SR / "ir_opt/constant_propagation_pass.cpp", SR / "ir_opt/dead_code_elimination_pass.cpp"]
    with tempfile.TemporaryDirectory(prefix="dce-conv-") as tmp:
        exe = Path(tmp) / "t"
        subprocess.run([args.cxx, "-std=c++20", "-O1", "-w", "-I", str(EDEN), "-I", str(ROOT / "gpu/vk/compat"),
                        "-I", str(ROOT / "gpu/compat"), str(ROOT / "tests/test_dce_convergent_ops.cpp"),
                        *map(str, sources), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
