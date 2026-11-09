"""Local, rebuildable hybrid search over explicitly selected technical sources.

The index is derived data, never the source of truth. Model loading and index
construction are deliberately explicit; importing this module makes no network calls.
"""

import hashlib
import json
import math
import re
import sqlite3
import struct
from contextlib import closing
from pathlib import Path

TECHNICAL_ALIASES = {
    '虚拟内存': 'virtual memory', '页表': 'page table', '指针': 'pointer',
    '快速排序': 'quicksort', '时间复杂度': 'time complexity', '进程': 'process',
    '线程': 'thread', '死锁': 'deadlock', '并发': 'concurrency',
    '二叉树': 'binary tree', '哈希表': 'hash table', '链表': 'linked list',
    '动态规划': 'dynamic programming', '最短路径': 'shortest path',
    '数据库': 'database', '事务': 'transaction', '网络': 'network',
    '合并冲突': 'merge conflict', '分支': 'branch', '异常处理': 'exception handling',
    '异常': 'exception', '冲突': 'collision', '调度': 'scheduling',
    '插入': 'insertion', '解决': 'resolution', '避免': 'avoidance',
    '地址': 'address', '映射': 'mapping',
    '三次握手': 'three way handshake',
}
STOPWORDS = {'a', 'an', 'the', 'how', 'what', 'is', 'of', 'to', 'in', 'and', 'for'}


def _query_domains(query):
    """Only restrict a single explicit language; comparisons search all sources."""
    domains = []
    if re.search(r'\bc\s*(?:语言|language\b)', query, re.IGNORECASE):
        domains.append(('c_cpp/docs/c-language/',))
    if re.search(r'\bpython(?=\b|[\u4e00-\u9fff])', query, re.IGNORECASE):
        domains.append(('python/', 'Think Python'))
    if re.search(r'\bc\+\+', query, re.IGNORECASE):
        domains.append(('cpp/docs/cpp/',))
    if re.search(r'\bc#', query, re.IGNORECASE):
        domains.append(('csharp/',))
    if re.search(r'\bgit(?=\b|[\u4e00-\u9fff])', query, re.IGNORECASE):
        domains.append(('git_book/',))
    if (re.search(r'\bGo\b', query)
            or re.search(r'\bgo语言|\bgolang\b|goroutine', query, re.IGNORECASE)
            or re.search(r'\bgo\s+(?:language|channel|interface)\b', query, re.IGNORECASE)):
        domains.append(('go/_content/',))
    return domains[0] if len(domains) == 1 else ()


def _unit(values):
    vector = [float(value) for value in values]
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm or not all(math.isfinite(value) for value in vector):
        raise ValueError("Invalid embedding")
    return [value / norm for value in vector]


def _pack(values):
    return struct.pack(f"<{len(values)}f", *values)


def _unpack(blob):
    return struct.unpack(f"<{len(blob) // 4}f", blob)


def _load_vector_extension(db):
    import sqlite_vec

    db.enable_load_extension(True)
    try:
        sqlite_vec.load(db)
    finally:
        db.enable_load_extension(False)


