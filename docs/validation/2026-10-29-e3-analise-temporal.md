# E3 — análise temporal e trabalhos independentes

Estado: implementação congelada para revisão Sol e depois Astra. Nenhum staging, commit, reset, download de modelo ou uso de áudio pessoal.

## Entrega

- `transcript_model.py`: resultado temporal com palavras/segmentos reais, identidades estáveis por análise e samples na fonte canônica a 48 kHz; projeção nos spans; texto legado não alinhado; correção em bloco com intervalos preservados; substituição limitada ao alvo.
- `analysis_jobs.py`: fila local de capacidade 8, prioridade para editor aberto, worker serial, reserva antes de incrementar geração, estados consultáveis, cancelamento lógico e histórico em memória limitado a 128 trabalhos. Inferência cancelada continua ocupando capacidade/handle até terminar. Encerramento não aplica resultado tardio.
- `ai_analyzer.py`: `word_timestamps=True`, configuração de modelo/dispositivo/beam/vocabulário/contexto, carga exclusivamente local e fallback CPU identificado. Lock reentrante global serializa também consumidores legados e instâncias diferentes. Diarização pode executar sem ASR. Metadados distinguem `heuristic` de `local_llm`.
- `store.py`: aplicação sob lock com geração por etapa, draft ID/generation, source/revision e versões/proveniência de campos publicados e do draft. Alterações no texto também invalidam metadados em andamento. Propostas humanas exigem aceite com token revalidado. Falha de persistência reverte a entrada afetada. Reabertura pública invalida análise antiga. Mantém até 16 propostas por clipe.
- `main.py`: endpoints por etapa, consulta/listagem/cancelamento/aceite, leitura/correção temporal, aquisição/liberação da origem. Retranscrição admite até cinco segundos de contexto lateral, mas aceita somente o intervalo solicitado. `reanalyze_clip_ai` agora devolve job assíncrono; estados E1 de falha parcial/warnings continuam disponíveis na consulta.
- `ui/analysis.js`, `ui/index.html`, `ui/app.js`: painel utilizável com quatro etapas, intervalo/contexto, configuração local, estado, cancelamento e prévia/aceite de proposta. Resultados não reabrem nem substituem o formulário silenciosamente. Botão explícito carrega campos já aplicados ao draft. Autosave compara campos dentro da fila contra baseline da sessão, preservando reversões e trocas de editor; regra E2 `editTrimDirty` permanece.
- `config.py`: parâmetros ASR persistidos; validação ocorre antes de substituir o arquivo de configuração.

Os novos dados são campos aditivos de clipes/drafts dentro do schema 2 existente. Não há nova coleção nem migração destrutiva; mapas E2 são preservados. Texto antigo permanece utilizável e só perde a condição de não alinhado após aceite de uma análise integral. Uma seleção sobre texto não alinhado é rejeitada explicitamente, pois não há âncora segura para preservar o texto externo.

## TDD e verificação

Testes novos: `tests/test_transcript_model.py` (5), `tests/test_analysis_jobs.py` (4), `tests/test_analysis_apply.py` (23) e `tests/test_analysis_ui.test.cjs` (4).

Ciclos RED observados antes das implementações: contratos temporais/fila (7 falhas); apply/ASR (8); integração/configuração/projeção (4); rollback/reabertura/serialização/capacidade cancelada (4); reserva de fila e âncoras com lacuna (2); preservação de texto não alinhado/config inválida/C2 queued (3); painel e baseline UI (2); reversão durante autosave (1); troca de editor com autosave pendente (1). Os grupos correspondentes passaram após a alteração. Os demais casos consolidam aceitação de comportamentos já implementados.

O teste E1 em `tests/test_analysis_status.py` foi adaptado ao job/poll, mantendo os asserts de falha parcial, warnings e texto anterior preservado. Em `tests/test_edit_draft.test.cjs` somente a extração da função foi ajustada à assinatura com parâmetro; os três comportamentos E2 continuam testados.

Verificação local por AST da instalação: faster-whisper **1.2.1**, com `word_timestamps`, `hotwords`, `initial_prompt` e `beam_size` na assinatura real de `WhisperModel.transcribe`.

