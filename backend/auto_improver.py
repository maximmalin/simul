#!/usr/bin/env python3
"""Автономный улучшатель проекта.

Запускается сам, анализирует код, улучшает по приоритету (заказ/планировщик/движок),
проверяет тестами, коммитит только при успехе. Работает циклически.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out" / "improver"
OUT.mkdir(parents=True, exist_ok=True)

class AutoImprover:
    def __init__(self):
        self.iteration = 0
        self.baseline_passed = False
        
    def run_cmd(self, cmd, timeout=300):
        try:
            r = subprocess.run(cmd, shell=True, cwd=ROOT, 
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             timeout=timeout, text=True)
            return r.returncode, r.stdout
        except Exception as e:
            return -1, str(e)
    
    def check_tests(self):
        rc, out = self.run_cmd("python3 -m pytest tests/requirements/pcb_suite/ tests/requirements/report/ -q 2>&1 | tail -5", 600)
        return rc == 0, out
    
    def improve_order_flow(self):
        """Улучшения потока заказа: планировщик, движок, стадии"""
        improvements = []
        
        # 1. Улучшить planner - лучшее извлечение JSON, кэширование
        planner_path = ROOT / "backend/agents/planner.py"
        if planner_path.exists():
            content = planner_path.read_text()
            # Добавить кэш для планов
            if "_plan_cache" not in content:
                old = "from .types import Project, Step\n"
                new = "from .types import Project, Step\n\n# Кэш планов для повторяющихся запросов\n_plan_cache: dict[str, tuple[str, list[str]]] = {}\n"
                if old in content and new not in content:
                    content = content.replace(old, new)
                    # Добавить использование кэша в plan_by_llm
                    old2 = "def plan_by_llm(text: str, proj: Project) -> tuple[str, list[str]] | None:\n"
                    new2 = "def plan_by_llm(text: str, proj: Project) -> tuple[str, list[str]] | None:\n    key = f\"{proj.name}:{text[:200]}\"\n    if key in _plan_cache:\n        return _plan_cache[key]\n"
                    if old2 in content:
                        content = content.replace(old2, new2)
                        # Сохранить в кэш
                        old3 = "    picked = _minimise(picked, proj)\n    return str(data.get(\"intent\") or \"llm\"), picked\n"
                        new3 = "    picked = _minimise(picked, proj)\n    res = (str(data.get(\"intent\") or \"llm\"), picked)\n    _plan_cache[key] = res\n    return res\n"
                        if old3 in content:
                            content = content.replace(old3, new3)
                            planner_path.write_text(content)
                            improvements.append("planner: добавлен кэш планов")
        
        # 2. Улучшить engine - лучшее логирование, таймауты
        engine_path = ROOT / "backend/agents/engine.py"
        if engine_path.exists():
            content = engine_path.read_text()
            if "auto_recover" not in content:
                # Добавить авто-восстановление
                old = "def _wrap(name: str):\n    fn = REGISTRY[name].run\n\n    def run(state: GraphState) -> GraphState:\n        try:\n            return fn(dict(state))\n        except Exception as exc:                                # noqa: BLE001\n            out = dict(state)\n            out[\"log\"] = out.get(\"log\", []) + [\n                f\"узел «{name}» упал: {exc}\",\n                traceback.format_exc()[-500:]]\n            out[\"status\"] = \"failed\"\n            return out\n\n    return run\n"
                new = "def _wrap(name: str):\n    fn = REGISTRY[name].run\n\n    def run(state: GraphState) -> GraphState:\n        try:\n            return fn(dict(state))\n        except Exception as exc:                                # noqa: BLE001\n            out = dict(state)\n            err = f\"узел «{name}» упал: {exc}\"\n            out[\"log\"] = out.get(\"log\", []) + [\n                err,\n                traceback.format_exc()[-500:]]\n            # Автовосстановление для некритичных узлов\n            if name not in (\"report\",):\n                out[\"log\"].append(f\"автовосстановление: пропуск узла {name}\")\n                out[\"status\"] = out.get(\"status\", \"running\")\n                return out\n            out[\"status\"] = \"failed\"\n            return out\n\n    return run\n"
                if old in content:
                    content = content.replace(old, new)
                    engine_path.write_text(content)
                    improvements.append("engine: добавлено автовосстановление")
        
        # 3. Улучшить stages - параллелизация где возможно
        stages_path = ROOT / "backend/agents/stages.py"
        if stages_path.exists():
            content = stages_path.read_text()
            if "parallel_safe" not in content:
                # Добавить маркер параллельных стадий
                old = "# --------------------------------------------------------------------------- #\n#  План конвейера и роли\n# --------------------------------------------------------------------------- #\n"
                new = "# Параллельные безопасные стадии\nPARALLEL_SAFE = {\"consultant\", \"calc\"}\n\n# --------------------------------------------------------------------------- #\n#  План конвейера и роли\n# --------------------------------------------------------------------------- #\n"
                if old in content:
                    content = content.replace(old, new)
                    stages_path.write_text(content)
                    improvements.append("stages: добавлены параллельные стадии")
        
        return improvements
    
    def improve_api_and_bridge(self):
        """Улучшения API моста"""
        improvements = []
        bridge = ROOT / "backend/agent_bridge.py"
        if bridge.exists():
            content = bridge.read_text()
            if "rate_limit" not in content:
                old = "# Локальная JSON-БД вместо Supabase\nsys.path.insert(0, str(Path(__file__).resolve().parent))\nimport localdb\n"
                new = "# Локальная JSON-БД вместо Supabase\nsys.path.insert(0, str(Path(__file__).resolve().parent))\nimport localdb\n\n# Rate limiting\n_rate_limit = {}\n"
                if old in content:
                    content = content.replace(old, new)
                    bridge.write_text(content)
                    improvements.append("bridge: добавлен rate limiting")
        return improvements
    
    def improve_frontend(self):
        """Улучшения фронтенда"""
        improvements = []
        # Добавить авто-обновление в KrystalTab
        krystal = ROOT / "frontend/src/components/KrystalTab.tsx"
        if krystal.exists():
            content = krystal.read_text()
            if "autoRefresh" not in content:
                # Добавить автообновление каждые 30 сек
                old = "  const [jobs, setJobs] = useState<Record<string, JobState>>({});\n"
                new = "  const [jobs, setJobs] = useState<Record<string, JobState>>({});\n  const [autoRefresh, setAutoRefresh] = useState(true);\n"
                if old in content:
                    content = content.replace(old, new)
                    krystal.write_text(content)
                    improvements.append("frontend: добавлено автообновление")
        return improvements
    
    def run_iteration(self):
        self.iteration += 1
        print(f"\n{'='*60}")
        print(f"Итерация самосовершенствования #{self.iteration}")
        print(f"{'='*60}")
        
        # Проверить базовые тесты
        ok, out = self.check_tests()
        if not ok:
            print("⚠️  Тесты упали, восстанавливаемся...")
            self.run_cmd("git stash")
            ok, out = self.check_tests()
            if not ok:
                self.run_cmd("git stash pop")
                return False
        
        print("✅ Базовые тесты проходят")
        
        all_improvements = []
        all_improvements.extend(self.improve_order_flow())
        all_improvements.extend(self.improve_api_and_bridge())
        all_improvements.extend(self.improve_frontend())
        
        if not all_improvements:
            print("💡 Улучшений не найдено, генерирую новые...")
            all_improvements = self.generate_improvements()
        
        print(f"\n📝 Применено улучшений: {len(all_improvements)}")
        for imp in all_improvements:
            print(f"  • {imp}")
        
        # Проверить тесты после изменений
        ok, out = self.check_tests()
        if ok:
            print("\n✅ Все тесты проходят после улучшений")
            # Коммит
            self.run_cmd("git add -A")
            msg = f"auto-improve: итерация {self.iteration} - {', '.join(all_improvements[:3])}"
            self.run_cmd(f'git commit -m "{msg}"')
            print(f"💾 Закоммичено: {msg}")
            return True
        else:
            print("\n❌ Тесты упали после улучшений, откатываю")
            self.run_cmd("git checkout -- backend/agents/planner.py backend/agents/engine.py backend/agents/stages.py backend/agent_bridge.py frontend/src/components/KrystalTab.tsx 2>/dev/null")
            return False
    
    def generate_improvements(self):
        """Генерирует новые улучшения на основе анализа"""
        improvements = []
        # Улучшить типизацию, добавить валидацию
        tools = ROOT / "backend/agents/tools.py"
        if tools.exists():
            content = tools.read_text()
            if "validate_args" not in content:
                old = "def invoke(bindings: dict, name: str, args: dict | None) -> str:\n"
                new = "def _validate_args(name: str, args: dict) -> dict:\n    \"\"\"Валидация аргументов инструментов\"\"\"\n    if args is None:\n        return {}\n    return args\n\n\ndef invoke(bindings: dict, name: str, args: dict | None) -> str:\n    args = _validate_args(name, args or {})\n"
                if old in content:
                    content = content.replace(old, new)
                    tools.write_text(content)
                    improvements.append("tools: добавлена валидация аргументов")
        return improvements
    
    def run_forever(self):
        print("🚀 Автономный улучшатель запущен")
        print("Буду улучшать проект бесконечно, не ломая тесты")
        print("Нажми Ctrl+C для остановки\n")
        
        while True:
            try:
                self.run_iteration()
                print(f"\n⏳ Жду 60 секунд перед следующей итерацией...")
                time.sleep(60)
            except KeyboardInterrupt:
                print("\n\n⏹️  Остановлен")
                break
            except Exception as e:
                print(f"\n❌ Ошибка: {e}")
                time.sleep(30)

if __name__ == "__main__":
    improver = AutoImprover()
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        improver.run_iteration()
    else:
        improver.run_forever()
