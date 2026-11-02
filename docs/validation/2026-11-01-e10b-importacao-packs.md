# E10B — entrada de mídia e packs de grafo

Data: 2026-11-01. Base integrada: `4deb17d18b5b6073c786f96f32bd51703bf305fa`, acrescida dos ajustes aprovados de E10A já presentes no worktree. Alterações restritas ao worktree `C:\Users\Hylex\.codex\worktrees\soundboard-evolution\soundboard-ai`. Sem staging/commit, sem edição da instalação pessoal em `D:\repos\soundboard-ai`, sem reiniciar seus serviços.

## Estado da entrega

**Concluído e revisado:** Sol SPEC_PASS e Astra QUALITY_PASS após as correções descritas neste relatório. Gate completo do coordenador: **728 Python + 77 JavaScript**, sintaxe UI e diffcheck aprovados. Este relatório registra também as execuções direcionadas intermediárias; a [entrega integrada](2026-11-01-entrega-evolucao.md) consolida os resultados finais e os limites de hardware/modelos reais.

## Entrada unificada

- `import_queue.py` implementa fila persistente por item, descoberta limitada, probe de cabeçalho, conversão de janela, histórico, progresso, cancelamento, retry e identidade SHA-256 do conteúdo com janela normalizada em amostras. Arquivos iguais com nomes diferentes são duplicatas; janelas diferentes da mesma mídia são entradas distintas.
- `enqueue_import(paths=None, skip_speaker_id=False, forced_category=None, folder=False, auto_analyze=None)` aceita arquivos, pasta e o callback nativo de drop. `None` em `auto_analyze` usa a preferência atual, cujo padrão é análise automática.
- `list_import_jobs`, `cancel_import_job`, `retry_import_job`, `set_import_window`, `retry_import_analysis` e `apply_import_metadata` expõem os estados e operações. Cancelamento e publicação final compartilham o lock; clipe e histórico são persistidos no mesmo save. Cancelar análise após a publicação mantém o áudio importado.
- Estados de mídia: `queued`, `probing`, `awaiting_window`, `running`, `success`, `duplicate`, `failed`, `cancelled`, `interrupted`. Estado da IA é separado em `job.analysis`.
- Cada tentativa de decode tem `attempt_id` e arquivo de preparo próprios; cada execução do pipeline IA tem `generation`. Após probe/hash/decode/consulta/submissão, o worker resolve novamente o item atual pelo ID e token sob os locks antes de alterar ou publicar. Cancelamento seguido de retry não aceita resultado nem erro da tentativa anterior; submissão E6 retornada tardiamente é cancelada. Rollbacks próprios conservam a lista/registros/estado de análise vivos; a resolução pelo ID também cobre substituições externas do estado.
- A admissão e a aplicação E6 também verificam a geração importada, independentemente de o ID da submissão já ter retornado. Um escopo interno `ContextVar` captura callbacks por submissão em `AnalysisQueue.submit`; a aplicação verifica ID/geração/estado atuais e mantém `store._lock` até o callback de aplicação terminar. Ordem de locks: condição E6 → store, sem adquirir o lock da fila de mídia nesse callback. O escopo é restaurado em `finally` e não modifica a assinatura pública nem outros pedidos manuais/threads.
- Acima de 120 s, a entrada espera uma janela de até 120 s; `sf.info`/`ffprobe` determinam duração sem decodificação integral. Leitura nativa usa `start/stop`; FFmpeg usa `-ss` e `-t`. Vídeo exige FFmpeg e ffprobe e uma faixa de áudio reconhecida.
- Limites: 512 itens mantidos/admitidos por lote, 64 eventos de histórico por item, descoberta interrompida após 10.240 entradas, mapa de falas de até 8 MB. Históricos terminais antigos podem sair da lista ao admitir novos lotes, mas mídia concluída com análise ainda ativa não pode ser descartada. Dedupe publicado permanece no clipe.
- O adaptador `separados/mapa_de_falas.json` conserva transcrição e identificação fornecidas, recusa escape da pasta e evita reidentificar automaticamente o interlocutor explícito. Não adiciona exemplos ao banco vocal.
- Análise automática usa as etapas E6 sequenciais com prioridade de fundo: transcrição, pessoas quando aplicável, acústica e metadados. Perfil de jogo/pausa segura as etapas; áudio permanece disponível. Resultado/proposta usa as gerações e proveniência do E6. Falha não inventa texto ou pessoa. Análise manual é opção explícita.
- Categoria forçada e metadados definidos pelo operador têm proveniência humana. Metadados em lote comparam versões e valores de publicação, além de edições humanas abertas, antes de escrever. Campos alterados posteriormente são preservados e reportados como conflitos.
- Reinício marca trabalho incompleto como interrompido e não retoma inferência automaticamente. O worker é iniciado sob demanda. `api._imports.close()` cancela coordenação/análises e deve ser chamado antes de fechar o store em harnesses.