Gate anterior à primeira revisão Sol, em `D:\repos\soundboard-ai`, com `PYTHONDONTWRITEBYTECODE=1` e `NODE_BINARY=C:\Users\Hylex\AppData\Local\Author Software\nvm\installs\v24.21.0\node.exe`:

| Comando | Resultado |
|---|---|
| `.venv\Scripts\python.exe -m pytest -q --basetemp=.worktrees/e3-final` | **365 passed**, 12,52 s |
| Node 24.21.0 `--test` sobre todos `tests/*.test.cjs` | **18 passed** |
| Node `--check` em `ui/analysis.js`, `ui/app.js`, `ui/fuzzy.js` | exit 0 nos três |
| `git diff --check` | exit 2, somente `tests/test_e_mixer.py:431: new blank line at EOF` preexistente, não alterado |

Testes sintéticos e stubs: incluem edição humana durante inferência bloqueada, waveform disponível, cancelamento, liberação de handle, contexto externo excluído, proteção contra job antigo, proposta obsoleta, fila cheia, falha de disco e ASR legado concorrente. Não medem qualidade de reconhecimento, GPU, áudio real ou ganho de modelo. Smoke WebView2 e revisões externas ficam sob coordenação do agente principal.

## Contratos para smoke/revisão

- Construção isolada: `SoundboardAPI(data_dir=..., start_background_services=False)` conserva ausência de captura/prewarm/hotkeys. Fila inicia somente no primeiro trabalho; encerrar com `api._analysis_queue.close()`, `api._audio.shutdown()` e `api._store.close()`.
- Injeção sintética mínima: substituir `api._ai.transcribe_temporal(audio_16k, analysis_id, source_id, interval)` por função que retorne `status`, `text`, `alignment`, `words`, `segments`, `corrections`; tempos em samples de origem. Modelo/dispositivo/parâmetros podem ser incluídos para testar a exibição. Um `threading.Event` permite manter o job em `running` enquanto a UI edita/cancela.
- Endpoint principal: `request_clip_analysis(clip_id, stage='transcription', interval=None, context_seconds=0, priority=True)`. Etapas: `transcription`, `metadata`, `speakers`, `acoustics`.
- Consulta: `get_analysis_job(analysis_id)` e `list_analysis_jobs(clip_id=None)`. Cancelamento: `cancel_analysis_job(analysis_id)`. Aceite: `accept_analysis_result(clip_id, analysis_id, accept_token)`.
- Preparação E4: `get_clip_transcript(clip_id)` e `correct_clip_transcript(clip_id, word_ids, text)`.
- Seletores: `analysisPanel`, `analysisStage`, `btnStartAnalysis`, `analysisStatus`, `analysisRuntime`, `analysisAlignment`, `btnCancelAnalysis`, `analysisProposal`, `btnAcceptAnalysis`, `btnLoadAnalysis`, `analysisSelection`, `analysisStart`, `analysisEnd`, `analysisContext`, `asrModel`, `asrDevice`, `asrBeam`, `asrHotwords`, `asrPrompt`, `btnSaveAsr`.
- `metadata` deve ser testado com `use_local_llm=False` para evitar qualquer chamada ao serviço local durante smoke sintético. A UI exige clique explícito para carregar campos após aplicação; aceitar proposta revalida a edição depois de preservar entradas pendentes.

## Insights do domínio

Uma revisão de áudio sozinha não identifica a intenção de análise: duas solicitações podem compartilhar revisão e campos, mas a segunda deve invalidar a primeira. A capacidade da fila precisa acompanhar a inferência física, inclusive cancelada, e deve ser reservada antes de alterar a geração no store. O baseline da UI pertence à sessão de edição e precisa ser atualizado dentro da fila serial; a comparação antecipada perde reversões humanas. Texto sem alinhamento não permite substituir somente uma seleção sem risco de apagar contexto externo.

## Correções da revisão Sol — novo congelamento

A revisão encontrou dois defeitos confirmados por reprodutores RED:

