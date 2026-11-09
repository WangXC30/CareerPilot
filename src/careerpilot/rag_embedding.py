"""Offline multilingual E5 ONNX inference; never downloads model files."""

import hashlib
from pathlib import Path

REVISION = '761b726dd34fb83930e26aab4e9ac3899aa1fa78'


def model_identity(model_dir):
    directory = Path(model_dir)
    digest = hashlib.sha256()
    for name in ('tokenizer.json', 'onnx/model_quantized.onnx'):
        with (directory / name).open('rb') as stream:
            for part in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(part)
    return 'Xenova/multilingual-e5-small@' + REVISION + ':' + digest.hexdigest()


class E5Embedder:
    def __init__(self, model_dir):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        directory = Path(model_dir)
        self.tokenizer = Tokenizer.from_file(str(directory / 'tokenizer.json'))
        self.tokenizer.enable_truncation(max_length=512)
        self.tokenizer.enable_padding(pad_id=1, pad_token='<pad>')
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        self.session = ort.InferenceSession(
            str(directory / 'onnx' / 'model_quantized.onnx'),
            sess_options=options, providers=['CPUExecutionProvider'],
        )

    def __call__(self, texts, role):
        import numpy as np

        if role not in {'query', 'passage'}:
            raise ValueError('Invalid embedding role')
        results = []
        for start in range(0, len(texts), 8):
            batch = self.tokenizer.encode_batch([f'{role}: {s}' for s in texts[start:start + 8]])
            ids = np.asarray([item.ids for item in batch], dtype=np.int64)
            mask = np.asarray([item.attention_mask for item in batch], dtype=np.int64)
            feeds = {'input_ids': ids, 'attention_mask': mask}
            if any(item.name == 'token_type_ids' for item in self.session.get_inputs()):
                feeds['token_type_ids'] = np.zeros_like(ids)
            hidden = self.session.run(None, feeds)[0]
            pooled = (hidden * mask[:, :, None]).sum(axis=1) / mask.sum(axis=1)[:, None]
            results.extend(pooled.tolist())
        return results


class BGEReranker:
    """Score query/passage pairs with a locally stored cross-encoder."""

    def __init__(self, model_dir):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        directory = Path(model_dir)
        with (directory / 'onnx' / 'model_quantized.onnx').open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != '912fc1215c2dbff6499700534bd8d31253af01573861abbfc43afd1fab6cce5d':
            raise ValueError('Unexpected reranker model file')
        tokenizer_hash = hashlib.sha256((directory / 'tokenizer.json').read_bytes()).hexdigest()
        if tokenizer_hash != '8bf8afbfd11306bd872018c53bfdf2e160a56f8edbcf49933324404791c148d3':
            raise ValueError('Unexpected reranker tokenizer file')
        self.tokenizer = Tokenizer.from_file(str(directory / 'tokenizer.json'))
        self.tokenizer.enable_truncation(max_length=512)
        self.tokenizer.enable_padding(pad_id=1, pad_token='<pad>')
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        self.session = ort.InferenceSession(
            str(directory / 'onnx' / 'model_quantized.onnx'),
            sess_options=options, providers=['CPUExecutionProvider'],
        )

    def __call__(self, query, passages):
        import numpy as np

        results = []
        for start in range(0, len(passages), 4):
            batch = self.tokenizer.encode_batch(
                [(query, passage) for passage in passages[start:start + 4]]
            )
            ids = np.asarray([item.ids for item in batch], dtype=np.int64)
            mask = np.asarray([item.attention_mask for item in batch], dtype=np.int64)
            feeds = {'input_ids': ids, 'attention_mask': mask}
            if any(item.name == 'token_type_ids' for item in self.session.get_inputs()):
                feeds['token_type_ids'] = np.zeros_like(ids)
            scores = self.session.run(None, feeds)[0]
            results.extend(float(value) for value in np.asarray(scores).reshape(-1))
        return results
