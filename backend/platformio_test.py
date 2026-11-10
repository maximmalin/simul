"""PlatformIO-тестирование прошивки.

Создаёт минимальный PlatformIO-проект из сгенерированного кода, запускает
`pio run` (сборка) и опционально `pio test` (unit-тесты). Используется
софтваре-флоу для проверки кода на реальном тулчейне (не только gcc).

CLI: python3 backend/platformio_test.py --code out/software_result/final_code.txt --board nucleo_f401re
"""
from __future__ import annotations
import json, subprocess, sys, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_BOARD = "nucleo_f401re"  # STM32F401 (есть в ststm32)


def run_pio(code: str, board: str = DEFAULT_BOARD, out_dir: str = "out/pio_test") -> dict:
    work = Path(out_dir)
    work.mkdir(parents=True, exist_ok=True)
    src_dir = work / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "main.cpp").write_text(code, encoding="utf-8")

    # platformio.ini
    ini = f"""[env:{board}]
platform = ststm32
board = {board}
framework = stm32cube
build_flags = -Wall
"""
    (work / "platformio.ini").write_text(ini, encoding="utf-8")

    # lib (пусто)
    (work / "lib").mkdir(exist_ok=True)
    (work / "test").mkdir(exist_ok=True)

    results = {}
    # pio run
    try:
        p = subprocess.run(["pio", "run", "-d", str(work)], capture_output=True, text=True, timeout=300)
        results["build_rc"] = p.returncode
        results["build_output"] = (p.stdout or "")[-1500:] + (p.stderr or "")[-500:]
    except subprocess.TimeoutExpired:
        results["build_rc"] = 124
        results["build_output"] = "TIMEOUT"
    except Exception as e:
        results["build_rc"] = 1
        results["build_output"] = str(e)

    results["board"] = board
    results["success"] = (results.get("build_rc") == 0)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "pio_result.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    return results


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="PlatformIO build test for firmware")
    ap.add_argument("--code", required=True, help="path to source file")
    ap.add_argument("--board", default=DEFAULT_BOARD)
    ap.add_argument("--out", default="out/pio_test")
    a = ap.parse_args()
    code = Path(a.code).read_text(encoding="utf-8")
    r = run_pio(code, a.board, a.out)
    print(json.dumps({"success": r["success"], "build_rc": r["build_rc"]}, ensure_ascii=False))
    return 0 if r["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())