1. `replace_interval` excluía integralmente uma palavra/correção que apenas intersectava o alvo, perdendo texto externo. Agora rejeita um alvo que atravessa palavra, bloco corrigido ou segmento sem alinhamento interno, orientando ampliar a seleção. Aceite explícito não amplia o alvo. Segmentos que têm palavras alinhadas preservam as partes externas por meio das palavras/tempos reais; contexto textual de segmentos novos também é limitado às palavras do alvo. Correções e suas âncoras externas são preservadas. O teste da API confirma falha sem alteração de draft, proveniência ou proposta.
2. `diarizer.process` com exceção virava lista vazia e `no_speech`. `DiarizationError` agora distingue `inference_error` e `model_unavailable`. O job falha sem aplicação e conserva pessoas/segmentos anteriores. Ausência real de segmentos continua `no_speech`. Importação legada mantém o texto e informa `partial`/`diarization_error`; separar falas devolve erro estruturado e não cria clipes.

Evidência TDD: **4 falhas** nos primeiros reprodutores dos contratos, mais **2 falhas** nos consumidores legados antes das mudanças; **49 testes alvo aprovados** após as correções. Um teste adicional confirmou a conservação de âncoras externas e a exclusão do texto contextual do segmento.

Gate final após correções, com o mesmo Python/Node e ambiente isolado:

| Comando | Resultado |
|---|---|
| `.venv\Scripts\python.exe -m pytest -q --basetemp=.worktrees/e3-review-final` | **374 passed**, 14,07 s |
| Node `--test` em todos `tests/*.test.cjs` | **18 passed** |
| Node `--check` em todos os três arquivos JS da UI | exit 0 |
| `git diff --check` | somente o whitespace preexistente em `tests/test_e_mixer.py:431` |

Arquivos alterados nesta revisão: `transcript_model.py`, `ai_analyzer.py`, `main.py`, `tests/test_transcript_model.py`, `tests/test_analysis_apply.py`, `tests/test_analysis_status.py` e este relatório. Nenhum harness WebView foi alterado. Código novamente congelado para revalidação Sol e revisão Astra.

## Alinhamento misto — segunda correção Sol

O recheck encontrou uma premissa global incorreta: haver uma palavra na análise não significa que todos os segmentos tenham alinhamento interno. Isso ocultava texto externo na projeção e permitia recortar um bloco sem palavras quando outro bloco tinha palavras.

`from_segments` agora associa os IDs reais de palavras a cada segmento e registra seu alinhamento. A presença de blocos com e sem palavras resulta em `alignment=partial`. Resultados anteriores sem associação explícita usam interseção temporal por segmento para compatibilidade. A projeção conserva o texto de cada bloco sem palavras, misturando-o às palavras reais na ordem da montagem; não fabrica palavras ou novos timestamps.

Substituição e fragmentação verificam as palavras do próprio segmento. Um bloco sem alinhamento interno não pode ser atravessado, mesmo em análise parcialmente alinhada; substituí-lo integralmente continua permitido. Segmentos externos sem palavras permanecem visíveis após substituir outro segmento por palavras alinhadas. A UI informa alinhamento parcial e a presença de segmentos sem alinhamento por palavra.

TDD focal: **5 falhas Python + 1 falha JS** observadas antes da correção; depois **41 testes Python alvo + 8 JS alvo aprovados**. Inclui retorno misto realista de `from_segments`, projeções integral/externa, contexto proposto misto, substituição completa e rejeição atômica pelo endpoint de aceite.

Novo gate final, sem alteração no harness WebView:

| Comando | Resultado |
|---|---|
| `.venv\Scripts\python.exe -m pytest -q --basetemp=.worktrees/e3-mixed-final` | **379 passed**, 13,02 s |
| Node `--test` em todos `tests/*.test.cjs` | **19 passed** |
| Node `--check` nos três arquivos JS da UI | exit 0 |
| `git diff --check` | somente whitespace preexistente `tests/test_e_mixer.py:431` |

Arquivos desta rodada: `transcript_model.py`, `ui/analysis.js`, testes temporais/apply/UI e este relatório. Novo congelamento para Sol → Astra; sem commits ou staging.

