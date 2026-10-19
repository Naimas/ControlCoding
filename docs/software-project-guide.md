# Documents and views for a software project

This is a recommended organization, not a requirement to create a separate file
for every row. Small projects can combine sections. Each document should identify
its purpose, status, owner, revision and the sources or decisions it depends on.

## Document roles

| Role | What it answers | Typical contents |
| --- | --- | --- |
| Product requirements | What must the product do, and for whom? | Users, use cases, scope, exclusions, acceptance criteria and measurable quality targets |
| Architecture / design | How will the system work? | Context, modules/services, responsibilities, data, interfaces, runtime interactions, deployment, security and quality tradeoffs |
| Interface and data contracts | How do parts communicate? | APIs, events, schemas, validation, error handling and compatibility |
| Architecture decisions (ADRs) | Why was this approach chosen? | Context, options, chosen solution, consequences and superseded decisions |
| Implementation plan | How do we build and verify it? | Objectives, phases, deliverables, tasks, owners, prerequisites, blockers and completion criteria |
| Verification plan and evidence | What supports the claim that it works? | Test strategy, scenarios, results tied to source revisions and explicit acceptance |
| Operating guide | How is it installed and maintained? | Setup, configuration, deployment, monitoring, backup, recovery and rollback |
| Risks and technical debt | What remains uncertain or costly? | Impact, priority, mitigation, owner and review trigger |
| Reference material | What informed the work? | External specifications, manuals, research, supplied examples and raw source documents |
| Conversations and session records | What was discussed? | Exchanges, unresolved questions, decisions proposed and links to accepted records |
| Working instructions | How should contributors or agents operate? | AGENTS.md, contribution rules, commands, boundaries and approval requirements |

An architectural document describes the intended system; an implementation plan
organizes work; evidence records what was checked. A chat proposing a solution is
not automatically an accepted decision. A README can link to all of these without
being the design or the plan itself. Preserve original titles and source paths;
labels should explain the document's role instead of replacing its identity.

For architecture coverage, [arc42](https://arc42.org/overview/) provides an
adaptable structure spanning goals, constraints, context, building blocks,
runtime, deployment, decisions, quality and risks. [ADRs](https://adr.github.io/)
record significant decisions and their rationale.

## Visual questions

| View | Question answered |
| --- | --- |
| Code status grid | Which code units have current evidence, are active, blocked or still unverified? |
| Folder/file tree | Where does the implementation live? |
| Architecture diagram | Which systems, services and components exist, and what are their responsibilities? |
| Runtime / sequence diagram | What happens, and in what order, during a specific interaction? |
| Data model / ER diagram | What information is stored and how is it related? |
| Deployment diagram | Where do executable parts run and communicate? |
| Work breakdown hierarchy | Which subtasks and deliverables belong to each objective? |
| Dependency roadmap | What must finish first, what can run in parallel and what blocks progress? |
| Calendar Gantt | When could estimated work start and finish, given dates, durations and dependencies? |
| Traceability matrix | Which requirements link to design, tasks, code, tests and acceptance? |
| Document graph | Which source documents, decisions, chats and evidence support one another? |

[C4](https://c4model.com/diagrams) offers architecture views at increasing detail:
context, containers (applications/data stores, not necessarily Docker), components
and code, plus dynamic and deployment diagrams. Use only useful levels. A folder
tree alone does not establish logical architecture or service boundaries.

## Current panel integration

Status opens on the code grid after explicit source observation. Its large view
buttons expose Code grid, Code structure (folders/files), Work roadmap and Plans
& evidence. Source reading and detailed checks are under Sources, checks & map
settings. The navigation remains visible on page scroll; evidence column headings
and work-plan headings stay visible inside their scrolling views.

Work roadmap reuses [the recorded work plan](work-schedule.md). Process order is
the default: numbered dependency steps, actual task names, hierarchy and arrows.
These steps are not dates or inferred business phases. Actual objective/phase
labels come from records. Selecting the view reads an enabled archive; it never
enables memory or generates a plan. Empty or stale sources remain explicit.

Code presence is not verified completion. Equal squares do not imply equal work.
A useful traceability chain is requirement -> design/decision -> task -> code ->
test evidence -> acceptance, with current source revisions at each link. The list
above describes useful software views; it does not claim every diagram editor is
implemented in ControlCoding.
