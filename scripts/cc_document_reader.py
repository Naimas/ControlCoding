"""Read a hash-bound Markdown source and bounded, project-local image assets."""
import base64
import hashlib
from pathlib import PurePosixPath
import posixpath
import re
import struct
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

from cc_setup_service import ReadPolicy, SetupServiceError, _Snapshots, _root
from cc_layout import source_path

TEXT_LIMIT = 256 * 1024
IMAGE_LIMIT = 384 * 1024
POLICY = ReadPolicy(file_bytes=IMAGE_LIMIT, target_bytes=TEXT_LIMIT + IMAGE_LIMIT)


class DocumentError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def relative_path(value):
    if (type(value) is not str or not value or len(value) > 4096
            or any(ord(c) < 32 for c in value) or any(c in value for c in '\\:~')
            or value.startswith('/') or any(p in ('', '.', '..') or p.endswith(('.', ' ')) for p in value.split('/'))):
        raise DocumentError('unsupported_path')
    return value


def image_path(source, reference):
    try:
        parts = urlsplit(reference)
        if parts.scheme or parts.netloc or not parts.path:
            raise DocumentError('remote_or_embedded_image')
        decoded = unquote(parts.path)
        if decoded.startswith('/') or '\\' in decoded or ':' in decoded:
            raise DocumentError('unsupported_path')
        return relative_path(posixpath.normpath(posixpath.join(posixpath.dirname(source), decoded)))
    except ValueError as error:
        if isinstance(error, DocumentError):
            raise
        raise DocumentError('unsupported_path') from None


def safe_svg(raw):
    # Reject active/foreign content rather than silently present a changed diagram.
    if re.search(br'<!DOCTYPE|<!ENTITY', raw, re.I):
        raise DocumentError('unsupported_image')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise DocumentError('unsupported_image') from None
    tags = {'svg', 'g', 'defs', 'title', 'desc', 'path', 'rect', 'circle', 'ellipse',
            'line', 'polyline', 'polygon', 'text', 'tspan', 'linearGradient',
            'radialGradient', 'stop', 'pattern', 'clipPath'}
    attributes = set(('id viewBox width height x y x1 x2 y1 y2 cx cy r rx ry d points '
                      'fill fill-opacity fill-rule stroke stroke-width stroke-opacity '
                      'stroke-linecap stroke-linejoin stroke-dasharray opacity transform '
                      'font-family font-size font-weight font-style text-anchor '
                      'dominant-baseline letter-spacing offset stop-color stop-opacity '
                      'gradientUnits gradientTransform patternUnits patternTransform '
                      'clip-path preserveAspectRatio role aria-labelledby').split())
    if root.tag != '{http://www.w3.org/2000/svg}svg':
        raise DocumentError('unsupported_image')
    for index, node in enumerate(root.iter()):
        if index > 4000 or not node.tag.startswith('{http://www.w3.org/2000/svg}') or node.tag.split('}')[-1] not in tags:
            raise DocumentError('unsupported_image')
        for key, value in node.attrib.items():
            if key not in attributes or len(value) > 32768:
                raise DocumentError('unsupported_image')
            if ('url' in value.lower() and not re.fullmatch(r'url\(#[A-Za-z_][\w.-]*\)', value)) or any(c in value for c in '<>\\'):
                raise DocumentError('unsupported_image')
    return raw


