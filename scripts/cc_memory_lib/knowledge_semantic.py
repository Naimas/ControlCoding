"""Explicit loopback-only neural embeddings; no cloud or lexical masquerade."""
import json
import math
import struct
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from .knowledge_store import KnowledgeError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise KnowledgeError('embedding_redirect_refused')


class OllamaEmbedding:
    def __init__(self, model):
        self.model = model
        self.identity = None
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, route, body=None):
        request = Request('http://127.0.0.1:11434/api/' + route,
                          data=None if body is None else json.dumps(body).encode(),
                          headers={'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=45) as response:
                data = response.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024:
                raise KnowledgeError('embedding_response_limit')
            return json.loads(data)
        except (OSError, ValueError) as exc:
            raise KnowledgeError('embedding_unavailable') from exc

    def pin(self):
        rows = self.request('tags').get('models', [])
        row = next((r for r in rows if r.get('name') == self.model), None)
        if not row or not isinstance(row.get('digest'), str):
            raise KnowledgeError('embedding_model_missing')
        self.identity = self.model + '@' + row['digest']
        return self.identity

    def embed(self, texts):
        values = self.request('embed', {'model': self.model, 'input': texts,
                                        'truncate': False, 'keep_alive': '2m'}).get('embeddings')
        if not isinstance(values, list) or len(values) != len(texts):
            raise KnowledgeError('invalid_embeddings')
        return [normalize(v) for v in values]


def normalize(vector):
    if (not isinstance(vector, list) or not 1 <= len(vector) <= 16384
            or any(type(n) not in (int, float) or not math.isfinite(n) for n in vector)):
        raise KnowledgeError('invalid_embeddings')
    length = math.sqrt(sum(n * n for n in vector))
    if not math.isfinite(length) or length <= 0:
        raise KnowledgeError('invalid_embeddings')
    return [n / length for n in vector]


def cosine(left, right):
    if len(left) != len(right):
        raise KnowledgeError('embedding_dimension_changed')
    return sum(a * b for a, b in zip(left, right))


def encode_vector(vector):
    values = normalize(vector)
    return struct.pack('<' + str(len(values)) + 'f', *values)


def decode_vector(value):
    if isinstance(value, bytes):
        if not value or len(value) % 4 or len(value) > 65536:
            raise KnowledgeError('invalid_embeddings')
        return struct.unpack('<' + str(len(value) // 4) + 'f', value)
    # Compatible with early local coordinator snapshots, before compact storage.
    return normalize(json.loads(value))
