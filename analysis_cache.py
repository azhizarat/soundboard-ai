"""Cache volátil limitado por bytes e itens; nunca persiste fala pessoal."""
import copy
import hashlib
import json
import pickle
import threading
from collections import OrderedDict


def cache_key(content_hash, interval, stage, model_version, parameters):
    payload = [content_hash, interval, stage, model_version, parameters]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def pcm_hash(audio):
    import numpy as np
    value = np.ascontiguousarray(audio, dtype=np.float32)
    return hashlib.sha256(str(value.shape).encode() + value.tobytes()).hexdigest()


class AnalysisCache:
    def __init__(self, max_bytes=64*1024*1024, max_items=128):
        self.max_bytes, self.max_items = max(0, int(max_bytes)), max(0, int(max_items))
        self._items = OrderedDict()
        self._bytes = self._hits = self._misses = 0
        self._lock = threading.RLock()

    def get(self, key):
        with self._lock:
            if key not in self._items:
                self._misses += 1
                return None
            self._hits += 1
            self._items.move_to_end(key)
            # Bytes são produzidos apenas por put(), nunca lidos de disco/rede.
            return pickle.loads(self._items[key])

    def put(self, key, value):
        encoded = pickle.dumps(value, protocol=5)
        size = len(encoded)
        with self._lock:
            if key in self._items:
                self._bytes -= len(self._items.pop(key))
            if size > self.max_bytes or not self.max_items:
                return
            while self._items and (self._bytes + size > self.max_bytes or len(self._items) >= self.max_items):
                self._bytes -= len(self._items.popitem(last=False)[1])
            self._items[key] = encoded
            self._bytes += size

    def clear(self):
        with self._lock:
            self._items.clear()
            self._bytes = 0

    def stats(self):
        with self._lock:
            return {'items': len(self._items), 'bytes': self._bytes, 'hits': self._hits,
                    'misses': self._misses, 'max_bytes': self.max_bytes, 'max_items': self.max_items}


DECODE_CACHE = AnalysisCache(max_bytes=32*1024*1024, max_items=32)


def project_voice_embeddings(segments, source_id, spans, extractor_version):
    """Só reutiliza segmentos integrais, sem lacunas/sobreposição na montagem.

    Recortar dentro de uma fala não prova que seu vetor continua representativo;
    nesse caso a inferência normal refaz a evidência. Nunca cadastra referência.
    """
    selected = []
    for lo, hi in spans:
        candidates = []
        for segment in segments:
            a,b = segment.get('start_sample'),segment.get('end_sample')
            if type(a) is not int or type(b) is not int or a >= b:
                return None
            if a >= hi or b <= lo:
                continue
            if (a < lo or b > hi or segment.get('source_id',source_id) != source_id
                    or segment.get('extractor_version') != extractor_version
                    or segment.get('voice_embedding') is None
                    or not (segment.get('local_cluster_id') or segment.get('speaker_id'))):
                return None
            evidence = segment.get('embedding_source_spans')
            if segment.get('embedding_source_id') != source_id or not isinstance(evidence, list) or not evidence:
                return None
            # Vetor pode agregar outros trechos do cluster ou atravessar uma junção.
            # Toda a evidência precisa pertencer ao segmento integral ainda retido.
            for interval in evidence:
                if (not isinstance(interval, list) or len(interval) != 2
                        or any(type(value) is not int for value in interval)
                        or not a <= interval[0] < interval[1] <= b):
                    return None
            from speaker_matching import normalized
            if normalized(segment['voice_embedding']) is None:
                return None
            candidates.append(segment)
        cursor = lo
        for segment in sorted(candidates,key=lambda s:s['start_sample']):
            if segment['start_sample'] != cursor:
                return None
            cursor = segment['end_sample']
            selected.append(copy.deepcopy(segment))
        if cursor != hi:
            return None
    return selected or None
