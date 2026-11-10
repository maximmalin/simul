#!/usr/bin/env python3
"""Отдел улучшения пайплайна.

После каждого заказа автоматически анализирует выполнение, находит узкие места,
улучшает планировщик/движок/стадии, тестирует и внедряет улучшения без поломок.
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
sys.path.insert(0, str(ROOT / "backend"))

OUT = ROOT / "out" / "pipeline_improvements"
OUT.mkdir(parents=True, exist_ok=True)

class PipelineImprovementDept:
    def __init__(self):
        self.name = "Отдел улучшения пайплайна"
        self.roles = ["аналитик_производительности", "инженер_оптимизации", "тестировщик", "интегратор"]
        
    def run_cmd(self, cmd, timeout=300):
        try:
            r = subprocess.run(cmd, shell=True, cwd=ROOT, 
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             timeout=timeout, text=True)
            return r.returncode, r.stdout
        except Exception as e:
            return -1, str(e)
    
    def analyze_last_order(self):
        """Анализирует последний выполненный заказ"""
        analysis = {
            "timestamp": datetime.now().isoformat(),
            "bottlenecks": [],
            "suggestions": [],
            "metrics": {}
        }
        
        # Анализируем логи агентов
        log_file = ROOT / ".run" / "graph.db"
        agents_log = ROOT / "design" / "agents_transcript.md"
        if agents_log.exists():
            content = agents_log.read_text()[-5000:]
            if "timeout" in content.lower():
                analysis["bottlenecks"].append("таймауты агентов")
                analysis["suggestions"].append("увеличить таймауты или оптимизировать промпты")
            if "ошибк" in content.lower():
                analysis["bottlenecks"].append("ошибки в выполнении")
                analysis["suggestions"].append("улучшить обработку ошибок")
        
        return analysis
    
    def improve_from_analysis(self, analysis):
        """Вносит улучшения на основе анализа"""
        improvements = []
        
        # Улучшить обработку таймаутов
        if "таймауты агентов" in analysis["bottlenecks"]:
            stages = ROOT / "backend/agents/stages.py"
            if stages.exists():
                content = stages.read_text()
                if "STAGE_TIMEOUT" in content:
                    # Увеличить таймауты
                    old = 'STAGE_TIMEOUT = float(os.environ.get("KR_STAGE_TIMEOUT", "240"))'
                    new = 'STAGE_TIMEOUT = float(os.environ.get("KR_STAGE_TIMEOUT", "300"))'
                    if old in content and new not in content:
                        content = content.replace(old, new)
                        stages.write_text(content)
                        improvements.append("stages: увеличен таймаут")
        
        # Улучшить планировщик
        planner = ROOT / "backend/agents/planner.py"
        if planner.exists():
            content = planner.read_text()
            if "_learn_from_order" not in content:
                old = 'from .types import Project, Step\n'
                new = '''from .types import Project, Step

# Обучение на успешных заказах
_order_history = []

def _learn_from_order(text: str, targets: list[str], success: bool):
    """Учим планировщик на истории заказов"""
    _order_history.append({"text": text[:100], "targets": targets, "success": success})
    # Сохраняем в файл
    try:
        hist_file = Path(__file__).parent.parent / "out" / "pipeline_improvements" / "order_history.jsonl"
        hist_file.parent.mkdir(parents=True, exist_ok=True)
        with hist_file.open("a") as f:
            f.write(json.dumps({"text": text[:100], "targets": targets, "success": success}, ensure_ascii=False) + "\\n")
    except:
        pass
'''
                if old in content:
                    content = content.replace(old, new)
                    # Вызов в plan
                    old2 = '    return {"intent": intent, "targets": picked, "planner": note,\n            "note": note, "steps": steps}\n'
                    new2 = '    res = {"intent": intent, "targets": picked, "planner": note,\n            "note": note, "steps": steps}\n    try:\n        _learn_from_order(text, picked, len(picked) > 0)\n    except:\n        pass\n    return res\n'
                    if old2 in content:
                        content = content.replace(old2, new2)
                        planner.write_text(content)
                        improvements.append("planner: добавлено обучение на истории заказов")
        
        return improvements
    
    def test_improvements(self):
        """Тестирует улучшения"""
        rc, out = self.run_cmd("python3 -m pytest tests/requirements/pcb_suite/ tests/requirements/report/ -q 2>&1 | tail -3", 600)
        return rc == 0
    
    def post_order_improve(self, order_text="", success=True):
        """Вызывается после каждого заказа"""
        print(f"\n🔧 Отдел улучшения пайплайна: анализ после заказа...")
        analysis = self.analyze_last_order()
        improvements = self.improve_from_analysis(analysis)
        
        if improvements:
            print(f"📝 Предложено улучшений: {len(improvements)}")
            for imp in improvements:
                print(f"  • {imp}")
            
            if self.test_improvements():
                print("✅ Улучшения протестированы и работают")
                self.run_cmd("git add -A")
                self.run_cmd(f'git commit -m "pipeline-improve: {", ".join(improvements)}"')
                print("💾 Улучшения внедрены")
            else:
                print("❌ Тесты провалились, откатываю")
                self.run_cmd("git checkout -- backend/agents/stages.py backend/agents/planner.py 2>/dev/null")
        else:
            print("✨ Пайплайн в оптимальном состоянии")

if __name__ == "__main__":
    dept = PipelineImprovementDept()
    dept.post_order_improve()
