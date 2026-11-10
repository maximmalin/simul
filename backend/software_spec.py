"""Извлечение требований к ПО/прошивке из технического задания (ТЗ).

Консультант-агент (consultant) выделяет из ТЗ отдельное ТЗ на ПО:
прошивку МК, протоколы, интерфейсы, тайминги, память, тесты.
Результат: out/software_tz.md + out/software_task.json — передаётся
отделу программирования (programming_dept.py).

Аналог mech_spec.py, но для software/firmware.
"""
from __future__ import annotations
import json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TZ_CANDIDATES = ["technical_task.md", "technical_task_en.md", "technical_task_ru.md"]

KEYWORDS = [
    ("mcu", "микроконтроллер", r"микроконтроллер|МК\b|MCU|STM32|ESP32|AVR|PIC\b|Cortex"),
    ("firmware", "прошивка", r"прошивк|firmware|bootloader|загрузчик"),
    ("protocol", "протоколы", r"протокол|SPI|I2C|I²C|UART|USB|CAN|BLE|Wi-?Fi|RF\b|Modbus"),
    ("timing", "тайминги/реальное время", r"тайминг|real.?time|RTOS|прерывани|latency|задержк"),
    ("memory", "память", r"памят|flash|RAM|EEPROM|storage|буфер"),
    ("power", "энергопотребление", r"энергопотреблен|sleep|low.power|потреблен.*ток|ток.*потр"),
    ("sensor", "датчики", r"датчик|sensor|ADC|измерен"),
    ("dsp", "ЦОС/сигнал", r"фильтр|FFT|демодуляц|модуляц|DSP|сигнал"),
    ("test", "тесты ПО", r"тест|unit.test|HIL|SIL|проверк.*ПО"),
    ("update", "обновление", r"обновлен.*прошивк|OTA|FOTA|field.update"),
]


def find_tz(root: Path = ROOT) -> Path | None:
    for c in TZ_CANDIDATES:
        p = root / c
        if p.exists():
            return p
    return None


def scan(root: Path = ROOT) -> dict:
    tz = find_tz(root)
    if tz is None:
        return {"found": False, "requirements": []}
    text = tz.read_text(encoding="utf-8", errors="ignore")
    reqs = []
    for key, label, pat in KEYWORDS:
        matches = re.findall(pat, text, re.I)
        if matches:
            # извлечь строки с контекстом
            lines = [ln for ln in text.splitlines() if re.search(pat, ln, re.I)]
            reqs.append({"key": key, "label": label, "count": len(matches),
                         "excerpts": lines[:5]})
    return {"found": True, "tz_path": str(tz), "requirements": reqs}


def build_spec(root: Path = ROOT) -> dict:
    data = scan(root)
    data["delegated"] = bool(data.get("requirements"))
    data["requirement_count"] = len(data.get("requirements", []))
    return data


def write_outputs(root: Path = ROOT) -> dict:
    root = Path(root)
    spec = build_spec(root)
    out = root / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "software_spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2))
    # markdown TZ
    lines = ["# ТЗ на программное обеспечение (прошивку)", ""]
    if not spec.get("found"):
        lines.append("ТЗ не найдено.")
    else:
        lines.append(f"Источник: `{spec['tz_path']}`")
        lines.append("")
        lines.append("## Требования (извлечённые из ТЗ)")
        lines.append("")
        for r in spec["requirements"]:
            lines.append(f"### {r['label']} (`{r['key']}`)")
            for ex in r.get("excerpts", []):
                lines.append(f"- {ex.strip()}")
            lines.append("")
        lines.append("## Стандарты (консультант)")
        lines.append("")
        lines.append("- ГОСТ 19.101-77 (ЕСПД): состав и оформление документов")
        lines.append("- ГОСТ 19.201-78 (ТЗ на ПО)")
        lines.append("- MISRA C:2012 (если C/C++)")
        lines.append("- IEC 61508 (функциональная безопасность, если применимо)")
        lines.append("- ISO/IEC 25010 (качество ПО)")
        lines.append("")
        lines.append("## Задачи для отдела программирования")
        lines.append("")
        lines.append("1. Реализовать прошивку МК согласно требованиям и стандартам.")
        lines.append("2. Обеспечить протоколы и интерфейсы (SPI/I2C/UART/USB).")
        lines.append("3. Покрыть unit-тестами (host) и HIL/SIL.")
        lines.append("4. Документировать API и тайминги.")
        lines.append("5. Проверить соответствие MISRA C (если C/C++).")
    (out / "software_tz.md").write_text("\n".join(lines), encoding="utf-8")
    # task json
    task = {
        "type": "software",
        "requirements": spec.get("requirements", []),
        "deliverables": ["firmware binary", "unit tests", "API docs", "timing report"],
        "agents": ["coder", "tester", "reviewer", "debugger"],
    }
    (out / "software_task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2))
    return spec


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Extract software/firmware requirements from TZ")
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args()
    spec = write_outputs(Path(a.root))
    print(json.dumps({"delegated": spec.get("delegated"), "count": spec.get("requirement_count")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())