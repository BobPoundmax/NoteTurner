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

- Finance questions should prefer Hollihop entities: `balance`, `payment`,
  `edunit_student`, `edunit`.
- Group and scheduling questions should prefer `edunit` and `edunit_student`.
- Marketing questions should prefer `study_request`, `lead`, `student`.
- If no relevant CRM context is found, the bot should suggest refreshing CRM
  instead of hallucinating.

## Freshness strategy

- Default: answer from local indexed data.
- Manual refresh: `/admin` -> `Загрузить CRM`.
- Targeted admin refresh in private chat:
  - `обнови CRM`
  - `обнови платежи`
  - `обнови группы`
- Do not perform free-form live API lookup on every question; use limited,
  intent-driven refresh to avoid slow and brittle answers.
