# Contained project storage

For a mode with no product writes or hooks, see [External read-only mode](external-mode.md).
It has a separate entry point and a deliberately smaller execution surface.

ControlCoding can keep its managed configuration, documents, installed helpers,
Dev memory and embedded ControlWork records under `<project>/cc/`. The application
and Git root remain the original project directory. The Core engine still runs
from its external checkout or desktop distribution.

An ordinary application folder named `cc` is never adopted automatically. A
validated `cc/layout.json` selects contained storage. Without that marker, existing
projects retain their legacy layout. Invalid metadata fails closed.

## New projects

Run these commands from the external ControlCoding checkout, using the actual
application directory as `--project-root`:

```powershell
python scripts/cc.py layout init --project-root "C:\Projects\Example"
python scripts/cc.py layout init --project-root "C:\Projects\Example" --apply
python scripts/cc.py setup --project-root "C:\Projects\Example"
python scripts/cc.py layout status --project-root "C:\Projects\Example"
```

The first command previews the storage activation. The second creates only the
namespace and marker; setup subsequently installs/configures the selected Core
features. It does not move existing application files or enable a model.

In the desktop Setup page, open **Full Core installation and project setup**,
review **Contain CC in cc/**, and confirm it before saving drafts or installing
Core. Then use the configuration workflow or installation wizard normally.
The previews show actual destinations and the current storage location.

| Managed material | Contained location |
| --- | --- |
| Configuration, jobs, verification receipts | `cc/.controlcoding/` |
| Dev memory | `cc/.controlcoding/memory/` |
| Knowledge archive, wiki and conversations | `cc/.controlcoding/knowledge/` |
| Embedded portable Work records | `cc/.controlwork/` |
| Canonical context and generated Work base document | `cc/CONTROLCODING.md`, `cc/CONTROLWORK.md`, `cc/PROJECT.md` |
| CC status and planning documents | `cc/STATUS.md`, `cc/ROADMAP.md`, `cc/BUGS.md`, `cc/dev/` |
| Installed hooks and tools | `cc/hooks/`, `cc/tools/` |
| Default optional bridge and visual-test artifacts | `cc/.bridge/`, `cc/screenshots/` |
| Module lock declarations | `cc/.controlcoding/module-locks/<source-module>/.feature-lock.json` |

Module ownership patterns continue to address real application paths. Existing
application documents, explicitly selected sources and custom project base
documents are not relocated by source readers. Knowledge sources retain their
stable IDs where an unambiguous move can be reconciled; physical locators are
shown separately. Application `ROADMAP.md` and `cc/ROADMAP.md` can coexist.

## Integration exceptions

Hosts and Git discover some files outside `cc/`. The selected installation may
therefore create/update reviewed host instruction adapters (such as `AGENTS.md`),
host settings, Git hook stubs, `.gitignore`, or editor tasks. Foreign adapters and
hooks retain the existing ownership/conflict rules. Application source paths are
unchanged; `cc init-module` can still create the application module requested by
the user. Verification commands are project-defined and can have their own side
effects; contained storage is not an execution sandbox.
Explicitly chosen custom export/screenshot/shared-bridge destinations remain
user-selected paths; default CC-owned output locations use contained storage.

The managed ignore block excludes `/cc/`, without ignoring the application's
generic `dev`, `tools` or `hooks` directories. Do not place application code that
you intend to publish inside this private namespace. External desktop profiles
and explicitly selected backup paths remain outside the application repository.

## Existing installations

Migration is explicit and offline. Close the selected project's CC desktop,
workers and editing sessions first. Do not create the marker manually over a
legacy installation: that would select a different archive without migrating it.

The preview requires an unused external backup directory and an explicit list
of paths declared to be CC-owned. Do not select a whole `tools` or `dev` directory
if it also contains application files; individual managed files can be selected.
Inline module locks require their individual source-relative paths.

```powershell
python scripts/cc.py layout preview --project-root "C:\Projects\Example" --backup "C:\Backups\Example-cc-migration" --include .controlcoding --include CONTROLCODING.md --include hooks --include tools/fitness_check.py
```

Review the file inventory, helper refreshes, adapter changes and blockers. Add
the other CC-owned documents/archives actually present; the example is not a
universal complete selection. Customized generated adapters require explicit
reconciliation, not automatic replacement. Foreign `cc/`, unsafe paths, busy
databases and pending SQLite sidecars block migration.

The preview also checks a bounded subset of inline Markdown links and images.
Existing links that would point elsewhere after a move require reconciliation
before migration. Human text is not silently rewritten; unusual Markdown
extensions and external references still need review.

Run the same selection with `layout migrate`, `--apply` and
`--approval <approval_id-from-preview>`. Source changes invalidate that approval.
The operation preserves original bytes in the verified backup, stages contained
files, updates reviewed adapters, publishes the marker and removes only unchanged
selected originals. Known installed helpers are refreshed from the current Core;
their previous bytes remain in the backup. Missing bundled pack companions are
included in the preview and installed inside `cc/`; existing companions must be
explicitly included or reconciled. Empty application module directories
and unrelated files remain in place.

Interrupted migration leaves `cc/.migration.json`. Normal CC operations refuse
to proceed while that journal is present:

```powershell
python scripts/cc.py layout recover --project-root "C:\Projects\Example"
python scripts/cc.py layout recover --project-root "C:\Projects\Example" --apply
python scripts/cc.py layout recover --project-root "C:\Projects\Example" --apply --rollback
```

Inspection is read-only. Prepared/publication stages can resume; rollback requires
unchanged staged data and a verified backup. An interrupted copy or independently
modified destination requires inspection and recovery from the retained backup;
the command does not delete unverified files to force progress. After migration,
refresh indexes and rerun verification. Historical receipts do not establish
freshness under the new layout.

An interrupted atomic journal write can leave `cc/.migration.json.pending`.
Status marks recovery as required even if the main journal was not yet created.
Recovery inspection reports `journal-write-incomplete`; automatic resume and
rollback refuse that state. Preserve both journal files and the external backup,
and inspect its manifest before manual recovery. CC never deletes an uncertain
pending journal to force migration forward.

Containment does not authenticate the publisher or protect against hostile
concurrent filesystem/mount replacement. Source scopes, retention, provider
permissions and named-host delivery limitations remain unchanged.
