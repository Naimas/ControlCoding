# ControlCoding desktop development source

This branch preserves the optional desktop source and tests separately from the
Core distribution. It is development source, not a new binary or stable release.

## Reconstruct the development workspace

Use Core commit `b46db583048ce48ae1eeda461e79531edd4ff0bb` from
[ControlCoding Core](https://github.com/Naimas/ControlCoding/tree/b46db583048ce48ae1eeda461e79531edd4ff0bb).
Place this branch's `ui/` directory beside that Core checkout's `scripts/`,
`docs/` and `templates/` in a separate development workspace. Use the pinned
Core commit, rather than assuming that a later `master` is compatible.

Follow [ui/README.md](ui/README.md) and [ui/DISTRIBUTION.md](ui/DISTRIBUTION.md)
for the existing runtime requirements, explicit launcher paths and build steps.
The development launcher's defaults refer to the maintainer workstation; other
machines must supply their own installed runtimes, build output and profile paths.
No runtime, dependency, model, credential or local profile is included here.

The Core release contract excludes UI. Keep a clean Core checkout separately
when running Core release checks; a combined desktop development workspace is
intentionally not a Core-only source distribution.

## This snapshot

The source includes contained project storage, graph-guided original-evidence
retrieval, reviewed wiki synthesis, persistent findings and revision recovery.
See the pinned Core [graph/wiki guide](https://github.com/Naimas/ControlCoding/blob/b46db583048ce48ae1eeda461e79531edd4ff0bb/docs/graph-wiki.md)
and [contained storage guide](https://github.com/Naimas/ControlCoding/blob/b46db583048ce48ae1eeda461e79531edd4ff0bb/docs/contained-storage.md).
The experimental external mode is deferred and hidden from the ordinary welcome
screen; its explicit development entry points remain in the source.

Previously published portable ZIPs and the dated preview release are unchanged.
Source availability does not establish whole-product acceptance, AI answer
quality, seven-day adoption or independent release approval. No project migration
or AI provider is activated by downloading these files.

`SOURCE-MANIFEST.json` identifies the exact Core commit and hashes the files in
this snapshot. Checksums detect drift; they do not authenticate a publisher.
Core and UI terms are in LICENSE, NOTICE, TRADEMARKS.md and COMMERCIAL_LICENSE.md.
Third-party notices remain in `ui/vendor/`.
