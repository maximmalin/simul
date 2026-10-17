# analog-agents (upstream reference)

Источник: https://github.com/Arcadia-1/analog-agents (MIT, Arcadia-1).
Скопировано без изменений: `prompts/` (шаблоны ролей librarian/architect/
designer/verifier), `shared-references/` (handoff-contracts, effort-contract,
review-protocol, checklist-schema, wiki-schema), `checklists/` (common.yml и
топологические чек-листы). Лицензия MIT заявлена в README upstream (файла
LICENSE в клоне нет).

Как используется в Krystal Radio: эталон **методологии агентской линии**
(разделение ролей и прав, контракты передачи, ревью до компиляции, уровни
усилия, чек-листы приёмки, отчёт верификатора «измерено / цель / запас»).
Наши роли (`backend/agents/roles/*.md`) — адаптация этих шаблонов под
KiCad/tscircuit-конвейер, без доменного содержания из аналоговой схемотехники.