## Correções da revisão de qualidade Astra

Quatro achados reproduzidos e corrigidos:

1. Corrigir somente parte das palavras de um bloco já corrigido eliminava a correção humana restante. `correct` agora exige selecionar o bloco humano inteiro antes de substituí-lo. O teste do endpoint confirma rejeição sem mutação e permite depois corrigir o bloco completo.
2. Falha ao persistir um novo pedido mantinha sua geração incrementada e tornava obsoleto o pedido anterior já aceito. `prepare_analysis` reverte somente a geração da etapa afetada (e remove o mapa se tiver sido criado vazio), sem restaurar snapshots de outros dados. O teste preserva uma alteração independente em outro clipe, comprova que o primeiro pedido ainda aplica e verifica a liberação da reserva da fila/ausência de handles vazados.
3. O botão de carregar resultado reabria o formulário mesmo quando autosave falhava. O handler real agora aborta em `{ok:false}`, `null` ou rejeição da promise, mostra o erro e conserva os valores locais sem executar refresh.
4. Metadados reutilizavam o interlocutor primário antigo mesmo após cortar toda a evidência temporal dele. O contexto agora usa o único interlocutor com segmentos canônicos que intersectam os spans mantidos; sem evidência segura ou com vários interlocutores, omite o nome automático. Proveniência humana explícita tem precedência. Isso altera apenas o contexto do pedido, sem reatribuir pessoas no clipe nem executar ASR/diarização novamente.

TDD: **3 falhas Python + 1 falha JS** antes das mudanças; o cenário de atribuição humana explícita já passava e foi preservado. Após correção: **45 testes Python focais + 9 JS focais aprovados**.

Gate final da rodada Astra:

| Comando | Resultado |
|---|---|
| `.venv\Scripts\python.exe -m pytest -q --basetemp=.worktrees/e3-astra-final` | **383 passed**, 12,81 s |
| Node `--test` em todos `tests/*.test.cjs` | **20 passed** |
| Node `--check` nos três arquivos JS da UI | exit 0 |
| `git diff --check` | somente whitespace preexistente `tests/test_e_mixer.py:431` |

Arquivos desta rodada: `transcript_model.py`, `store.py`, `main.py`, `ui/analysis.js`, `tests/test_analysis_apply.py`, `tests/test_analysis_ui.test.cjs` e este relatório. Congelado para Sol e posterior re-review Astra. Nenhum harness, staging ou commit alterado.

## Fala insuficiente — re-review Astra

O re-review aprovou as quatro correções anteriores e confirmou um caso adicional: a filtragem de segmentos menores que 250 ms podia transformar fala detectada em `no_speech`, aplicando uma limpeza de pessoas anteriores. Entrada menor que 4000 samples a 16 kHz também não era analisada e retornava o mesmo vazio.

A mudança de produção foi restrita às duas saídas de `diarize_and_identify`: entrada curta retorna `DiarizationError('insufficient_audio', ...)`; detector com fala, mas nenhum segmento elegível, retorna `DiarizationError('insufficient_speech', ...)`. A propagação C2 existente marca o job como falho, sem aplicar campos. Pessoas e segmentos anteriores permanecem. A execução válida do detector com resultado vazio continua distinta e produz `no_speech`.

TDD: **5 novos testes RED → GREEN**, cobrindo os dois motivos no job, os dois no consumidor legado de separação e o aviso parcial de importação preservando texto. **52 testes focais aprovados** após a alteração.

Gate final desta rodada:

| Comando | Resultado |
|---|---|
| `.venv\Scripts\python.exe -m pytest -q --basetemp=.worktrees/e3-short-final` | **388 passed**, 12,80 s |
| Node `--test` em todos `tests/*.test.cjs` | **20 passed** |
| Node `--check` nos três arquivos JS da UI | exit 0 |
| `git diff --check` | somente whitespace preexistente `tests/test_e_mixer.py:431` |

Alterados nesta rodada: `ai_analyzer.py`, `tests/test_analysis_apply.py`, `tests/test_analysis_status.py` e este relatório. Código novamente congelado, sem staging, commits, modelos reais ou alterações no harness.
