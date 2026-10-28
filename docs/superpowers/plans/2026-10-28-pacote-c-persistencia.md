# Pacote C — Persistência Versionada e Write-Behind

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Prover persistência confiável e de alto desempenho no `SoundboardStore`, introduzindo versionamento de schema (`schema_version = 1`), backup automático (`.bak`) antes de migrações, worker de write-behind thread-safe (substituindo timers efêmeros por coalescência com `Condition`), desacoplamento de I/O em contadores de reprodução (`increment_play_count`), e documentação do gatilho para SQLite.

**Architecture:**
- `store.py`:
  - `schema_version = 1` no estado do store.
  - `_load()` detecta v0 (ausência de chave), gera backup `soundboard.json.bak` e migra para v1.
  - `_write_behind_loop`: thread dedicada única gerenciada por `threading.Condition`.
  - `mark_dirty(delay=0.75)` / `save_debounced`: registra deadline de escrita coalescendo múltiplas alterações.
  - `save()` / `flush()`: drena alterações imediatamente sob lock, mantém escrita atômica (`.tmp` + `os.replace`), garante que falhas mantenham o estado `_dirty` e não deixem resíduos temporários.
  - `increment_play_count`: opera em memória e marca dirty via write-behind em vez de travar o áudio com I/O síncrono.
  - Documentação dos gatilhos de migração para SQLite (>2.000 clipes, save >150 ms, ou JSON >10 MB).
- Testes em `tests/test_c_persistence.py`.

**Spec:** `docs/superpowers/specs/2026-10-28-soundboard-roadmap-design.md` §10.

---

## Contratos

```python
# store.py
CURRENT_SCHEMA_VERSION = 1
SQLITE_TRIGGER_CLIPS = 2000
SQLITE_TRIGGER_SAVE_MS = 150.0
SQLITE_TRIGGER_JSON_MB = 10.0

class SoundboardStore:
    def __init__(self, data_dir: str = "data"): ...
    def mark_dirty(self, delay: float = 0.75) -> None: ...
    def save(self) -> None: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...
```

---

## Tarefas de Implementação

### Task 1: Versionamento de Schema e Backup em `store.py`
- Adicionar `schema_version: 1` em `self.state`.
- Em `_load()`: se `soundboard.json` existe e não possui `schema_version` (v0):
  - Criar cópia de segurança em `soundboard.json.bak`.
  - Aplicar migração normalizando clipes, speakers e settings.
  - Definir `schema_version = 1`.
- Se o arquivo for corrompido, não sobrescrever o `.bak` existente.

### Task 2: Worker de Write-Behind Thread-Safe em `store.py`
- Substituir `_save_timer = threading.Timer(...)` por uma thread daemon dedicada `_write_thread` usando `threading.Condition(self._lock)`.
- `mark_dirty(delay=0.75)` / `save_debounced()`: marca `_dirty = True`, define deadline `time.time() + delay` e notifica a Condition.
- O loop de write-behind dorme até a deadline e persiste de forma coalescida (100 marks em 100 ms resultam em 1 único save atômico).
- `save()` e `flush()` realizam a gravação síncrona imediatamente sob lock e limpam `_dirty = False` apenas se o save atômico for concluído com sucesso.
- Se a gravação atômica falhar (`tmp_path.write_text` ou `os.replace`), `_dirty` permanece `True` e o arquivo `.tmp` é removido.
- `close()` / `atexit`: drena e finaliza o worker de forma limpa.

### Task 3: Desacoplar I/O de `increment_play_count`
- Alterar `increment_play_count` para usar `self.mark_dirty()` em vez de `self.save()` síncrono.

### Task 4: Testes Abrangentes em `tests/test_c_persistence.py`
- Testar boot inicial com `schema_version = 1`.
- Testar migração v0 para v1 com criação de `.bak`.
- Testar coalescência de write-behind (múltiplas chamadas de `mark_dirty` geram 1 gravação).
- Testar que `flush()` persiste imediatamente dados pendentes.
- Testar que falha na escrita mantém `_dirty = True` e não deixa resíduos `.tmp`.
- Testar que `increment_play_count` marca dirty sem travar I/O síncrono.

### Task 5: Verificação, Revisão e Merge
- Executar `uv run pytest -q`.
- Executar `node --test tests/test_fuzzy.test.cjs`.
- Executar `python -m compileall` e `git diff --check`.
- Revisão independente.
- Merge `--ff-only` na `main`.
