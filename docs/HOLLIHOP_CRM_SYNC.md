# Hollihop CRM Sync Matrix

## Current sync model

NoteTurner stores Hollihop data in `raw_records`, then vectorizes it into
`doc_chunks` for retrieval. Sync is incremental where the API supports cursors
and snapshot-based where it does not.

## Entity matrix

| Record type | Hollihop endpoint | Value for assistant | Freshness model | Important fields / nested objects |
|---|---|---|---|---|
| `lead` | `GetLeads` | Sales funnel, source attribution, handoff into students | `lastUpdatedFrom` + `Now` cursor | `Status`, contacts, `Agents`, `Assignees`, `ExtraFields`, `StudentClientId` |
| `student` | `GetStudents` | Student profile, lifecycle, linked study requests | `lastUpdatedFrom` + `Now` cursor | `Status`, contacts, `LearningTypes`, `Disciplines`, `Agents`, `Assignees`, `ExtraFields`, `StudyRequests` |
| `payment` | `GetPayments` | Payment history, due dates, states, payment methods | `createdFrom` cursor | `State`, `Value`, `PaidDate`, `RequiredPaidDate`, `ClientName`, `PaymentMethodName` |
| `study_request` | `GetStudyRequests` | Marketing attribution and inbound demand context | `from` cursor by `Created` | `Status`, `Location`, `Office`, `Discipline`, `Teacher`, `Utm`, `LeadId`, `StudentClientId` |
| `edunit` | `GetEdUnits` | Groups, schedule, responsible staff, fiscal info | `lastUpdatedFrom` + `Now` cursor | `Discipline`, `Level`, `LearningType`, `Assignee`, `ScheduleItems`, `Days`, `FiscalInfo`, `ExtraFields` |
| `edunit_student` | `GetEdUnitStudents` | Student-to-group links, contracts, payers | snapshot | `Status`, `StudyUnits`, `StudentAgents`, `StudentExtraFields`, `Payers`, `Days` |
| `balance` | `GetBalances` | Debt, residual value, aggregate balance, payer health | snapshot by `balanceDate` | `BalanceMoney`, `DebtMoney`, `StudyBalance`, `EdUnitsBalances`, debt flags |

## Retrieval strategy

- Finance questions should prefer financial chunk types:
  `balance`, `payment`, `group_payer`, `group_fiscal`.
- Group and scheduling questions should prefer schedule-aware chunk types:
  `schedule_item`, `schedule_day`, then `edunit` and `edunit_student`.
- Marketing questions should prefer `study_request`, `lead`, `student`.
- If no relevant CRM context is found, the bot should suggest refreshing CRM
  instead of hallucinating.

## Vector chunk model

- `lead`, `student`, `study_request`, `payment`, `balance` remain vectorized
  largely as before.
- `edunit` now produces:
  - non-financial summary chunks (`edunit`);
  - non-financial schedule chunks (`schedule_item`, `schedule_day`);
  - financial chunks for fiscal metadata (`group_fiscal`).
- `edunit_student` now produces:
  - non-financial summary chunks (`edunit_student`);
  - non-financial day/schedule chunks (`schedule_day`);
  - financial payer chunks (`group_payer`).
- This split allows non-admins to ask about groups and lessons without opening
  access to fiscal or payer details.

## Freshness strategy

- Default: answer from local indexed data.
- Manual refresh: `/admin` -> `Загрузить CRM`.
- Targeted admin refresh in private chat:
  - `обнови CRM`
  - `обнови платежи`
  - `обнови группы`
- Admins can also ask plain-language count questions like
  `сколько у тебя данных в векторной базе по лидам и расписанию`, which should
  be answered from DB counts directly instead of RAG.
- Do not perform free-form live API lookup on every question; use limited,
  intent-driven refresh to avoid slow and brittle answers.
