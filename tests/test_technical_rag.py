import pytest

from careerpilot.technical_rag import append_records, build, chunks, search


def embedding(texts, role):
    return [[1.0, 0.0] if 'transaction' in text.lower() else [0.0, 1.0] for text in texts]


def test_hybrid_search_and_citation(tmp_path):
    index = tmp_path / 'index.db'
    build(index, [
        {'text': 'Transactions provide atomicity. ' * 5,
         'source': {'url': 'https://example.org', 'page': 7}},
        {'text': 'Routing connects networks. ' * 5,
         'source': {'url': 'https://example.net', 'page': 2}},
    ], embedding, 'model-v1')
    result = search(index, 'transaction', embedding, 'model-v1')
    assert result[0]['source'] == {'url': 'https://example.org', 'page': 7}
    assert 'atomicity' in result[0]['text']
    with pytest.raises(ValueError, match='rebuild'):
        search(index, 'transaction', embedding, 'model-v2')


def test_failed_rebuild_preserves_previous_index(tmp_path):
    index = tmp_path / 'index.db'
    build(index, [{'text': 'Transactions work. ' * 8, 'source': {'page': 1}}], embedding, 'v1')
    with pytest.raises(ValueError, match='Invalid embedding'):
        build(index, [{'text': 'broken text ' * 12, 'source': {}}],
              lambda texts, role: [[0, 0]], 'v2')
    assert search(index, 'transaction', embedding, 'v1')[0]['source']['page'] == 1


def test_append_rejects_duplicates_without_partial_writes(tmp_path):
    index = tmp_path / 'index.db'
    original = {'text': 'Transactions work. ' * 8, 'source': {'page': 1}}
    additional = {'text': 'Routing connects networks. ' * 5, 'source': {'page': 2}}
    build(index, [original], embedding, 'v1')
    with pytest.raises(ValueError, match='already indexed'):
        append_records(index, [additional, original], embedding, 'v1')
    assert all(item['source']['page'] == 1 for item in search(index, 'routing', embedding, 'v1'))
    assert append_records(index, [additional], embedding, 'v1') == 1


def test_staged_build_preserves_existing_index_on_failure(tmp_path):
    from scripts.build_technical_rag import build_staged

    index = tmp_path / 'index.db'
    original = {'text': 'Transactions work. ' * 8, 'source': {'page': 1}}
    build(index, [original], embedding, 'v1')
    with pytest.raises(ValueError, match='Invalid embedding'):
        build_staged(index, [original], lambda texts, role: [[0, 0]], 'v2')
    assert search(index, 'transaction', embedding, 'v1')[0]['source']['page'] == 1
    assert sorted(path.name for path in tmp_path.iterdir()) == ['index.db']


def test_assistant_uses_local_sources_and_handles_unavailable_index(tmp_path, monkeypatch):
    from careerpilot import conversations
    from careerpilot.domain import DomainError

    index = tmp_path / 'index.db'
    build(index, [{'text': 'Transactions provide atomicity. ' * 5, 'source': {
        'title': 'Database reference', 'url': 'https://example.org/doc', 'page': 7,
    }}], embedding, 'v1')
    monkeypatch.setenv('CAREERPILOT_RAG_INDEX', str(index))
    monkeypatch.setattr(conversations, '_technical_model', lambda directory: (embedding, 'v1'))
    monkeypatch.setattr(conversations, '_technical_reranker',
                        lambda directory: lambda query, passages: [1.0] * len(passages))
    result = conversations.technical_search('transaction')
    assert result['items'][0]['source_id'] == result['sources'][0]['id']
    assert '第 7 页' in result['sources'][0]['label']
    monkeypatch.setattr(conversations, '_technical_reranker',
                        lambda directory: lambda query, passages: [-10.0] * len(passages))
    assert conversations.technical_search('transaction')['items'] == []
    monkeypatch.setenv('CAREERPILOT_RAG_INDEX', str(tmp_path / 'missing.db'))
    with pytest.raises(DomainError, match='本地技术资料库不可用'):
        conversations.technical_search('transaction')


def test_assistant_deduplicates_near_identical_passages(monkeypatch):
    from careerpilot import conversations, technical_rag

    def item(ident, text, title):
        return {'id': ident, 'text': text, 'source': {'title': title, 'url': 'https://example.org'}}

    base = 'Transactions commit atomically and maintain data integrity. ' * 5
    found = [item('1', base, 'Guide'), item('2', base + ' More.', 'Guide'),
             item('3', 'Transaction rollback discards changes. ' * 5, 'Guide')]
    monkeypatch.setattr(conversations, '_technical_model', lambda directory: (embedding, 'v1'))
    monkeypatch.setattr(conversations, '_technical_reranker',
                        lambda directory: lambda query, passages: [3.0, 2.0, 1.0])
    monkeypatch.setattr(technical_rag, 'search', lambda *args, **kwargs: found)
    result = conversations.technical_search('transaction')
    assert [item['source_id'] for item in result['items']] == ['1', '3']


def test_chinese_alias_finds_english_evidence_and_respects_language(tmp_path):
    index = tmp_path / 'index.db'
    records = [
        {'text': 'A page table maps virtual memory to physical addresses. ' * 3,
         'source': {'title': 'Computer Organization'}},
        {'text': 'A pointer in the C language stores the address of an object. ' * 3,
         'source': {'title': 'c_cpp/docs/c-language/pointers.md'}},
        {'text': 'C sharp uses a pointer in unsafe contexts. ' * 4,
         'source': {'title': 'csharp/standard/unsafe-code.md'}},
    ]
    build(index, records, embedding, 'v1')
    assert 'page table' in search(index, '虚拟内存页表', embedding, 'v1')[0]['text']
    assert 'c-language' in search(index, 'C语言指针', embedding, 'v1')[0]['source']['title']


def test_language_filter_applies_before_top_k(tmp_path):
    from careerpilot.technical_rag import _query_domains

    index = tmp_path / 'index.db'
    noise = [{'text': 'pointer dereference example and memory address ' * 4,
              'source': {'title': f'csharp/standard/{i}.md'}} for i in range(110)]
    target = {'text': 'pointer dereference example and memory address ' * 4,
              'source': {'title': 'c_cpp/docs/c-language/pointers.md'}}
    build(index, [*noise, target], embedding, 'v1')
    result = search(index, 'C语言指针 pointer', embedding, 'v1')
    assert result and all('c-language' in item['source']['title'] for item in result)
    assert _query_domains('compare Python and Go') == ()
    assert _query_domains('how do I go to work') == ()
    assert _query_domains('go语言并发') == ('go/_content/',)
    assert _query_domains('Python异常处理') == ('python/', 'Think Python')
    assert _query_domains('Git合并冲突') == ('git_book/',)


def test_chunk_boundaries_preserve_words_and_overlap():
    text = ' '.join(f'concept{i:03d}' for i in range(150))
    parts = list(chunks(text, size=200, overlap=55))
    assert len(parts) > 2
    assert all(piece.split()[0].startswith('concept') for piece in parts)
    assert all(piece.split()[-1].startswith('concept') for piece in parts)
    assert all(set(left.split()) & set(right.split()) for left, right in zip(parts, parts[1:]))