def chunks(text, size=500, overlap=75):
    if not 0 <= overlap < size:
        raise ValueError("Invalid chunk overlap")
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"/[A-Za-z0-9]+\.(?:oldstyle|c2sc)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            # Keep whole words whenever there is a reasonable boundary nearby.
            boundary = text.rfind(' ', start + size // 2, end)
            if boundary > start:
                end = boundary
        part = text[start:end].strip()
        if (len(part) >= 100 and len(re.findall(r'[^\W\d_]', part)) >= 50
                and not part.startswith('.. Copyright')):
            yield part
        if end == len(text):
            break
        next_start = max(start + 1, end - overlap)
        if next_start > 0 and text[next_start - 1] != ' ':
            boundary = text.find(' ', next_start, end)
            next_start = boundary + 1 if boundary >= 0 else end
        start = next_start


def textbook_records(library, *, names=None):
    """Yield verified, explicitly selected PDF pages with stable source metadata.

    No-derivatives and unresolved-license sources are excluded
    pending a decision about their intended use. Broken files fail closed.
    """
    from pypdf import PdfReader

    root = Path(library).resolve()
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    permitted = {
        'textbooks/algorithms_erickson.pdf',
        'textbooks/computer_organization_lee.pdf',
        'textbooks/open_data_structures_pseudocode.pdf',
        'textbooks/xv6_book.pdf',
        'textbooks/think_python_2e.pdf',
    }
    if names is not None:
        requested = set(names)
        if not requested <= permitted:
            raise ValueError('Unapproved textbook')
        permitted = requested
    entries = {item['file']: item for item in manifest['files']}
    for name in sorted(permitted):
        item = entries[name]
        path = (root / name).resolve()
        if (not path.is_relative_to(root)
                or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']):
            raise ValueError(f"Source integrity check failed: {name}")
        reader = PdfReader(path)
        for page_number, page in enumerate(reader.pages, 1):
            content = page.extract_text() or ''
            if content.strip():
                yield {'text': content, 'source': {
                    'title': item['title'], 'url': item['source'], 'homepage': item['homepage'],
                    'license': item['license'], 'sha256': item['sha256'], 'page': page_number,
                }}


def text_records(library):
    """Read selected original documents; exclude duplicate versions and metadata."""
    root = Path(library).resolve()
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    licenses = {
        'c_cpp': 'CC BY 4.0 (code: MIT)', 'cpp': 'CC BY 4.0 (code: MIT)',
        'csharp': 'CC BY 4.0 (code: MIT)', 'go': 'CC BY 4.0 (code: BSD-3-Clause)',
        'python': 'Python Software Foundation license; see Doc/license.rst',
        'rust': 'MIT OR Apache-2.0', 'git_book': 'CC BY-NC-SA 3.0',
        'network_cnp3': 'CC BY-SA 3.0',
    }
    for item in manifest['files']:
        name = item['file']
        parts = Path(name).parts
        if (not parts or parts[0] not in licenses
                or Path(name).suffix.lower() not in {'.md', '.rst', '.asc'}):
            continue
        if Path(name).name.lower().startswith(('license', 'copying', 'copyright', 'readme')):
            continue
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f'Invalid source path: {name}')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError(f'Source integrity check failed: {name}')
        yield {'text': data.decode('utf-8'), 'source': {
            'title': name, 'url': item['source'], 'homepage': item['repository'],
            'license': licenses[parts[0]], 'sha256': item['sha256'],
            'revision': item['revision'],
        }}


def build(index_path, records, embedder, model_id):
    """Atomically replace an index; records contain text and citation metadata.

    Embedder accepts (list of texts, role), with role 'passage' or 'query'.
    The model ID must change whenever the embedding model or its revision changes.
    """
    if not model_id:
        raise ValueError("A pinned model ID is required")
    path = Path(index_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        _load_vector_extension(db)
        db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS chunks (id INTEGER PRIMARY KEY, text TEXT NOT NULL, "
                   "source TEXT NOT NULL, vector BLOB NOT NULL)")
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text)")
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM chunks_fts")
        db.execute("DELETE FROM chunks")
        db.execute("DROP TABLE IF EXISTS chunk_vectors")
        dimension = None
        for record in records:
            texts = list(chunks(record['text']))
            if not texts:
                continue
            vectors = list(embedder(texts, 'passage'))
            if len(vectors) != len(texts):
                raise ValueError("Embedding count mismatch")
            for text, raw in zip(texts, vectors):
                vector = _unit(raw)
                dimension = dimension or len(vector)
                if len(vector) != dimension:
                    raise ValueError("Embedding dimension mismatch")
                exists = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='chunk_vectors'"
                ).fetchone()
                if exists is None:
                    db.execute(
                        "CREATE VIRTUAL TABLE chunk_vectors "
                        f"USING vec0(embedding float[{dimension}])"
                    )
                cursor = db.execute("INSERT INTO chunks(text,source,vector) VALUES (?,?,?)", (
                    text, json.dumps(record['source'], ensure_ascii=False), _pack(vector)))
                db.execute("INSERT INTO chunk_vectors(rowid,embedding) VALUES (?,?)",
                           (cursor.lastrowid, _pack(vector)))
                db.execute("INSERT INTO chunks_fts(rowid,text) VALUES (?,?)",
                           (cursor.lastrowid, text))
        if dimension is None:
            raise ValueError("No indexable content")
        db.execute("DELETE FROM meta")
        db.executemany("INSERT INTO meta(key,value) VALUES (?,?)", [
            ('model_id', model_id), ('dimension', str(dimension))])


