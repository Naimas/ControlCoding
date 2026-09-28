# ControlWork portable archive management

This optional Windows desktop development feature is separate from the frozen
RC3 observer release. Open **ControlWork → Manage project memory**. Every action
requires a content preview followed by **Save and refresh memory**. Opening the
page or previewing changes creates no project files.

## Supported actions

- **Initialize archive** creates missing `CONTROLWORK.md`, `PROJECT.md`, the Core
  `.controlwork/config.json`, categories registry and portable directory markers.
  Existing files are preserved. A custom base-document layout requires the CLI.
- **Import documents** uses a native picker for up to eight ordinary Markdown,
  UTF-8 text, DOCX, XLSX or PDF files inside the selected project. Hidden paths
  and linked files are refused. Text sources are limited to 32 KiB; rich sources
  to 1 MiB and extracted text to 32 KiB. The source remains unchanged; a Core
  Markdown record contains its text and relative provenance. Identical imports
  in the same area reuse the existing record; changed source content creates a
  new record without deleting history. Conflicting edits block the operation.
  Rich imports include the source hash, extractor metadata and coverage warnings
  in the preview and saved record. DOCX is text-only; XLSX is a bounded summary
  of five sheets and 40 rows per sheet; PDF text is best-effort, without layout
  analysis or a guarantee of physical page mapping. Encrypted files and excessive
  archive/stream expansion are rejected.
  Scanned PDFs can use an adjacent `filename.pdf.ocr.json` sidecar in the existing
  ControlWork OCR shape, with `pages` and a matching source SHA-256. No OCR program
  is installed or executed by this import. The preview binds both files; changed
  sidecars require another preview. OCR accuracy remains subject to human review.
- **Capture knowledge** saves a title and body in a selected canonical memory
  area. Its lifecycle is `captured`, not independently verified.
- **Record session** saves a completed manual summary with decisions and
  follow-ups in the existing Core session JSON format. It does not collect chat
  conversations automatically. A selected text chat export can be imported as
  a document; that does not reconstruct structured conversation history.
- **AI & Sessions** can automatically archive each reviewed outgoing request and
  visible answer, when explicitly enabled before sending. These are separate
  completed session records with user/assistant notes, not hidden reasoning or
  silently collected host history. See [provider workflow](panel-execution.md).

Successful saves reread the documented observer scope and update documents,
sessions, connections and retrieval. A save receipt and the read result are
separate: a later read failure does not undo a successful save. External edits
still need an explicit refresh. The existing 96-record observer limit applies
to additions; the management operation refuses an over-budget archive.

This is portable Project Plane initialization, not full CLI initialization:
there is no project scan, Dev Plane SQLite database, ingestion index, provider
request, lifecycle promotion, graph approval or automatic source synchronization.
ControlCoding Setup remains a separate workflow. Standalone ControlWork is not
modified.

## Write boundary

`scripts/cc_controlwork_manage.py` reuses Core context, category, capture and
session renderers. The main process owns the selected root, native selected
paths, operation ID, timestamp and pending approval. Narrow versioned bridge
operations are `work_manage_preview_v1` and `work_manage_commit_v1`; the renderer
has no general file-write or command API. Editing a form or switching projects
invalidates its preview. Project switches and normal exit are blocked during
the commit, and late responses cannot repopulate a different project.

The preview binds selected input bytes, archive directory membership, trusted
implementation sources and intended output. Commit regenerates that preview
and uses the shared Windows transaction writer with exclusive retained handles.
Existing content is never overwritten by these actions. Normal failures roll
back owned writes. An interrupted transaction leaves
`.controlcoding/panel-setup-transaction.json` for diagnosis; subsequent writes
refuse to continue until reviewed recovery. Newly created empty directories can
remain after failure. This journal directory does not imply Core setup was
applied. Concurrent hostile mount/junction replacement is outside the supported
ordinary-local-filesystem model.

Content previews are limited to 8,000 characters and identify truncation, size
and SHA-256. Text is rendered inertly; source bodies are not stored in activity
or preferences. Imported records can contain sensitive material: the native
selection and concrete preview identify exactly what will be persisted.

## Verification

`tests/test_cc_controlwork_manage.py` covers preservation, Core read compatibility,
repeat imports, stale previews, malformed input, path/link limits and record
budgets. `ui/tests/work-manage-unit.cjs` covers protocol and state transitions.
`ui/tests/work-manage-desktop.cjs` exercises actual Electron mouse/keyboard input,
isolated Python writes, refreshed views, inert text, stale-source refusal and
all forms at 320 CSS pixels. Fixtures and profiles belong in an external
workbench; the development checkout is not initialized by these tests.
