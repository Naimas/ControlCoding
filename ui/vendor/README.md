# Markdown parser provenance

`markdown-it.cjs` is the unmodified standalone `dist/markdown-it.min.js` from
markdown-it **12.3.2**, already installed with Cursor's `@vscode/vsce` dependency.
SHA-256: `2e77c809205e08971b002366974580295051cad390a607cc25c12731b066790c`.
Upstream: https://github.com/markdown-it/markdown-it/tree/12.3.2 (MIT; adjacent license).
No install or runtime network lookup. This records the reused version, not a claim
that it is the latest upstream release.

The app uses its tokens, never its HTML renderer. HTML and linkification are off;
text/nesting/token limits apply. React creates only explicitly allowed elements
and attributes. Images use main-process validated bytes, links never fetch or
navigate externally. Unsupported Markdown extensions remain visible source text.