def append_records(index_path, records, embedder, model_id):
    """Add verified records in one transaction without mixing embedding models."""
    with closing(sqlite3.connect(index_path)) as db, db:
        _load_vector_extension(db)
        db.execute('BEGIN IMMEDIATE')
        metadata = dict(db.execute('SELECT key,value FROM meta'))
        if metadata.get('model_id') != model_id:
            raise ValueError('Embedding model does not match index')
        dimension = int(metadata['dimension'])
        added = 0
        for record in records:
            source = json.dumps(record['source'], ensure_ascii=False)
            if db.execute('SELECT 1 FROM chunks WHERE source=? LIMIT 1', (source,)).fetchone():
                raise ValueError('Source already indexed')
            texts = list(chunks(record['text']))
            if not texts:
                continue
            vectors = list(embedder(texts, 'passage'))
            if len(vectors) != len(texts):
                raise ValueError('Embedding count mismatch')
            for text, raw in zip(texts, vectors):
                vector = _unit(raw)
                if len(vector) != dimension:
                    raise ValueError('Embedding dimension mismatch')
                cur = db.execute('INSERT INTO chunks(text,source,vector) VALUES (?,?,?)',
                                 (text, source, _pack(vector)))
                db.execute('INSERT INTO chunks_fts(rowid,text) VALUES (?,?)', (cur.lastrowid, text))
                db.execute('INSERT INTO chunk_vectors(rowid,embedding) VALUES (?,?)',
                           (cur.lastrowid, _pack(vector)))
                added += 1
        return added


def search(index_path, query, embedder, model_id, limit=5):
    """Combine FTS5 BM25 and exact cosine rankings with reciprocal rank fusion."""
    if not query.strip() or not 1 <= limit <= 20:
        raise ValueError("Invalid query or limit")
    path = Path(index_path).resolve()
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        _load_vector_extension(db)
        meta = dict(db.execute("SELECT key,value FROM meta"))
        if meta.get('model_id') != model_id:
            raise ValueError("Embedding model does not match index; rebuild required")
        aliases = [english for chinese, english in TECHNICAL_ALIASES.items() if chinese in query]
        english = re.findall(r'[A-Za-z][A-Za-z0-9_+#]*', query)
        translated = ' '.join([*english, *aliases]) if aliases else query
        queries = [query, translated] if translated != query else [query]
        vectors = list(embedder(queries, 'query'))
        if len(vectors) != len(queries):
            raise ValueError("Embedding count mismatch")
        vectors = [_unit(vector) for vector in vectors]
        if any(len(vector) != int(meta['dimension']) for vector in vectors):
            raise ValueError("Embedding dimension mismatch")
        domains = _query_domains(query)
        domain_clause = ' OR '.join('source LIKE ?' for _ in domains)
        domain_params = tuple('%' + name + '%' for name in domains)
        restriction = (
            f' AND rowid IN (SELECT id FROM chunks WHERE {domain_clause})' if domains else ''
        )
        ranked = [[row[0] for row in db.execute(
            'SELECT rowid FROM chunk_vectors WHERE embedding MATCH ? AND k = 50'
            + restriction + ' ORDER BY distance', (_pack(vector), *domain_params))]
            for vector in vectors]
        expanded = query + ' ' + ' '.join(aliases)
        terms = list(dict.fromkeys(
            term.casefold() for term in re.findall(r'[A-Za-z][A-Za-z0-9_]*', expanded)
            if len(term) > 1 and term.casefold() not in STOPWORDS
        ))[:20]
        lexical, exact = [], []
        if terms:
            expression = ' OR '.join('"' + term.replace('"', '""') + '"' for term in terms)
            lexical_from = (
                'SELECT chunks_fts.rowid FROM chunks_fts JOIN chunks '
                'ON chunks.id=chunks_fts.rowid WHERE chunks_fts MATCH ?'
            )
            lexical_filter = f' AND ({domain_clause})' if domains else ''
            lexical = [row[0] for row in db.execute(
                lexical_from + lexical_filter + ' ORDER BY bm25(chunks_fts) LIMIT 100',
                (expression, *domain_params))]
            if aliases:
                exact = [row[0] for row in db.execute(
                    lexical_from + lexical_filter + ' ORDER BY bm25(chunks_fts) LIMIT 100',
                    ('"' + aliases[0] + '"', *domain_params))]
        scores = {}
        rankings = [(2, lexical), (1, exact), *((1, result) for result in ranked)]
        for weight, ranking in rankings:
            for rank, row_id in enumerate(ranking, 1):
                scores[row_id] = scores.get(row_id, 0) + weight / (60 + rank)
        result = []
        for row_id in sorted(scores, key=lambda key: (-scores[key], key))[:limit]:
            row = db.execute("SELECT id,text,source FROM chunks WHERE id = ?", (row_id,)).fetchone()
            result.append({'id': hashlib.sha256((row[2] + row[1]).encode()).hexdigest()[:24],
                           'text': row[1], 'source': json.loads(row[2]), 'score': scores[row_id]})
        return result
