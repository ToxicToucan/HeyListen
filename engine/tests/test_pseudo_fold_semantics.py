# Copyright (C) 2026 Sacrappy

"""The pseudo-operation fold (RX_VK_FOLD_PSEUDO) gives every folded instruction the value and the zero/sign flags the SPIR-V back end
would have made from the unfolded one (edge cases: count 0 and 32, offset + count at or over 32, all-ones, sign bit; then millions of
random immediates). Runs the real ConstantPropagationPass.

python -I -B tests/test_pseudo_fold_semantics.py [--cxx g++]
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
    sources += [SR / "ir_opt/constant_propagation_pass.cpp"]
    with tempfile.TemporaryDirectory(prefix="pseudo-fold-sem-") as tmp:
        exe = Path(tmp) / "t"
        subprocess.run([args.cxx, "-std=c++20", "-O1", "-w", "-I", str(EDEN), "-I", str(ROOT / "gpu/vk/compat"),
                        "-I", str(ROOT / "gpu/compat"), str(ROOT / "tests/test_pseudo_fold_semantics.cpp"),
                        *map(str, sources), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