def image_type(path, raw):
    suffix = PurePosixPath(path).suffix.lower()
    if suffix == '.svg':
        safe_svg(raw)
        return 'image/svg+xml'
    if suffix == '.png' and raw.startswith(b'\x89PNG\r\n\x1a\n') and len(raw) >= 24 and raw[12:16] == b'IHDR':
        image_dimensions(*struct.unpack('>II', raw[16:24]))
        return 'image/png'
    if suffix in ('.jpg', '.jpeg') and raw.startswith(b'\xff\xd8\xff'):
        offset = 2
        while offset + 4 <= len(raw):
            if raw[offset] != 255:
                break
            marker = raw[offset + 1]
            if marker == 255:
                offset += 1
                continue
            size = int.from_bytes(raw[offset + 2:offset + 4], 'big')
            if size < 2 or offset + 2 + size > len(raw):
                break
            if marker in (0xC0, 0xC1, 0xC2) and size >= 8:
                height, width = struct.unpack('>HH', raw[offset + 5:offset + 9])
                image_dimensions(width, height)
                return 'image/jpeg'
            offset += 2 + size
    if suffix == '.gif' and raw[:6] in (b'GIF87a', b'GIF89a') and len(raw) >= 10:
        image_dimensions(*struct.unpack('<HH', raw[6:10]))
        return 'image/gif'
    if suffix == '.webp' and raw[:4] == b'RIFF' and raw[8:12] == b'WEBP' and len(raw) >= 30:
        kind = raw[12:16]
        if kind == b'VP8X':
            # Animated WebP is intentionally excluded from document illustrations.
            if raw[20] & 2:
                raise DocumentError('unsupported_image')
            image_dimensions(1 + int.from_bytes(raw[24:27], 'little'), 1 + int.from_bytes(raw[27:30], 'little'))
            return 'image/webp'
        if kind == b'VP8 ' and raw[23:26] == b'\x9d\x01\x2a':
            image_dimensions(int.from_bytes(raw[26:28], 'little') & 0x3FFF, int.from_bytes(raw[28:30], 'little') & 0x3FFF)
            return 'image/webp'
        if kind == b'VP8L' and raw[20] == 0x2F:
            bits = int.from_bytes(raw[21:25], 'little')
            image_dimensions((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
            return 'image/webp'
    raise DocumentError('unsupported_image')


def image_dimensions(width, height):
    if not (0 < width <= 8192 and 0 < height <= 8192 and width * height <= 16000000):
        raise DocumentError('too_large')


def read_document(root_value, source, expected_hash, images):
    source = relative_path(source)
    if not source.lower().endswith('.md') or PurePosixPath(source).name.upper() == 'AGENTS.MD':
        raise DocumentError('invalid_document')
    if type(expected_hash) is not str or not re.fullmatch('[a-f0-9]{64}', expected_hash):
        raise DocumentError('invalid_document')
    if (type(images) is not list or len(images) > 24 or any(type(i) is not str or len(i) > 4096 for i in images)
            or len(set(images)) != len(images)):
        raise DocumentError('invalid_images')
    try:
        root = _root(root_value)
        physical_source = _physical_document_path(root, source)
        with _Snapshots(root, {}, POLICY) as reader:
            entry = reader.observe(physical_source, {})
            if entry is None or hashlib.sha256(entry[-1]).hexdigest() != expected_hash:
                raise DocumentError('changed_input')
            raw = entry[-1]
            if len(raw) > TEXT_LIMIT:
                raise DocumentError('too_large')
            markdown = raw.decode('utf-8-sig')
            assets, used = [], 0
            for reference in images:
                asset = {'reference': reference}
                try:
                    target = image_path(source, reference)
                    if PurePosixPath(target).suffix.lower() not in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'):
                        raise DocumentError('unsupported_image')
                    image = reader.observe(_physical_document_path(root, target), {})
                    if image is None:
                        raise DocumentError('missing_image')
                    data = image[-1]
                    if used + len(data) > IMAGE_LIMIT:
                        raise DocumentError('too_large')
                    mime = image_type(target, data)
                    used += len(data)
                    asset.update(status='available', data='data:' + mime + ';base64,' + base64.b64encode(data).decode('ascii'))
                except (DocumentError, SetupServiceError) as error:
                    asset.update(status='unavailable', reason=error.code)
                assets.append(asset)
            reader.recheck()
        physical_relative = physical_source.relative_to(root).as_posix()
        return {'project_root': root_value, 'document': {'path': source, 'physicalPath': physical_relative, 'sha256': expected_hash,
                'markdown': markdown, 'bytes': len(raw), 'images': assets}}
    except DocumentError:
        raise
    except SetupServiceError as error:
        raise DocumentError(error.code) from None
    except (OSError, UnicodeError):
        raise DocumentError('invalid_document') from None


def _physical_document_path(root, relative):
    """Keep adopter documents literal; only explicit managed namespaces route."""
    if relative.split('/', 1)[0] in ('.controlcoding', '.controlwork'):
        return source_path(root, relative)
    return root / relative
