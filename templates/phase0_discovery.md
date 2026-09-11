# Phase 0: Architecture Discovery

> This template guides the AI through discovering your project's architecture
> before writing any code. The output is a first draft of `CONTROLCODING.md`.
>
> **How to use**: paste this into your first AI session, or include it in your
> rules file as a one-time instruction. The AI will follow the appropriate path
> and produce a `CONTROLCODING.md` draft for your review.

---

## Instructions for the AI

The user wants to set up ControlCoding for their project. Your task is to
discover the project's architecture and produce a first draft of `CONTROLCODING.md`.

**Ask the user which path applies:**

- **Path A**: "I have an idea for a new project" (no code exists yet)
- **Path B**: "I have an existing codebase or document to analyze"

Then follow the appropriate path below.

---

## Path A: From an idea (new project)

Ask these questions one at a time. Wait for each answer before proceeding.
Adapt follow-up questions based on the answers.

### Questions

1. **What does the software do?**
   Describe the purpose in 2-3 sentences. What problem does it solve?

2. **What is the stack?**
   Language, framework, key libraries. If undecided, describe the constraints
   (e.g., "needs to run in the browser", "must integrate with PostgreSQL").

3. **What are the main data entities?**
   What does the system store or manipulate? Examples: users, orders, documents,
   simulation states, sensor readings. List them with a one-line description each.

4. **What are the main operations?**
   What can users (or the system) do? Examples: create/read/update/delete records,
   run calculations, generate reports, process files. List the 5-10 most important.

5. **Where does data come from and where does it go?**
   External APIs, databases, file imports, user input. What is the authoritative
   source of truth? What is derived/computed?

6. **Are there hard constraints?**
   Performance requirements, security boundaries, regulatory compliance,
   external service limitations, real-time requirements.

7. **What must NEVER happen?**
   Think about catastrophic failures specific to the domain:
   - Finance: money appearing or disappearing
   - Medical: wrong patient data association
   - Simulation: non-deterministic results with same inputs
   - E-commerce: order total not matching line items
   - Any domain: data loss, silent corruption, security bypass

### Output

From the answers, produce:

1. **Module structure**: suggested folders and their responsibilities
2. **Zone classification**: which modules are stable, shared, features, workspace
3. **Architecture rules**: 3-5 rules that emerge from the constraints
4. **Domain invariants**: properties that must always hold (derived from question 7)
5. **`CONTROLCODING.md` draft**: a complete first draft following the template in
   `templates/CLAUDE.md.template`

Present the draft to the user and ask for corrections before finalizing.

---

## Path B: From an existing codebase or document

### If the user provides a codebase

Scan the project and analyze:

1. **Directory structure**: list top-level folders and their apparent purpose
2. **File inventory**: count files per folder, identify the largest files
3. **Dependency graph**: what imports what, where are the coupling points
4. **Entry points**: main files, route definitions, CLI handlers
5. **Data layer**: models, schemas, database access patterns
6. **Test coverage**: where tests exist, what they test, what is untested
7. **Hotspots**: files with many imports (heavily depended upon),
   large files (>300 lines), files that change frequently (git log)
8. **Implicit patterns**: naming conventions, error handling patterns,
   data flow direction

### If the user provides a document

Read the document and extract:

1. **System purpose**: what the software does
2. **Components**: modules, services, or subsystems described
3. **Data model**: entities, relationships, constraints
4. **Operations**: what the system must do (use cases, user stories, features)
5. **Non-functional requirements**: performance, security, scalability
6. **Constraints**: technology choices, integration requirements, regulations
7. **Risk areas**: anything described as "critical", "must not fail", "sensitive"

### Output

From the analysis, produce:

1. **Module map**: what exists (or should exist), what depends on what
2. **Zone classification proposal**:
   - **Stable candidates**: core models, auth, anything described as "audited" or
     "do not touch". For existing codebases: files unchanged for 3+ months with
     many dependents.
   - **Shared candidates**: utilities, helpers, anything used by 2+ modules.
     For existing codebases: files imported by 3+ other files.
   - **Features**: active development areas. For existing codebases: files changed
     recently and frequently.
   - **Workspace**: experiments, prototypes, scratch files. For existing codebases:
     files with "test", "draft", "tmp", "experiment" in the name or path.
3. **Architecture rules**: patterns already present (implicit rules to make explicit)
   plus rules needed to prevent identified risks
4. **Domain invariants**: properties that must hold, derived from the data model
   and constraints
5. **`CONTROLCODING.md` draft**: a complete first draft

Present the draft to the user and ask for corrections before finalizing.

---

## After Phase 0

Once the user approves the `CONTROLCODING.md` draft:

1. Save it as `CONTROLCODING.md` in the project root, then derive the host-native file your tool reads
2. The project is now at **Level 1 (Documented)** of ControlCoding
3. Next step: configure boundary hooks (see `docs/quick-start.md`, Step 2)

Phase 0 is a one-time process. The canonical project context evolves incrementally from here
through the normal commit ceremony (update `CONTROLCODING.md` when boundaries or rules
change).