## Interface e drop

- `ui/imports.js` controla `#importQueue` e `#importQueueItems`, com estado, progresso, histórico, janela, cancelamento, retry, seleção para metadados e botão “Abrir no editor”. A fila também contém “Adicionar arquivos” e “Adicionar pasta”, acessíveis na largura de 768 px quando os botões antigos do cabeçalho são ocultados.
- A atualização é incremental por ID/assinatura: progresso em outro item não substitui os controles em edição. Digitação parcial, seleção, foco, checkbox e histórico aberto são mantidos; atualização externa da própria linha aguarda o término da edição do campo temporal. O popover Pack usa posicionamento fixo com limites da viewport, recalculado na abertura e resize.
- Importação por arquivos, vinhetas, pasta e chips de pastas conhecidas usa a mesma fila. A área Tocar oculta a fila; Preparar a mantém disponível.
- `#importAutoAnalyze` controla a preferência também para drop; `#importQueueMode` oferece áudio/fala ou vinheta para os botões da fila.
- Versão instalada verificada: pywebview 6.2.1. Integração Python usa `webview.dom.DOMEventHandler`, `window.dom.document.events.drop` e `dataTransfer.files[].pywebviewFullPath`, com `prevent_default`. Nome de arquivo do DOM comum não é usado como caminho. Callback apenas admite entradas; decode e inferência ficam fora da thread do evento.

## Packs v2

- `pack_graph.py` define manifesto `version: 2`, `mode: compact|editable`, `voice_policy: excluded`, `graph`, `files` e `roots`. O grafo contém tabelas de clipes, origens, revisões, sequências, coleções e rótulos de pessoas.
- `export_pack(clip_ids, path, mode, collection_ids, sequence_ids)` seleciona o modo v2 explicitamente. A UI sempre informa o modo. Chamada legada com apenas dois argumentos conserva o escritor v1 para compatibilidade; leitura v1 permanece disponível.
- Biblioteca completa exporta todas as raízes. Filtro de clipes não inclui automaticamente outras coleções/fluxos. Seleção de coleção ou sequência inclui somente suas dependências necessárias, com resumo de contagens.
- Compacto renderiza a revisão escolhida, cria fonte contendo somente esse resultado e revisão neutra (`gain_db=0`, `normalization_gain=1`, `fade_samples=0`). Ganho do clipe e volume de ocorrência permanecem nas respectivas referências, sem reaplicar efeitos processados.
- PCM idêntico reutiliza a mesma fonte e codificação dentro do pacote. O timestamp do chunk WAV `PEAK` é zerado na exportação renderizada; uma mudança de segundo no relógio não troca o hash da mesma mídia nem quebra revisões que compartilham a fonte.
- Ocorrências de sequência são reescritas para nós que contêm apenas o intervalo daquela revisão renderizada. Isso permite revisão antiga fixada, variantes e intervalos locais sem transportar a origem completa nem o clipe atual fora das raízes selecionadas. Intervalos tornam-se `[0, tamanho]`, preservando duração, pausas, paralelo/manual e volume local.
- Texto compacto só transporta unidades com âncoras integralmente presentes nos spans e no intervalo exportado. Texto não alinhado, segmento parcialmente cortado e correção cujas âncoras foram removidas são omitidos. Não há fallback para a transcrição da revisão atual ao exportar um pin antigo.
- Backup editável inclui fontes e histórico de revisões/receitas das origens selecionadas, incluindo proveniência de variantes. Transporta texto temporal editável, mas exclui embeddings, banco vocal, exemplos confirmados e `voice_action_id`.
- IDs são remapeados por tabela; referências internas, variantes, ocorrências e coleções seguem o mapa retornado em `id_map`. Atalhos são normalizados e conflitos com globais, clipes, sequências e coleções existentes são removidos do item importado, com aviso.
- `summary` informa modo, clipes, fontes, revisões, sequências, coleções e política vocal. Importação repetida do mesmo manifesto é sem efeito enquanto seus nós importados ainda existem; edições locais não são revertidas. Se algum nó foi removido, uma nova cópia integral pode ser importada e recebe novo mapa.
- Configurações/roteamento do destinatário não são campos do grafo. Importação não altera mic, cabo, monitor, `replay_source_name`, preferências nem banco vocal. Pessoas importadas têm IDs novos e são apenas rótulos.
- Validação precede publicação: formato/versão, manifesto até 8 MB, 4.096 nós/arquivos, até 2 GB descompactados, caminhos, duplicatas, links, mídia declarada, tamanho, SHA-256, PCM estéreo 48 kHz/amostras finitas, receitas conhecidas, referências, spans, variante, ciclos, sequências e coleções. Transcrição temporal valida versões, referencial, listas, IDs únicos, texto, tempos inteiros na origem, âncoras e referências reais entre palavras/segmentos/correções; metadados do clipe também têm tipos verificados. Extração usa diretório temporário interno; publicação usa um save sob lock e remove arquivos novos em rollback.
- Schema local continua 6: `import_jobs`, `import_identity` e `pack_imports` são metadados aditivos, sem reinterpretar fontes/revisões existentes. Retry de `store._perform_save` do Windows não foi alterado.
- Sequência manual continua sendo grafo de execução; não foi apresentada como exportação de um único arquivo de áudio (E11 permanece fora do lote).

