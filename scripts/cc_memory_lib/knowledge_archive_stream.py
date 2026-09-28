"""Streaming CCMEMORY/1 JSON; only local fixed DDL can become SQL."""
import codecs
import hashlib
import json

from .knowledge_store import KnowledgeError

MAX_VALUE = 4 * 1024 * 1024


def write_payload(db, stream, tables, rebuildable, schema, created, encode, limit):
    count = 0
    digest = hashlib.sha256()

    def emit(text):
        nonlocal count
        raw = text.encode('utf-8')
        count += len(raw)
        if count > limit:
            raise KnowledgeError('backup_limit')
        stream.write(raw)
        digest.update(raw)

    def value(item):
        text = json.dumps(item, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        if len(text.encode('utf-8')) > MAX_VALUE:
            raise KnowledgeError('backup_value_limit')
        emit(text)

    emit('{"schema":')
    value(schema)
    emit(',"created":')
    value(created)
    emit(',"tables":{')
    for index, table in enumerate(tables):
        if index:
            emit(',')
        value(table)
        emit(':{"columns":')
        condition = (' WHERE 0' if table in rebuildable else
                     " WHERE key NOT GLOB 'ingest-stage/*' AND key NOT GLOB 'source-scan/*'" if table == 'meta' else '')
        cursor = db.execute('SELECT * FROM ' + table + condition)
        value([column[0] for column in cursor.description])
        emit(',"rows":[')
        for row_index, row in enumerate(cursor):
            if row_index:
                emit(',')
            value([encode(v) for v in row])
        emit(']}')
    emit('}}')
    return digest.hexdigest(), count


class Reader:
    def __init__(self, stream, limit):
        self.stream, self.limit = stream, limit
        self.buffer, self.eof, self.count = '', False, 0
        self.digest = hashlib.sha256()
        self.utf8 = codecs.getincrementaldecoder('utf-8')()
        self.decoder = json.JSONDecoder(parse_constant=lambda _: self.invalid())

    def invalid(self):
        raise KnowledgeError('invalid_backup_json')

    def fill(self):
        if self.eof:
            return False
        raw = self.stream.read(65536)
        self.count += len(raw)
        if self.count > self.limit:
            raise KnowledgeError('backup_limit')
        self.digest.update(raw)
        self.eof = not raw
        self.buffer += self.utf8.decode(raw, final=self.eof)
        return bool(raw)

    def peek(self):
        while True:
            self.buffer = self.buffer.lstrip(' \t\r\n')
            if self.buffer or not self.fill():
                return self.buffer[:1]

    def take(self, token):
        if self.peek() != token:
            self.invalid()
        self.buffer = self.buffer[1:]

    def value(self):
        self.peek()
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buffer)
            except json.JSONDecodeError:
                if len(self.buffer.encode('utf-8')) > MAX_VALUE or not self.fill():
                    self.invalid()
                continue
            if len(self.buffer[:end].encode('utf-8')) > MAX_VALUE:
                raise KnowledgeError('backup_value_limit')
            self.buffer = self.buffer[end:]
            return value

    def members(self):
        self.take('{')
        seen = set()
        if self.peek() == '}':
            self.take('}')
            return
        while True:
            key = self.value()
            if type(key) is not str or key in seen:
                self.invalid()
            seen.add(key)
            self.take(':')
            yield key
            if self.peek() == '}':
                self.take('}')
                return
            self.take(',')


def read_payload(stream, db, tables, schema, decode, limit):
    reader = Reader(stream, limit)
    top, found_tables = {}, set()
    for key in reader.members():
        if key != 'tables':
            if key not in ('schema', 'created'):
                raise KnowledgeError('unsupported_backup_schema')
            top[key] = reader.value()
            continue
        top['tables'] = True
        for table in reader.members():
            if table not in tables:
                raise KnowledgeError('unsupported_backup_schema')
            found_tables.add(table)
            columns = [r['name'] for r in db.execute('PRAGMA table_info(' + table + ')')]
            fields = set()
            for field in reader.members():
                fields.add(field)
                if field == 'columns':
                    if reader.value() != columns:
                        raise KnowledgeError('invalid_backup_table')
                elif field == 'rows':
                    reader.take('[')
                    if reader.peek() != ']':
                        while True:
                            row = reader.value()
                            if type(row) is not list or len(row) != len(columns):
                                raise KnowledgeError('invalid_backup_row')
                            db.execute('INSERT INTO ' + table + ' VALUES(' + ','.join('?' for _ in columns) + ')',
                                       [decode(v) for v in row])
                            if reader.peek() == ']':
                                break
                            reader.take(',')
                    reader.take(']')
                else:
                    raise KnowledgeError('invalid_backup_table')
            if fields != {'columns', 'rows'}:
                raise KnowledgeError('invalid_backup_table')
    if top.get('schema') != schema or type(top.get('schema')) is not int or found_tables != set(tables):
        raise KnowledgeError('unsupported_backup_schema')
    if reader.peek():
        reader.invalid()
    return reader.digest.hexdigest()
