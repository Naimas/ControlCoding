"""The full reader is hash-bound, read-only and confined even for image targets."""
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cc_document_reader import DocumentError, read_document, image_type
from cc_panel_bridge import dispatch, serve


def prepare(root, text='# Document\n' + 'Full text beyond excerpt.\n' * 100):
    raw = text.encode('utf-8')
    (root / 'README.md').write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def test_complete_unicode_source_and_strict_transport(tmp_path):
    text = '# Città\n' + 'Documentazione 日本語\n' * 4000
    sha = prepare(tmp_path, text)
    request = dict(version=1, id='test', operation='document_read_v1', project_root=str(tmp_path), path='README.md', sha256=sha, images=[])
    output = io.BytesIO()
    serve(io.BytesIO(json.dumps(request).encode()), output)
    result = json.loads(output.getvalue())
    assert result['status'] == 'ok' and result['result']['document']['markdown'] == text
    assert dispatch(dict(request, arbitrary=True))['error']['code'] == 'invalid_message'
    assert not (tmp_path / '.controlwork').exists()


def test_hash_mismatch_deletion_and_non_utf8(tmp_path):
    sha = prepare(tmp_path)
    (tmp_path / 'README.md').write_text('Changed', encoding='utf-8')
    with pytest.raises(DocumentError, match='changed_input'):
        read_document(str(tmp_path), 'README.md', sha, [])
    (tmp_path / 'README.md').unlink()
    with pytest.raises(DocumentError, match='changed_input'):
        read_document(str(tmp_path), 'README.md', sha, [])
    raw = b'\xff\xff'; (tmp_path / 'README.md').write_bytes(raw)
    with pytest.raises(DocumentError, match='invalid_document'):
        read_document(str(tmp_path), 'README.md', hashlib.sha256(raw).hexdigest(), [])


@pytest.mark.parametrize('source', ['../private.md', '/README.md', 'README.md:secret', 'DOCUME~1/a.md', 'docs./a.md', 'docs /a.md', 'AGENTS.md', '.env', 'docs\\a.md'])
def test_source_paths_are_not_generic_file_access(tmp_path, source):
    sha = prepare(tmp_path)
    with pytest.raises(DocumentError):
        read_document(str(tmp_path), source, sha, [])


def test_local_svg_and_reference_images_and_read_only_bytes(tmp_path):
    sha = prepare(tmp_path)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20" fill="red"/></svg>'
    (tmp_path / 'drawing.svg').write_bytes(svg)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    result = read_document(str(tmp_path), 'README.md', sha, ['drawing.svg'])['document']
    assert result['images'][0]['data'].startswith('data:image/svg+xml;base64,')
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize('reference', ['../secret.png', '%2e%2e/secret.png', 'https://example.org/tracker.png', '//host/share/a.png', 'file:///C:/private.png', 'data:image/png;base64,AAAA', 'C:/private.png', 'docs\\a.png', 'DOCUME~1/a.png', '.env'])
def test_image_escape_network_and_arbitrary_types_are_visible_failures(tmp_path, reference):
    sha = prepare(tmp_path)
    item = read_document(str(tmp_path), 'README.md', sha, [reference])['document']['images'][0]
    assert item['status'] == 'unavailable' and 'data' not in item


@pytest.mark.parametrize('body', ['<script>alert(1)</script>', '<foreignObject/>', '<image href="https://example.org/a.png"/>', '<rect onload="alert(1)"/>', '<rect fill="url(https://example.org/a)"/>', '<style>body{}</style>'])
def test_active_svg_is_rejected(tmp_path, body):
    sha = prepare(tmp_path)
    (tmp_path / 'bad.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg">'+body+'</svg>', encoding='utf-8')
    assert read_document(str(tmp_path), 'README.md', sha, ['bad.svg'])['document']['images'][0]['reason'] == 'unsupported_image'


def test_budgets_and_image_dimensions(tmp_path):
    sha = prepare(tmp_path, 'x' * (256 * 1024 + 1))
    with pytest.raises(DocumentError, match='too_large'):
        read_document(str(tmp_path), 'README.md', sha, [])
    sha = prepare(tmp_path)
    with pytest.raises(DocumentError, match='invalid_images'):
        read_document(str(tmp_path), 'README.md', sha, ['a.png'] * 25)
    (tmp_path / 'huge.png').write_bytes(b'x' * (384 * 1024 + 1))
    assert read_document(str(tmp_path), 'README.md', sha, ['huge.png'])['document']['images'][0]['status'] == 'unavailable'
    with pytest.raises(DocumentError, match='too_large'):
        image_type('bomb.png', b'\x89PNG\r\n\x1a\n'+b'\0\0\0\rIHDR'+struct.pack('>II', 100000, 100000))


def test_hardlinked_sources_and_images_are_rejected(tmp_path):
    sha = prepare(tmp_path)
    os.link(tmp_path / 'README.md', tmp_path / 'alias.md')
    with pytest.raises(DocumentError, match='unsupported_path'):
        read_document(str(tmp_path), 'README.md', sha, [])
    (tmp_path / 'alias.md').unlink()
    (tmp_path / 'picture.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding='utf-8')
    os.link(tmp_path / 'picture.svg', tmp_path / 'alias.svg')
    assert read_document(str(tmp_path), 'README.md', sha, ['alias.svg'])['document']['images'][0]['status'] == 'unavailable'


def test_archive_markdown_uses_same_identity_contract(tmp_path):
    path = '.controlwork/memory/plans/plan.md'
    target = tmp_path / path; target.parent.mkdir(parents=True)
    target.write_bytes(b'# Archive plan\nOriginal source')
    result = read_document(str(tmp_path), path, hashlib.sha256(target.read_bytes()).hexdigest(), [])
    assert result['document']['markdown'].endswith('Original source')
