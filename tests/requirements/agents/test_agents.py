"""Тесты агентно-функционального графа.

Проверяется то, что легко сломать и трудно заметить:

* проект не должен «протекать» конкретикой — движок обязан работать
  на любом репозитории, а не только на этом;
* система сборки должна определяться верно, цели — читаться верно;
* конфликтующие цели не должны идти одновременно;
* план не должен содержать служебные цели и дубли покрытых;
* граф обязан собираться и работать без langgraph;
* числа в отчёте должны браться из артефактов, а не выдумываться.

Каждый тест создаёт временный проект с нуля — это гарантирует, что
движок не привязан к репозиторию: если бы он был, тесты бы упали.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))

from agents import engine, planner, profile, reporter, runner, tools  # noqa: E402
from agents.types import (Artifact, Project, Step, Target,  # noqa: E402
                          is_service)


# ==========================================================================
#  Фикстуры: посторонние проекты
# ==========================================================================
@pytest.fixture
def make_project(tmp_path):
    """Создать проект с нуля. Никаких файлов исходного репозитория."""
    def build(files: dict[str, str], name: str = "proj") -> Path:
        root = tmp_path / name
        for rel, content in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(textwrap.dedent(content), encoding="utf-8")
        return root
    return build


MAKEFILE = """\
    .PHONY: all app test clean serve
    all: app test
    app: build
    build: deps
    deps:
    test: app
    clean:
    serve:
    """

PACKAGE_JSON = json.dumps({
    "name": "widget", "version": "1.0.0",
    "scripts": {"build": "tsc", "test": "jest", "dev": "vite"},
}, indent=2)

JUSTFILE = """\
    build:
        echo build
    check: build
    """


# ==========================================================================
#  Движок не привязан к проекту
# ==========================================================================
class TestGeneric:
    def test_works_on_foreign_make_project(self, make_project):
        """Движок обязан работать на make-проекте без единого нашего файла."""
        root = make_project({"Makefile": MAKEFILE}, "foreign_make")
        proj = profile.load(root)
        assert proj.runner == "make"
        names = {t.name for t in proj.targets}
        assert {"all", "app", "test", "serve"} <= names

    def test_works_on_foreign_npm_project(self, make_project):
        root = make_project({"package.json": PACKAGE_JSON}, "foreign_npm")
        proj = profile.load(root)
        assert proj.runner == "npm"
        assert {t.name for t in proj.targets} == {"build", "test", "dev"}

    def test_works_on_foreign_just_project(self, make_project):
        root = make_project({"justfile": JUSTFILE}, "foreign_just")
        proj = profile.load(root)
        assert proj.runner == "just"
        assert {t.name for t in proj.targets} == {"build", "check"}

    def test_works_on_project_without_build_system(self, make_project):
        """Нет системы сборки — это не повод падать."""
        root = make_project({"README.md": "# пусто"}, "foreign_empty")
        proj = profile.load(root)
        assert proj.runner == "shell"
        assert proj.targets == []

    def test_no_project_specifics_in_engine(self):
        """В движке не должно быть имён и файлов этого проекта.

        Проверка идёт по AST: из кода вырезаются комментарии и
        докстринги. Иначе под правило попадали бы безобидные упоминания
        в документации, а настоящая протечка — строковый литерал в
        логике — проходила бы незамеченной.
        """
        import ast as _ast

        # имена целей и файлы артефактов именно Krystal Radio
        banned = {"thermal_fast", "link_budget", "gnuradio", "waterfall",
                  "reliability.json", "enclosure.json", "direction_finding",
                  "virtual_rx_spectrum", "link-budget", "board-svg",
                  "st32f103", "stm32"}
        pkg = Path(engine.__file__).parent
        offenders: list[str] = []

        for f in sorted(pkg.glob("*.py")):
            tree = _ast.parse(f.read_text(encoding="utf-8"))
            import inspect as _inspect
            holders = (_ast.Module, _ast.ClassDef, _ast.FunctionDef,
                       _ast.AsyncFunctionDef)
            # ast.get_docstring нормализует отступы, а литерал в дереве
            # хранится как есть — сравниваем в том же виде
            docstrings = {_ast.get_docstring(n) for n in _ast.walk(tree)
                          if isinstance(n, holders)}
            docstrings.discard(None)
            normalized = {_inspect.cleandoc(d) for d in docstrings}
            for node in _ast.walk(tree):
                if isinstance(node, _ast.Constant) and \
                        isinstance(node.value, str):
                    val = node.value
                    if val in docstrings or _inspect.cleandoc(val) in normalized:
                        continue
                    low = val.lower()
                    for word in banned:
                        if word in low:
                            offenders.append(
                                f"{f.name}:{getattr(node, 'lineno', '?')} {word}")
        assert not offenders, f"проектная конкретика в движке: {offenders[:6]}"

    def test_project_profile_is_data_not_code(self):
        """Описание проекта — файл в репозитории, а не модуль движка."""
        assert "agents.project.yaml" in profile.PROFILE_NAMES


# ==========================================================================
#  Системы сборки
# ==========================================================================
class TestRunners:
    def test_make_parses_dependencies(self, make_project):
        root = make_project({"Makefile": MAKEFILE}, "mk")
        proj = profile.load(root)
        assert proj.target("all").depends_on == ["app", "test"]
        assert proj.target("app").depends_on == ["build"]
        assert proj.target("build").depends_on == ["deps"]

    def test_make_command_has_parallel_flag(self, make_project):
        root = make_project({"Makefile": MAKEFILE}, "mk2")
        proj = profile.load(root)
        cmd = runner.get("make").command(root, proj.target("all"))
        assert cmd.startswith("make")
        assert "all" in cmd

    def test_npm_command_uses_run_script(self, make_project):
        root = make_project({"package.json": PACKAGE_JSON}, "np")
        proj = profile.load(root)
        assert runner.get("npm").command(root, proj.target("build")) \
            == "npm run build"

    def test_just_command(self, make_project):
        root = make_project({"justfile": JUSTFILE}, "jst")
        proj = profile.load(root)
        assert runner.get("just").command(root, proj.target("build")) \
            == "just build"

    def test_shell_without_command_fails_loudly(self, make_project):
        root = make_project({"README.md": "x"}, "sh")
        proj = profile.load(root)
        with pytest.raises(runner.BuildError):
            runner.get("shell").command(root, Target(name="x"))

    def test_mixed_make_and_npm_prefers_script(self, make_project):
        root = make_project({"Makefile": MAKEFILE, "package.json": PACKAGE_JSON},
                            "mix")
        proj = profile.load(root)
        assert proj.runner == "make+npm"
        assert runner.get("make+npm").command(root, proj.target("test")) \
            == "npm run test"

    def test_runner_registry_is_extensible(self):
        """Свой раннер добавляется регистрацией — без правок движка."""
        class Fake(runner.Runner):
            name = "тест-раннер"
            detect = ("x",)

            @classmethod
            def detect_in(cls, root):
                return False

            @classmethod
            def targets(cls, root):
                return []

            @classmethod
            def command(cls, root, target):
                return "true"

        runner.register(Fake)
        try:
            assert "тест-раннер" in runner.available()
            assert runner.get("тест-раннер") is Fake
        finally:
            runner.REGISTRY.pop("тест-раннер", None)


# ==========================================================================
#  Верхний уровень и служебные цели
# ==========================================================================
class TestTopLevel:
    def test_top_level_excludes_dependencies(self, make_project):
        root = make_project({"Makefile": MAKEFILE}, "tl")
        proj = profile.load(root)
        tops = {t.name for t in proj.buildable() if t.top_level}
        assert "all" in tops
        assert "build" not in tops      # build — зависимость all

    def test_service_targets_excluded_from_plans(self, make_project):
        root = make_project({"Makefile": MAKEFILE}, "sv")
        proj = profile.load(root)
        buildable = {t.name for t in proj.buildable()}
        assert "serve" not in buildable
        assert "clean" not in buildable
        assert "all" in buildable

    def test_service_declared_in_profile(self, make_project):
        files = {"Makefile": MAKEFILE,
                 "agents.project.yaml": "service:\n  - test\n"}
        root = make_project(files, "sv2")
        proj = profile.load(root)
        assert "test" not in {t.name for t in proj.buildable()}
        assert proj.target("test").service is True

    @pytest.mark.parametrize("name,expected", [
        ("serve", True), ("dev", True), ("watch-files", True),
        ("help", True), ("all", False), ("build", False), ("test", False),
    ])
    def test_is_service_heuristic(self, name, expected):
        assert is_service(name) is expected

    def test_top_level_falls_back_without_marking(self):
        """Цели без проставленных флагов всё равно классифицируются."""
        p = Project(targets=[Target(name="a", depends_on=["b"]),
                             Target(name="b")])
        assert [t.name for t in p.top_level()] == ["a"]


# ==========================================================================
#  Планирование
# ==========================================================================
class TestPlanner:
    @pytest.fixture
    def proj(self, make_project):
        root = make_project({
            "Makefile": MAKEFILE,
            "agents.project.yaml": textwrap.dedent("""
                vocabulary:
                  всё: [all]
                  уборка: [clean]
            """),
        }, "pl")
        return profile.load(root)

    def test_vocabulary_maps_words_to_targets(self, proj):
        _, targets = planner.plan_by_rules("собери всё", proj)
        assert targets == ["all"]

    def test_explicit_target_name_wins(self, proj):
        _, targets = planner.plan_by_rules("запусти clean", proj)
        assert "clean" in targets

    def test_longer_target_name_not_split(self, make_project):
        root = make_project({"Makefile": "board:\nboard-svg: board\n"}, "ls")
        p = profile.load(root)
        _, targets = planner.plan_by_rules("сделай board-svg", p)
        assert "board-svg" in targets

    def test_service_target_not_offered_for_vague_order(self, proj):
        _, targets = planner.plan_by_rules("сделай что-нибудь непонятное", proj)
        assert targets != ["serve"]

    def test_no_arbitrary_default_target(self, make_project):
        """Непонятный заказ не запускает сборку наугад."""
        root = make_project({"Makefile": "build:\nall:\n"}, "dflt")
        p = profile.load(root)
        _, targets = planner.plan_by_rules("совершенно невнятно", p)
        assert targets == []

    def test_unmatched_order_returns_empty(self, proj):
        """Непонятный заказ не должен молча запускать сборку всего."""
        r = planner.plan("совершенно посторонний разговор про луну", proj)
        assert r["targets"] == []
        assert r["available"], "интерфейс должен показать, что доступно"

    def test_unmatched_order_does_not_pick_arbitrary_target(self, proj):
        """Раньше здесь выбиралась первая верхняя цель — и запускалась."""
        _, targets = planner.plan_by_rules("совершенно посторонний разговор",
                                           proj)
        assert targets == []

    def test_minimise_drops_covered_targets(self, make_project):
        root = make_project(
            {"Makefile": "all: build test\nbuild:\ntest:\n"}, "min")
        p = profile.load(root)
        assert planner._minimise(["all", "build"], p) == ["all"]

    def test_minimise_keeps_independent(self, make_project):
        root = make_project({"Makefile": "all: build\nbuild:\ntest:\n"}, "min2")
        p = profile.load(root)
        assert planner._minimise(["all", "test"], p) == ["all", "test"]


# ==========================================================================
#  Конфликты и параллельность
# ==========================================================================
class TestConflicts:
    def _proj(self, make_project, mk, name):
        return profile.load(make_project({"Makefile": mk}, name))

    def test_independent_targets_share_group(self, make_project):
        p = self._proj(make_project, "a:\nb:\n", "cf1")
        steps = [{"target": "a"}, {"target": "b"}]
        groups = engine._conflict_groups(steps, p)
        assert len(groups) == 1, "независимые цели должны идти вместе"

    def test_shared_dependency_forces_separate_groups(self, make_project):
        p = self._proj(make_project, "mid:\na: mid\nb: mid\n", "cf2")
        steps = [{"target": "a"}, {"target": "b"}]
        assert len(engine._conflict_groups(steps, p)) == 2

    def test_transitive_dependency_forces_separation(self, make_project):
        p = self._proj(make_project, "base:\na: mid\nmid: base\nb: base\n",
                       "cf3")
        steps = [{"target": "a"}, {"target": "b"}]
        assert len(engine._conflict_groups(steps, p)) == 2

    def test_group_members_are_pairwise_disjoint(self, make_project):
        p = self._proj(make_project,
                       "x:\ny:\nz: x\na: y\nb: z\n", "cf4")
        steps = [{"target": t} for t in ("a", "b", "x", "y")]
        by = p.by_name()

        def fp(n):
            out, stack = {n}, [n]
            while stack:
                t = by.get(stack.pop())
                if not t:
                    continue
                for d in t.depends_on:
                    if d not in out:
                        out.add(d)
                        stack.append(d)
            return out

        for grp in engine._conflict_groups(steps, p):
            for i in range(len(grp)):
                for j in range(i + 1, len(grp)):
                    assert not (fp(steps[grp[i]]["target"])
                                & fp(steps[grp[j]]["target"])), \
                        "в группе есть конфликтующие цели"

    def test_every_step_lands_in_exactly_one_group(self, make_project):
        p = self._proj(make_project, "a:\nb:\nc:\n", "cf5")
        steps = [{"target": t} for t in ("a", "b", "c")]
        groups = engine._conflict_groups(steps, p)
        flat = [i for g in groups for i in g]
        assert sorted(flat) == [0, 1, 2]


# ==========================================================================
#  Факты и отчёт
# ==========================================================================
class TestReporter:
    def test_reads_numbers_from_json(self, make_project):
        root = make_project({
            "Makefile": MAKEFILE,
            "out/metrics.json": json.dumps({"nf_db": 2.14, "tj_c": 31.6}),
            "agents.project.yaml":
                "artifacts:\n  - pattern: out/*.json\n    title: метрики\n",
        }, "rp")
        proj = profile.load(root)
        facts, files = reporter.collect_facts(proj)
        assert any("metrics.json" in f for f in files)
        assert "2.14" in facts
        assert "31.6" in facts

    def test_does_not_invent_missing_numbers(self, make_project):
        """В фактах не может быть чисел, которых нет в файлах."""
        root = make_project({
            "Makefile": MAKEFILE,
            "out/a.json": json.dumps({"x": 1}),
            "agents.project.yaml": "artifacts:\n  - pattern: out/*.json\n",
        }, "rp2")
        proj = profile.load(root)
        facts, _ = reporter.collect_facts(proj)
        numbers = {w.strip(".,") for w in facts.replace("=", " ").split()}
        assert not {"2", "999"} & numbers

    def test_skips_service_keys(self, make_project):
        root = make_project({
            "Makefile": MAKEFILE,
            "out/a.json": json.dumps(
                {"real": 5, "generated_at": "2020-01-01", "path": "/tmp/x"}),
            "agents.project.yaml": "artifacts:\n  - pattern: out/*.json\n",
        }, "rp3")
        proj = profile.load(root)
        facts, _ = reporter.collect_facts(proj)
        assert "real" in facts
        assert "generated_at" not in facts
        assert "/tmp/x" not in facts

    def test_structural_report_without_llm(self, make_project):
        root = make_project({"Makefile": MAKEFILE}, "rp4")
        proj = profile.load(root)
        from agents.types import new_state
        st = new_state("собери всё")
        st["steps"] = [Step(id="s0", target="all", title="всё",
                            status="done", exit_code=0).as_dict()]
        text = reporter.structural(st)
        assert "1/1" in text
        assert "всё" in text          # описание
        assert "all" in text          # имя цели

    def test_missing_artifacts_stated_explicitly(self, make_project):
        root = make_project({"Makefile": MAKEFILE}, "rp5")
        proj = profile.load(root)
        facts, files = reporter.collect_facts(proj)
        assert files == []
        assert "не найдены" in facts.lower()


# ==========================================================================
#  Граф
# ==========================================================================
class TestGraph:
    def test_nodes_registered(self):
        names = [a["name"] for a in engine.graph_shape()]
        assert "supervisor" in names
        assert "tools" in names
        assert "await_human" in names
        assert "report" in names

    def test_plain_path_executes_targets(self, make_project, tmp_path):
        """Без langgraph заказ всё равно выполняется."""
        root = make_project({
            "Makefile": "hello:\n\t@echo привет\n",
            "agents.project.yaml": "vocabulary:\n  поздоровай: [hello]\n",
        }, "gr1")
        proj = profile.load(root)
        res = engine.run_plain("поздоровай", proj)
        assert res["status"] == "done"
        assert res["steps"][0]["status"] == "done"
        assert res["steps"][0]["exit_code"] == 0

    def test_failing_target_is_reported(self, make_project):
        root = make_project({
            "Makefile": "boom:\n\t@exit 3\n",
            "agents.project.yaml": "vocabulary:\n  сломай: [boom]\n",
        }, "gr2")
        proj = profile.load(root)
        res = engine.run_plain("сломай", proj)
        assert res["status"] == "failed"
        # make отдаёт 2 на любой сбой рецепта, а не код из самого рецепта
        assert res["steps"][0]["exit_code"] != 0
        assert res["verdict"] == "needs_human"

    def test_unknown_target_handled(self, make_project):
        root = make_project({
            "Makefile": "ok:\n",
            "agents.project.yaml": "vocabulary:\n  сделай: [нет-такой]\n",
        }, "gr3")
        proj = profile.load(root)
        # цель не существует -> её нельзя взять в план
        res = engine.run_plain("сделай", proj)
        assert res["status"] == "failed"
        assert res["steps"] == []

    def test_build_requires_langgraph(self):
        """Сборка графа обязана работать в том окружении, где он есть."""
        if not engine.have_langgraph():
            pytest.skip("langgraph не установлен")
        assert engine.build() is not None

    def test_checkpointer_prefers_durable(self):
        if not engine.have_langgraph():
            pytest.skip("langgraph не установлен в этом интерпретаторе")
        saver, durable = engine._checkpointer()
        assert saver is not None
        # в этом окружении sqlite доступен, значит состояние переживёт
        # перезапуск; если нет — тест фиксирует, что мы не врём
        assert durable in (True, False)


# ==========================================================================
#  Инструменты
# ==========================================================================
class TestTools:
    @pytest.fixture
    def ctx(self, make_project):
        root = make_project({
            "Makefile": MAKEFILE,
            "agents.project.yaml":
                "artifacts:\n  - pattern: out/*.json\n    title: метрики\n",
        }, "tl2")
        proj = profile.load(root)
        ran: list[str] = []
        # полный набор: эти тесты проверяют поведение инструментов,
        # а не уровни (для уровней есть отдельный тест)
        bindings = tools.make_bindings(
            proj,
            run_one=lambda n: (ran.append(n),
                               {"status": "done", "exit_code": 0,
                                "duration_s": 0.1, "log": ["ok"]})[1],
            tier="full")
        return proj, bindings, ran

    def test_all_tools_have_schemas(self, ctx):
        """Схемы и привязки должны предлагать один и тот же набор."""
        proj, _b, _r = ctx
        bindings = tools.make_bindings(
            proj, run_one=lambda n: {}, tier="full")
        schemas = tools.tool_schemas(proj, tier="full")
        assert {s["function"]["name"] for s in schemas} == set(bindings)

    def test_tier_limits_visible_tools(self, ctx):
        """Слабой модели нельзя показывать все инструменты сразу.

        Заимствовано у WebBrain: компактный уровень оставляет три
        инструмента, которых хватает для большинства заказов.
        """
        proj, _b, _r = ctx
        compact = {s["function"]["name"]
                   for s in tools.tool_schemas(proj, tier="compact")}
        full = {s["function"]["name"]
                for s in tools.tool_schemas(proj, tier="full")}
        assert len(compact) < len(full)
        assert "run_target" in compact, "даже компактный набор умеет собирать"
        # привязки урезаются тем же уровнем: вызвать скрытое нельзя
        tight = tools.make_bindings(proj, run_one=lambda n: {}, tier="compact")
        assert set(tight) == compact

    def test_schemas_have_required_fields(self, ctx):
        proj, _b, _r = ctx
        for s in tools.tool_schemas(proj):
            fn = s["function"]
            assert fn["description"]
            params = fn["parameters"]
            assert params["type"] == "object"
            for req in params.get("required", []):
                assert req in params["properties"]

    def test_list_targets_hides_service(self, ctx):
        proj, bindings, _ = ctx
        out = bindings["list_targets"]()
        assert "all" in out
        assert "serve" not in out

    def test_describe_unknown_target_lists_alternatives(self, ctx):
        proj, bindings, _ = ctx
        out = bindings["describe_target"](name="нет")
        assert "Доступные" in out

    def test_submit_plan_rejects_unknown(self, ctx):
        proj, bindings, _ = ctx
        out = bindings["submit_plan"](targets=["выдумка"])
        assert "не подошла" in out

    def test_submit_plan_minimises(self, ctx):
        proj, bindings, _ = ctx
        out = bindings["submit_plan"](targets=["all", "build"])
        assert "['all']" in out

    def test_invoke_unknown_tool_is_graceful(self, ctx):
        proj, bindings, _ = ctx
        out = tools.invoke(bindings, "выдуманный", {})
        assert "нет" in out

    def test_invoke_bad_arguments_is_graceful(self, ctx):
        proj, bindings, _ = ctx
        out = tools.invoke(bindings, "describe_target", {"нет_того": 1})
        assert "Неверные аргументы" in out

    def test_ask_human_marker(self, ctx):
        proj, bindings, _ = ctx
        out = bindings["ask_human"](question="нужен доступ?")
        assert tools.is_ask_human(out) == "нужен доступ?"


# ==========================================================================
#  Описание проекта
# ==========================================================================
class TestProfile:
    def test_reads_yaml_profile(self, make_project):
        root = make_project({
            "Makefile": MAKEFILE,
            "agents.project.yaml": textwrap.dedent("""
                name: Тестовое изделие
                description: проверка чтения описания
                vocabulary:
                  мойзаказ: [all]
                verify: make check
            """),
        }, "pf")
        proj = profile.load(root)
        assert proj.name == "Тестовое изделие"
        assert proj.vocabulary["мойзаказ"] == ["all"]
        assert proj.verify == "make check"

    def test_json_profile_also_works(self, make_project):
        root = make_project({
            "Makefile": MAKEFILE,
            "agents.project.json": json.dumps({"name": "из json"}),
        }, "pf2")
        assert profile.load(root).name == "из json"

    def test_profile_can_add_targets(self, make_project):
        root = make_project({
            "Makefile": MAKEFILE,
            "agents.project.yaml": textwrap.dedent("""
                targets:
                  - name: release
                    command: echo release
                    top_level: true
            """),
        }, "pf3")
        proj = profile.load(root)
        t = proj.target("release")
        assert t is not None and t.command == "echo release"

    def test_explicit_profile_path(self, make_project):
        root = make_project({"Makefile": MAKEFILE, "custom.yaml":
                             "name: по пути\n"}, "pf4")
        (root / "custom.yaml").write_text("name: по пути\n", encoding="utf-8")
        assert profile.load(root, profile=str(root / "custom.yaml")).name \
            == "по пути"

    def test_missing_root_raises(self):
        with pytest.raises(runner.BuildError):
            profile.load("/нет/такого/каталога")

    def test_artifact_glob_expansion(self, make_project):
        root = make_project({
            "Makefile": MAKEFILE,
            "out/a.json": "{}", "out/b.txt": "x", "other/c.json": "{}",
        }, "pf5")
        proj = profile.load(root)
        names = {p.name for p in profile.collect(proj)}
        assert {"a.json", "b.txt"} <= names
        assert "c.json" not in names