## Evidência TDD e checks

Execuções com `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH` apontando ao worktree C:, modelos/separados sob `.worktrees/e10b-tests`, Python read-only da instalação pessoal e o harness `.worktrees/integration-audio/run_pytest.py` que bloqueia áudio nativo. Node foi invocado pelo executável real v24.21.0, sem depender do shim NVM.

Casos observados RED antes da implementação/fix:

1. Fila ausente: 4 falhas para isolamento/dedupe/retry, seleção de janela, cancelamento durante decode e proteção contra edição humana.
2. Packs de grafo ausentes: 4 falhas para compacto/editável, rollback e filtro.
3. Status/pipeline automático ausente: falha, seguida de GREEN com política pausada e etapas sequenciais de fundo.
4. Sequência isolada recusada e segmento cortado vazando texto: 2 falhas, seguidas de GREEN.
5. UI da fila ausente: 3 falhas, seguidas de GREEN.
6. Edição humana aberta não protegida, descoberta de pasta sem limite precoce, preferência de drop ausente: 3 falhas, seguidas de GREEN.
7. Janela inteira vs float duplicando conteúdo e falha no save final deixando item ativo: 2 falhas, seguidas de GREEN com identidade em amostras e publicação única.
8. Receita futura e PCM NaN aceitos: 2 falhas, seguidas de GREEN.
9. Categoria forçada com proveniência automática: falha, seguida de GREEN preservando intenção humana e mapa separados.
10. Botões da fila indisponíveis no controller para uso sem cabeçalho: falha JS, seguida de GREEN.
11. Poll substituindo linha em digitação e popover fora da viewport: 2 falhas JS, seguidas de GREEN com atualização incremental e clamp em 768/360 px.
12. Revisão Sol: 15 corrupções temporais e 10 metadados inválidos aceitos: 25 falhas, seguidas de GREEN, mantendo transcrição temporal válida e texto legado usáveis após round-trip.
13. Revisão Sol: item de mídia concluído com análise pausada removido pelo limite do histórico: falha, seguida de GREEN com preservação do pipeline e admissão posterior após conclusão.
14. Smoke do agente principal: cabeçalho WAV variável sobrescrevia fonte compartilhada, invalidando a revisão anterior. Dois testes sem sleep reproduziram hash variável/reencode duplicado e passaram após reuso por PCM e timestamp `PEAK` determinístico.
15. Revisão Astra: cancelamento seguido de retry durante decode aceitava conclusão/erro da tentativa antiga; rollback de metadados durante decode descolava o registro vivo; cancelamento/retry durante submissão E6 aceitava o ID antigo. Quatro testes duráveis RED confirmaram os casos e passaram após tokens por tentativa/geração, nova resolução do registro e rollback preservando referências. Acrescentados checks de rollback durante submissão IA e falha de publicação seguida de retry/reinício.
16. Revisão Sol do delta confirmou que um resultado E6 real ainda podia aplicar antes do retorno do ID. Dois testes com `request_clip_analysis`, `AnalysisQueue` e aplicação do store reais falharam (barreira antes da admissão e após admissão/antes de aplicar), e passaram após guarda atômica de admissão/aplicação. Mais dois testes confirmam reset do escopo em retorno/exceção e isolamento de thread/pedido manual seguinte.

