# ControlCoding Observer: Windows portable candidate

This is an **unsigned local candidate awaiting review**, not a published release.
It contains the optional Windows x64 observer and Project Map, a separate Core
source tree and Electron 44.0.0. Python 3.11 or newer must already be installed.
Git is needed for Git-aware canonical evidence; no provider, account or model is
required. No runtime is downloaded or installed by this package.

## Start

Extract the ZIP to a new empty folder on an ordinary local Windows filesystem.
In PowerShell, from that folder:

```powershell
.\Launch-Observer.ps1 -Python 'C:\path\to\python.exe' -Check
.\Launch-Observer.ps1 -Python 'C:\path\to\python.exe'
```

Use an installed interpreter's actual absolute path. PowerShell's execution
policy still applies; the launcher does not weaken machine policy. `-Check`
verifies the complete file inventory/hashes and Python version without launching
the app or creating a profile. Checksums detect drift; an unsigned manifest is
not a publisher signature or proof against replacing both files and manifest.

The default profile is `%LOCALAPPDATA%\ControlCoding Observer`, outside the
package. `-Profile` can select a separate absolute ordinary folder. The selected
adopter folder must be physically disjoint from the profile: neither may contain
the other, including equality. This is checked by the launcher (also with `-Check`)
and by the app before startup writes and each later project selection. Existing
8.3 aliases are resolved; UNC/device namespace spellings and linked paths are
unsupported and refused. A rejected selection keeps the current project.
Choose a project with **Open project**. Preview the Project Map scope, observe it,
and optionally select design documents. Controls/evidence and quality analysis
have their own explicit previews. Reviewed mappings are saved only after a
concrete change preview, in the selected project's map definition.

Enable automatic refresh after observing the map. File/Git metadata hints and
30-second reconciliation update approved scopes while the app runs, including
in the tray. Changed scope needs another preview/read. A commit does not certify
completion. History is bounded and session-only. The development Checks page can run explicitly reviewed project commands.

The observer includes three linked views, code-square explanations, reviewed
mapping persistence, canonical control projections and static quality/dependency
signals. This candidate also packages guided configuration drafts, manual AI
proposal import and reviewed Core apply (`core/docs/panel-configuration.md`),
reviewed portable ControlWork archive setup and knowledge workflows
(`core/docs/controlwork-management.md`), and the Core install/project wizard,
governed Dev Plane setup, explicit Checks and advisory Ollama/OpenAI conversations
with opt-in panel exchange archival (`core/docs/panel-execution.md`). The packaged
source and build also include the document reader, Markdown-backed conversation
view, role configuration, manual handoff and knowledge graph/topic views. Their
presence in this candidate is not an independent adoption or quality acceptance.
Autonomous code editing, signing and live host attestation remain separate.
This does not change the previously accepted RC3 archive.
The saved Agents tab opens AI & Sessions. Every execution has an explicit preview.

## Update, move and remove

New builds also accept `-External`, optionally with `-Source` and `-Workspace`,
for a separate read-only-source knowledge/evidence window. It has no project
commands, hooks, setup/apply, provider execution or semantic/OCR workers. Its
workspace and desktop profile must be physically separate from the source and
package. See `core/docs/external-mode.md` for capabilities and preservation limits.
Previously published archives are not changed by this source addition.

Exit the panel through **Exit panel** or its tray menu before changing its files.
Extract a later candidate into a **different empty folder**, verify it, and launch
it with the same external profile. The old folder remains available for rollback;
there is no in-place updater or profile migration in this candidate. Never extract
over an adopter repository or an existing package. Moving the extracted package
while closed is supported because launch paths are relative to its own folder.

To remove the app, exit it and delete only the extracted package folder you chose.
The launcher performs no deletion. Profiles and adopter files remain outside that
folder; saved map definitions remain owned by each project. Delete a profile only
as a separate deliberate action. Removing the app does not uninstall Python, Git
or user-owned models and does not modify the Core checkout used elsewhere.

## Contents and build

`core/` uses the unchanged canonical Core source allow/deny contract. The optional
`app/`, `runtime/` and `source/ui/` are outside that Core distribution. UI build
dependencies are React/React DOM 19.3.0, scheduler 0.28.0 and esbuild 0.28.2;
the static JS/TS parser is Babel 7.29.7. The bundled Markdown tokenizer is the
vendored markdown-it 12.3.2 standalone build, pinned by source SHA-256. Its MIT
license is in both `app/vendor/` and `source/ui/vendor/`; the provenance note is
in `source/ui/vendor/README.md`.
Runtime Chromium notices, Electron license, React/React DOM/scheduler licenses,
the Babel parser license and the markdown-it license are retained. Core
license, NOTICE and trademark terms remain in `core/`. No new licensing grant is
implied. `app/BUILD-INFO.json` records dependencies, input source hashes and output
hashes. `MANIFEST.json` enumerates the artifact; the delivery report records ZIP SHA-256.

Maintainers build outside the source checkout with the existing `build.cjs`, then
run `package-observer.py` with `--source`, `--source-manifest`, `--build`,
`--runtime` and a fresh external `--output` directory. The explicit source manifest
is `{ "schemaVersion": 1, "files": { "relative/path": "sha256..." } }` for the
reviewed live source candidate. Product Git HEAD is not used as a substitute for
live source bytes. Core-required and exact UI source entries must be present;
local/private paths are not copied. No broad repository clone is performed.
To rebuild from the distributed source, first reconstruct a new external source
root containing the contents of `core/` and the `ui/` directory from `source/`.
The packager imports the canonical release owner from that root's `scripts/`.

The packager rejects extra build/runtime files, links, hardlinks, changed hashes,
existing output paths and bounded inventory/size violations. It resolves existing
physical directory ancestors and requires output paths
disjoint from source, build and runtime in both directions. UNC/device namespace
spellings are refused, not compared as ordinary drive paths. It rechecks bytes
before copying and emits a deterministic ZIP. On failure a partial output may
remain for diagnosis; it never deletes or overwrites a pre-existing directory.
Run builders on trusted, quiescent local input/output directories: this is not an
adversarial concurrent-filesystem sandbox. There is no signing, installer service,
registry integration or automatic update channel. Publication, independent review,
other-platform support and independent user acceptance require separate evidence.
