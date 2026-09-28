"""Streaming lexical statistics and deterministic BM25 passage ranks."""
from collections import Counter
import math
import re


K1 = 1.2
B = .75
TITLE_WEIGHT = 2
TITLE_LIMIT = 180
TOKENS = re.compile(r'[^\W_]{2,}')
ASCII_WORDS = str.maketrans({number: chr(number).lower() if chr(number).isalnum() else ' '
                            for number in range(128)})


def tokenize(value):
    """Split Unicode words and identifier underscores into whole search terms."""
    # ASCII has the same alphanumeric word boundaries as TOKENS. Translate/split
    # stays in the runtime's tight loops; non-ASCII retains Unicode casefold and
    # regex semantics (including casefold expansions). No corpus cache is kept.
    if value.isascii():
        return [word for word in value.translate(ASCII_WORDS).split() if len(word) >= 2]
    return TOKENS.findall(value.casefold())


def bm25_ranks(rows, terms):
    """Return current passage IDs ordered by BM25 without retaining passage text."""
    query = frozenset(terms)
    document_count = 0
    length_sum = 0
    document_frequency = Counter()
    matched = []
    for row in rows:
        body = tokenize(row['text'])
        # The bounded source title supplies context when a passage omits its
        # subject. Count its terms separately with a fixed, transparent weight.
        title = row['title'][:TITLE_LIMIT] if 'title' in row.keys() else ''
        title_tokens = tokenize(title)
        length = len(body) + TITLE_WEIGHT * len(title_tokens)
        if len(query) <= 6:
            # list.index/count run in C; preserve first-appearance order so
            # score summation and deterministic ties match the tokenwise path.
            body_positions = []
            for term in query:
                try:
                    body_positions.append((body.index(term), term))
                except ValueError:
                    pass
            counts = {term: body.count(term)
                      for _, term in sorted(body_positions)}
            title_positions = []
            for term in query:
                try:
                    title_positions.append((title_tokens.index(term), term))
                except ValueError:
                    pass
            for _, term in sorted(title_positions):
                counts[term] = counts.get(term, 0) + TITLE_WEIGHT * title_tokens.count(term)
        else:
            counts = Counter()
            for token in body:
                if token in query:
                    counts[token] += 1
            for token in title_tokens:
                if token in query:
                    counts[token] += TITLE_WEIGHT
        document_count += 1
        length_sum += length
        if counts:
            document_frequency.update(counts.keys())
            matched.append((row['id'], counts, length))
    if not matched:
        return []
    average_length = length_sum / document_count
    idf = {term: math.log1p((document_count - frequency + .5) / (frequency + .5))
           for term, frequency in document_frequency.items()}
    ranked = []
    for identifier, counts, length in matched:
        normalization = K1 * (1 - B + B * length / average_length)
        score = sum(idf[term] * frequency * (K1 + 1) / (frequency + normalization)
                    for term, frequency in counts.items())
        ranked.append((score, identifier))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [identifier for _, identifier in ranked]
