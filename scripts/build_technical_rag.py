"""Build a local derived index, without modifying business records."""

import argparse
import itertools
import os
import tempfile
from pathlib import Path

from careerpilot.rag_embedding import E5Embedder, model_identity
from careerpilot.technical_rag import build, text_records, textbook_records


def build_staged(target, records, embedder, identity):
    target = Path(target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.rag-build-', dir=target.parent) as temporary:
        staged = Path(temporary) / 'technical.sqlite3'
        build(staged, records, embedder, identity)
        os.replace(staged, target)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--library', default='data/rag_technical_sources')
    parser.add_argument('--model', default='data/rag_models/multilingual-e5-small')
    parser.add_argument('--index', default='data/rag_index/technical.sqlite3')
    args = parser.parse_args()
    print('Loading local embedding model...', flush=True)
    embedder = E5Embedder(args.model)
    identity = model_identity(args.model)

    def records():
        source = itertools.chain(text_records(args.library), textbook_records(args.library))
        for count, record in enumerate(source, 1):
            if count % 50 == 0:
                print(f'Processed {count} source records', flush=True)
            yield record

    target = Path(os.path.expanduser(args.index)).resolve()
    print('Building a separate index; the existing index remains available.', flush=True)
    build_staged(target, records(), embedder, identity)
    print('Index built successfully.', flush=True)


if __name__ == '__main__':
    main()
