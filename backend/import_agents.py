#!/usr/bin/env python3
"""Перенос имён агентов из AGENT_PROMPTS.md в описание проекта.

Зачем
----
Идентификаторы агентов заданы бюро и интерфейсом: `AGENT-MK-01`,
`AGENT-TST-01` и так далее. Генератор промптов не имеет права их
выдумывать — иначе один и тот же агент называется в разных местах по-разному,
а ссылки в переписке и отчётах перестают работать.

Поэтому канонические имена живут в `agents.project.yaml` — это данные,
а не код, — а бюро и фронтенд читают их оттуда.

Скрипт разовый: он переносит уже согласованные имена и проверяет, что
ничего не потерялось. Запускать вручную не нужно.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_bridge as BR  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "agents.project.yaml"

#: цель make, к которой относится агент, выводится из его команды.
#: Имена целей меняются, а идентификаторы бюро — нет, поэтому
#: привязка идёт от идентификатора.
TARGET_BY_ID = {
    "AGENT-MK-01": "all",
    "AGENT-MK-02": "page",
    "AGENT-TST-01": "test",
    "AGENT-GNUR-01": "gnuradio",
    "AGENT-TH-01": "thermal",
    "AGENT-RL-01": "reliability",
    "AGENT-DF-01": "link-budget",
    "AGENT-DF-02": "drawings",
    "AGENT-ENC-01": "enclosure",
    "AGENT-KICAD-02": "pcb",
    "AGENT-ML-02": None,
}

ROLE_BY_ID = {
    "AGENT-MK": "agent.report",
    "AGENT-TST": "agent.verify",
    "AGENT-GNUR": "agent.rf",
    "AGENT-TH": "agent.thermal",
    "AGENT-RL": "agent.reliability",
    "AGENT-DF": "agent.rf",
    "AGENT-ENC": "agent.mechanical",
    "AGENT-KICAD": "agent.pcb",
    "AGENT-ML": "agent.ml",
    "AGENT-DOC": "agent.report",
    "AGENT-GIT": "agent.release",
}


def yaml_str(value: str) -> str:
    """Строка в YAML: кавычки и переводы строк."""
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    text = text.replace("\n", " ").strip()
    return f'"{text}"'


def main() -> int:
    agents = BR.parse_agents()
    enc = BR.parse_enclosure_agent()
    if enc:
        agents.append(enc)

    print(f"разобрано агентов: {len(agents)}")

    block = ["\n# ---------------------------------------------------------------------------",
             "# Агенты бюро.",
             "#",
             "# Идентификаторы заданы бюро и интерфейсом: их показывает",
             "# /api/agents, по ним пишутся отчёты и переписка. Поэтому",
             "# переименовывать их нельзя, а выдумывать новые — тем более.",
             "#",
             "# Здесь у агента указана цель make, за которую он отвечает.",
             "# Команда и файлы при этом собираются заново из описания",
             "# проекта: пути в старом AGENT_PROMPTS.md были абсолютными и",
             "# устаревали.",
             "#",
             "# Без привязки к цели агент остаётся в бюро (его видно в",
             "# интерфейсе), но в графе заказов не участвует.",
             "# ---------------------------------------------------------------------------",
             "agents:"]

    bound = 0
    for a in agents:
        aid = a.get("id", "")
        target = TARGET_BY_ID.get(aid, None)
        prefix = aid.rsplit("-", 1)[0] if "-" in aid else aid
        role = ROLE_BY_ID.get(prefix, "agent.build")
        block.append(f"  - id: {aid}")
        block.append(f"    name: {yaml_str(a.get('title', aid))}")
        block.append(f"    goal: {yaml_str(a.get('goal', ''))}")
        if a.get('command'):
            block.append(f"    command: {yaml_str(a.get('command'))}")
        if target:
            block.append(f"    target: {target}")
            bound += 1
        else:
            raw = a.get("files") or ""
            # бюро хранит файлы строкой через запятую; здесь разбираем
            # на элементы, чтобы не писать символы вместо путей
            items = [x.strip().strip("`").strip() for x in raw.split(",")]
            items = [x for x in items if x and x != "`"]
            if items:
                block.append(f"    files: [{', '.join(yaml_str(x) for x in items)}]")
            else:
                block.append("    files: []")
        block.append(f"    role: {role}")
        block.append("")

    profile = PROFILE.read_text(encoding="utf-8")
    # старый раздел, если он уже есть
    profile = re.sub(r"\n# -+ ?\n# Агенты бюро\..*?(?=\n\w+:|\Z)", "\n",
                     profile, flags=re.S)
    PROFILE.write_text(profile.rstrip() + "\n" + "\n".join(block) + "\n",
                       encoding="utf-8")

    print(f"записано в agents.project.yaml: {len(agents)}")
    print(f"  привязано к целям: {bound}")
    print(f"  осталось без цели: {len(agents) - bound} "
          f"(видны в бюро, в графе не участвуют)")

    # проверка: YAML разбирается
    from agents.profile import load
    p = load(ROOT)
    declared = p.targets
    print(f"\nописание читается: целей {len(declared)}, "
          f"раннер {p.runner}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())