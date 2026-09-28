"""Reviewed rich-document extraction from already verified immutable bytes."""
import io
import json
from pathlib import Path
import re
import zipfile
import zlib

from . import extractors
from cc_panel_configuration import require, digest

SUPPORTED = ('.md', '.txt', '.docx', '.xlsx', '.pdf')
# Bump when extraction semantics change; followed-source revisions bind this.
EXTRACTOR_VERSION = 'rich-text-v1'


class BytesSource:
    def __init__(self, raw):
        self.raw = raw

    def read_bytes(self):
        return self.raw


def extract(relative, raw, sidecar=None):
    suffix = Path(relative).suffix.lower()
    require(suffix in SUPPORTED and len(raw) <= 1024 * 1024, 'document_limit')
    if suffix in ('.md', '.txt'):
        require(len(raw) <= 32768, 'document_limit')
        body = raw.decode('utf-8-sig')
        require('\x00' not in body and bool(body.strip()), 'unsupported_document')
        return body
    if suffix in ('.docx', '.xlsx'):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = archive.infolist()
                require(len(entries) <= 256 and sum(e.file_size for e in entries) <= 4 * 1024 * 1024,
                        'document_expansion_limit')
                require(len(set(e.filename for e in entries)) == len(entries), 'unsupported_document')
                for entry in entries:
                    require(not entry.flag_bits & 1, 'encrypted_document')
                    if entry.filename.endswith('.xml'):
                        require(b'<!DOCTYPE' not in archive.read(entry).upper(), 'unsupported_document')
        except (zipfile.BadZipFile, RuntimeError):
            require(False, 'unsupported_document')
        result = (extractors.docx_text_extract(io.BytesIO(raw)) if suffix == '.docx' else
                  extractors.xlsx_text_extract(io.BytesIO(raw)))
    else:
        require(b'/Encrypt' not in raw, 'encrypted_document')
        expanded, count = 0, 0
        for match in re.finditer(rb'<<(?P<dict>.*?)>>\s*stream\r?\n(?P<stream>.*?)\r?\nendstream', raw, re.S):
            count += 1
            require(count <= 256, 'document_expansion_limit')
            block = match['stream']
            if b'/FlateDecode' in match['dict']:
                try:
                    inflater = zlib.decompressobj()
                    block = inflater.decompress(block, 4 * 1024 * 1024 + 1 - expanded)
                    require(inflater.eof, 'document_expansion_limit')
                except zlib.error:
                    require(False, 'unsupported_document')
            expanded += len(block)
            require(expanded <= 4 * 1024 * 1024, 'document_expansion_limit')
        result = extractors.pdf_text_extract(BytesSource(raw), 32000)
        if not result.get('ok') and sidecar is not None:
            from .work_features import validate_ocr_sidecar_payload, ocr_source_hash, ocr_sidecar_text
            value = json.loads(sidecar.decode('utf-8-sig'))
            valid, _ = validate_ocr_sidecar_payload(value, relative, digest(raw))
            require(valid and ocr_source_hash(value) == digest(raw), 'ocr_source_mismatch')
            result = {'ok': True, 'text': ocr_sidecar_text(value),
                      'warnings': ['OCR sidecar supplied by the user; transcription accuracy requires review.'],
                      'metadata': {'extractorFamily': 'reviewed_ocr_sidecar', 'sidecarSha256': digest(sidecar)}}
        if not result.get('ok'):
            require(False, 'ocr_required')
    require(result.get('ok') and result.get('text', '').strip(), 'unsupported_document')
    require(len(result['text'].encode('utf-8')) <= 32768, 'document_limit')
    return ('## Import provenance\n\n- Original: ' + relative + '\n- SHA-256: ' + digest(raw) +
            '\n- Extraction: ' + json.dumps(result.get('metadata', {}), ensure_ascii=False) +
            '\n\n## Coverage and review\n\n' + '\n'.join('- ' + w for w in result.get('warnings', [])) +
            '\n\n## Extracted content\n\n' + result['text'])