Última execução direcionada do implementador:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH=(Get-Location).Path
$env:SOUNDBOARD_MODELS_DIR=Join-Path (Get-Location) '.worktrees/e10b-tests/models'
$env:SOUNDBOARD_SEPARADOS_ROOT=Join-Path (Get-Location) '.worktrees/e10b-tests/separados'
$env:NODE_BINARY='C:\Users\Hylex\AppData\Local\Author Software\nvm\installs\v24.21.0\node.exe'
& 'D:\repos\soundboard-ai\.venv\Scripts\python.exe' .worktrees/integration-audio/run_pytest.py tests/test_import_queue.py tests/test_graph_packs.py tests/test_import_results.py tests/test_f2_packs.py tests/test_analysis_jobs.py .worktrees/astra-e10b/test_review_races.py -q --basetemp=.worktrees/e10b-tests/pytest-guarded
& $env:NODE_BINARY --test tests/test_imports_ui.test.cjs
git diff --check
```

Resultado: **122 Python passaram** (119 da suíte direcionada durável, incluindo testes existentes da fila E6, e 3 reproduções do revisor Astra), **6 JavaScript passaram**, `git diff --check` limpo. Os testes adversariais adicionais confirmam hash/ciclo/path, correção temporal parcial, validação temporal/metadados, cancelamento/close durante inferência simulada, retry concorrente, guarda E6 antes do retorno do ID e independência da fila de mídia. Alterações congeladas para revisão Sol do delta, nova revisão Astra e gate final coordenado pelo agente principal.

## Smoke integrado coordenado pelo agente principal

Resultados comunicados pelo agente principal, separados da execução direcionada acima:

- `.worktrees/e10b-pack-smoke.py`: compacto/editável com comparação `np.allclose` do áudio atual, variante, revisão antiga fixada e ocorrência paralela; manual, ordem, posições, shuffle/cooldown; colisão de atalhos; roteamento/banco vocal intactos; repetição idempotente; grafo inválido sem publicação. Compacto sem marcador privado e sem componente de 2.200 Hz removida; backup mantém origem de 3 s; fontes originais mantêm hash.
- `.worktrees/e10b-import-smoke.py`: WAV inválido/duplicado/MP4 real/arquivo ausente no mesmo lote; retry após reparo; probe de FLAC 130 s sem decode antes da janela; janelas distintas; cancelamento durante decode; proteção humana; pausa/resume com fila E6 real e transcritor/metadados fake; restart mantém IDs sem iniciar inferência.
- Primeiro smoke WebView de 768 px identificou falta de botão de admissão responsivo; corrigido com botões dentro da fila e ocultação da fila em Tocar. Reexecução visual e gate final ficam no relatório integrado do agente principal.

## Arquivos deste lote

- Novos: `import_queue.py`, `pack_graph.py`, `ui/imports.js`, `tests/test_import_queue.py`, `tests/test_graph_packs.py`, `tests/test_imports_ui.test.cjs`, este relatório.
- Integração: `main.py`, `packs.py`, `analysis_jobs.py`, `ui/app.js`, `ui/index.html`, `ui/styles.css`, `ui/collections.css`.
- Mudanças prévias em E10A, guia, plano consolidado e relatórios do agente principal não são trabalho deste lote e foram preservadas.

## Limites da evidência

Os sinais são sintéticos. Não foram usados modelos, vozes, biblioteca ou hardware pessoal. A verificação não mede qualidade perceptual, precisão de diarização/ASR nem reprodução em cabo/microfone físico. A análise E6 deixa campos aplicados/propostas no rascunho do editor e respeita seu fluxo de salvar/aceitar; não confirma aprendizado vocal. Leitura v1 mantém os contratos antigos; garantias transacionais do grafo v2 não reescrevem o formato legado.
