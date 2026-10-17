# EEsizer (upstream reference)

Источник: https://github.com/eelab-dev/EEsizer (MIT), статья
arXiv:2509.25510 / IEEE TCAS-I: «EEsizer: LLM-Based AI Agent for Sizing of
Analog and Mixed-Signal Circuit» (Chang Liu, Danial Chitnis).

Скопировано без изменений: `initial_circuit_netlist/` (эталонные деки),
`variation/` (проверка устойчивости разбросом), `figures/`, README, LICENSE.

Что взято в нашу линию: **метод петли**, а не аналоговые модели.
EEsizer держит LLM в цикле с симулятором (функциональный вызов ngspice),
где измеренные метрики с целями и история результатов сходят в следующий
промпт — это даёт in-context learning по истории, а не по памяти модели.
У нас тот же цикл применён на уровне номиналов компонентов печатной платы,
а не транзисторов AMS: стадия `sizing` (backend/agents/stages.py, роль
`roles/sizer.md`). Модели приборов каркас не выдумывает — агент берёт их
у изготовителей (см. решение в tools/ngspice_measure.py).
