# E6 — qualidade local, cache e recursos

Data: 2026-10-30. Execução em `D:\repos\soundboard-ai`, Python 3.13 e Node 24.21.0. Sem staging/commit, modelos reais, downloads, treinamento neural, dispositivos de áudio ou biblioteca pessoal. Baseline funcional preservada: E0–E5 e E7–E8. Os números de fala abaixo verificam contratos com predições sintéticas; **não são qualidade de reconhecimento de gravações reais**.

## Entregas e limites

| Item | Entrega verificada | Limite |
|---|---|---|
| E6.1 | CLI de manifesto, WER bruto, âncoras correspondentes, precisão/cobertura/confusão, medições e proveniência | Métrica sem referência/medição fica `null` com motivo; ausência de previsão conta como omissão/abstenção |
| E6.2 | Mesma interface de avaliação para baseline, janelas com contexto, VAD conservador, vocabulário e modelo/beam alternativos | Runner local testado com modelo falso; só usa `local_files_only=True`; nenhuma alternativa foi promovida por dados sintéticos |
| E6.3 | Cadastro → calibração → teste, centroides versus até dois protótipos por pessoa, pares confundidos | **Calibração real pendente: usuário não possui corpus rotulado**. Limiares e modelo de produção permanecem iguais |
| E6.4 | Decode, ASR, embedding e matching com chaves por conteúdo/etapa/versão/parâmetros; bytes e itens limitados | Cache volátil, sem transcrições persistidas em cache; não promete eliminar leitura de hash nem o custo de uma nova janela |
| E6.5 | Jogando/Revisando, pausa, priorização/cancelamento na UI/API e persistência | Uma inferência serial; não preempta inferência ativa e não mede impacto no jogo |
| E6.6 | Benchmark reproduzível de replay/waveform/decode; cópia limitada à janela do replay | Waveform não alterada; tracemalloc não mede todas as alocações nativas, VRAM ou frame time |
| E6.7 | Dicionário explicitamente confirmado; preferências de ASR/recursos; origem de sugestões exposta | Termos são hotwords, nunca regras universais de substituição; acústica e categoria editorial continuam distintos |

## Reprodução da avaliação

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:SOUNDBOARD_MODELS_DIR='D:\repos\soundboard-ai\.worktrees\e6-models'
$env:SOUNDBOARD_SEPARADOS_ROOT='D:\repos\soundboard-ai\.worktrees\e6-separados'
.venv\Scripts\python.exe tools/generate_synthetic_evaluation.py --output-dir build/evaluation/synthetic
.venv\Scripts\python.exe tools/evaluate_audio_pipeline.py --manifest build/evaluation/synthetic/manifest.json --output build/evaluation/synthetic/results.json
.venv\Scripts\python.exe tools/measure_local_audio.py --output build/measurements/local-audio.json
```

`--mode predictions` é o default: lê previsões explícitas sem carregar IA. `--mode local --models-dir <pasta-local>` executa ASR e extração vocal instalados; não baixa modelos. Cada alternativa começa com cache de resultados vazio. Latência ASR inclui geração/consumo de segmentos e preparação da janela; extração vocal tem medição separada. Primeira execução pode incluir carregamento de modelo: não comparar esses números como benchmark isolado de kernel. `peak_python_bytes` exclui alocação nativa; RSS/VRAM/jogo ficam indisponíveis. Modo predictions identifica medições como `manifest_predictions`, nunca como inferência medida.

Manifesto: objeto com `synthetic`, `configurations` e listas `enrollment`, `calibration`, `test`. Cada entrada exige `id`, `capture_id`, `session_id`, `source_path` relativo ao manifesto e `interval: [início,fim)` em amostras canônicas de 48 kHz. Rótulos disponíveis ficam em `labels.text`, `labels.speaker_id` e `labels.words` (`text`, `start_sample`, `end_sample`). Speaker vazio rotulado significa desconhecido e continua no denominador. As predições por alternativa ficam em `predictions.<id>` com `text` bruto, `speaker_id`, palavras e medições opcionais. `corrected_text` não entra no WER. O gerador contém exemplo completo e reproduzível.

Alternativas locais aceitam `model`, `device`, `beam_size`, `language`, `hotwords`, `initial_prompt`, `vad_filter`, `speech_windows` e `context_seconds` (0–5). Janelas de fala vêm de `entry.speech_windows`, em coordenadas da fonte, dentro do intervalo avaliado; não são derivadas das palavras de referência. Contexto une janelas sobrepostas para evitar duplicação. VAD conservador usa 500 ms de silêncio mínimo e 400 ms de padding. Modelo diferente deve estar instalado. Falha de modelo é contada como falha, não evidência de ausência de fala.

A CLI rejeita captura/sessão repetida entre partições, mesmo caminho resolvido, mesmo hash de arquivo/PCM, trechos idênticos e sobreposições exatas detectáveis mesmo com IDs/arquivos novos. A comparação de PCM usa normalização mono/48 kHz e amostras idênticas; não é um identificador forense de regravações, efeitos ou codecs com perdas. Esses casos exigem IDs honestos de captura/sessão. Intervalos fora da fonte e âncoras inválidas são rejeitados. Saída inclui hashes de arquivo/PCM, manifesto e avaliador, configurações e extrator. O relatório não inclui transcrições de cada gravação.

Para voz offline, entradas incluem `embedding` e `extractor_version` iguais/compatíveis. Modo local extrai vetores e substitui qualquer vetor fornecido, sem misturar extratores. Protótipos usam apenas enrollment; limiar/margem são escolhidos apenas na calibração pela função explícita `2*atribuições_erradas + abstenções`, e depois congelados no teste. Há no máximo dois protótipos por pessoa; não há número ilimitado de comparações favorecendo quem tem mais exemplos. A grade inteira, seleção e métricas de teste ficam no relatório. A função é um critério experimental registrado, não política de produção validada.

O smoke independente do coordenador (`.worktrees/e6-parent-evaluation-421cwbx5`) conferiu: WER 2/9 nas duas alternativas; baseline com precisão 0,5 e cobertura 2/3, conservadora com precisão 1 e cobertura 1/3; ambas com acerto no total 1/3. Erro temporal de uma palavra correspondente: 10 ms. A abstenção não produziu melhoria escondida. Esse smoke usa ruído WAV e predições controladas, sem conteúdo falado correspondente. Rejeições de capture/session/conteúdo/cópia/intervalo também passaram.

## Cache e evidência vocal

`analysis_cache.py` guarda somente bytes serializados internamente, limitados por payload e número de itens; nenhum pickle é lido de arquivo ou rede. Overhead do mapa/chaves fica limitado pelo número de itens. Leituras desserializam objetos independentes; edição do consumidor não altera o cache. Decode: 32 MiB/32 itens; inferência: 64 MiB/128 itens por analisador; matching: 2 MiB/256 itens. Item maior que o orçamento não entra. O LRU de reprodução existente permanece separado.

Decode usa hash do arquivo e versão mono16k/polifásica; ASR usa hash do PCM da janela, comprimento, configuração/modelo/pacote/runtime e identidade de arquivos locais; embedding usa PCM e versão/hash do extrator; matching inclui versão de perfis/banco e limiar/margem. Correções editoriais e troca de pessoas não invalidam decode/ASR. IDs de análise/fonte e âncoras são recriados ao projetar segmentos ASR cacheados: o resultado nunca conserva tokens editoriais antigos. E3 continua protegendo campos humanos e propostas tardias.

No editor, análise de pessoas pode reutilizar **segmentos completos** da fonte retidos na montagem, com versão do extrator igual, âncoras válidas e cobertura contínua sem sobreposição. O vetor conserva seus intervalos reais de extração/cluster (`embedding_source_id`/`embedding_source_spans`); toda essa evidência precisa permanecer dentro do segmento retido. Não combina vetores de clusters distintos. Fragmento projetado sobre junção não vira evidência independente: se parte da origem do vetor foi removida, é necessária reinferência. Recorte dentro de segmento, gap, extrator novo ou evidência legada sem proveniência explícita também exige inferência. `source.voice_evidence` de E5 permanece intacta. Reutilização para matching não cadastra automaticamente referência. O smoke independente `.worktrees/e6-api-cache-smoke.py` verificou cache ASR através de API/fila/Store reais, correção humana preservada, proposta com ID novo e hotwords novos causando reinferência; nenhuma lease vazou.

## Recursos e interface

`get_local_quality`, `set_resource_policy(profile, paused)`, `set_analysis_priority(id, prioritized)` e `save_personal_dictionary(terms, confirmed)` são os contratos públicos. Perfis e pausa persistem em `config.json`. Jogando adia prioridade de segundo plano; ações explícitas de análise/importação/separação/cadastro continuam permitidas. Aprendizado opt-in derivado de correção é segundo plano; cadastro explícito é prioritário. Pausa impede início de fila e rejeita entradas síncronas legadas; inferência já ativa pode concluir. Priorizar não remove pausa. Cancelamento/close liberam leases de jobs aguardando; resultados ativos cancelados não são aplicados.

Captura de replay permanece disponível durante a pausa: grava e valida PCM, cria clipe/rascunho editável com `analysis_status=deferred`, aviso e `capture_analysis.reason=analysis_paused`. Não executa ASR/LLM, não inventa transcrição, interlocutor ou descritor acústico e não cadastra voz. A categoria padrão de captura (`Call`) é marcada `capture_default`, sem apresentá-la como previsão. O aviso histórico reaparece no editor. Cada captura usa nome com UUID, evitando sobrescrita em duas capturas no mesmo segundo. Erro de decoder permanece falha, não sucesso adiado.

Painel do editor `localQualityPanel`: `qualityProfile`, `qualityPaused`, `qualityDictionary`, `qualityConfirm`, `btnSaveQuality`, `btnRefreshQuality`, `qualityStatus`, `qualityJobs`. Botões de job usam `data-action=cancel|prioritize` e `data-job-id`. Dicionário aceita até 100 termos de 80 caracteres e exige confirmação quando alterado. O usuário continua escolhendo explicitamente modelo, beam, contexto e dispositivo. Descritores de sinal não são apresentados como certeza emocional; categoria permanece sugestão editorial editável.

Smoke independente WebView real, viewport 768: `.worktrees/e6-webview-data-zi0s4jp5/editor.png`. Verificados tema/legibilidade, recusa de dicionário não confirmado, persistência após reinício, job acústico real pausado, priorização sem furar pausa, cancelamento e leases zeradas. Sem erro JavaScript. Não houve áudio físico nem inferência real.

## Medições antes/depois

11 repetições sintéticas, mesmo PCM, sem modelos ou reprodução. Baseline `.worktrees/e6-baseline-measure.json`; after independente sem startup de UI concorrente `.worktrees/e6-after-isolated-measure.json`. A otimização foi motivada por cópia comprovada de 120 s para retornar apenas 3 s. `tools/measure_local_audio.py` reproduz também a implementação anterior por concatenação integral, confere PCM idêntico e mede a atual.

| Operação | Baseline mediana / pico traced | Atual mediana / pico traced |
|---|---|---|
| Replay 15 s → últimos 3 s | 0,7400 ms / 5.765.200 B | 0,1603 ms / 1.157.304 B |
| Replay 120 s → últimos 3 s | 6,2695 ms / 46.118.800 B | 0,1594 ms / 1.157.304 B |
| Replay integral 120 s | 6,2338 ms / 46.118.800 B | 6,3581 ms / 46.282.168 B |
| Waveform 15 s / 160 bins | 11,5218 ms / 8.641.184 B | 11,3272 ms / 8.641.184 B |

Retorno do replay continua sendo PCM independente, no limite do buffer e sob lock; wrap/trim, integral e janela foram testados. O caminho integral não ficou mais rápido. Waveform permaneceu igual: não houve mudança sem requisito demonstrado. Números variam por máquina/carga; não extrapolar para latência de jogo ou ganho de reconhecimento.

Leitura repetida medida depois da representação final em bytes do cache (`.worktrees/e6-cache-final-measure.json`): decode mono16k de WAV estéreo sintético de 15 s sem cache, 13,6581 ms / 8.772.739 B; com cache, 3,3959 ms / 2.106.358 B. Ambos incluem hash de conteúdo e filesystem aquecido. O resultado continua PCM independente e numericamente igual. Isso mede este decode local, não ASR nem disco frio.

Logs de análise usam allowlist de códigos/métricas numéricas (`duration_ms`, fila, cache). Texto, prompt e caminho enviados à função de métricas são descartados; bibliotecas de IA ficam em WARNING por padrão. Relatórios de avaliação são artefatos explícitos e `build/evaluation/`/`build/measurements/` estão ignorados.

## Gates e desvios

- RED comportamental: inferências idênticas repetidas, cópia integral de replay, falta de política/priorização e matching sem cache. Testes de novos módulos inicialmente também reportaram ausência de API; isso foi separado das evidências comportamentais.
- 149 testes focados de análise, vozes, configuração e logging aprovados; 22 testes E6 finais aprovados antes do gate completo.
- Dois testes existentes ajustados às novas invariantes: serialização força PCM diferente para realmente executar duas inferências; teste FFmpeg mock cria bytes de entrada para permitir hash real de conteúdo.
- Gate Python completo final: **562 testes aprovados em 29,38 s**, log `.worktrees/e6-full-final-2.log`. Gate JavaScript: **55 testes aprovados**, log `.worktrees/e6-js-final.log`.
- `git diff --check`: único aviso preexistente `tests/test_e_mixer.py:432`, linha vazia no EOF; nenhuma correção fora do escopo.
- Não houve download/treino/calibração em corpus pessoal, nem promessa de ganho percentual de IA. A seleção real de modelo/limiar permanece pendente de dados independentes.

Revisão Sol: dois achados reproduzidos em RED e corrigidos. Comparação vocal agora mantém todos os casos rotulados de calibração/teste, mesmo sem embedding: extração indisponível conta como abstenção e aparece em `embedding_availability` por partição. Fixture com um acerto e uma falha de extração resulta em cobertura/acerto total 0,5, não 1. Janelas de fala são validadas, ordenadas e então unidas após contexto; permutação de janelas não descarta fala anterior. Três regressões verificam ordem, união de contexto e limite da fonte. Gate focado posterior: **27 testes aprovados**; o gate completo acima precede esses ajustes e será renovado no fechamento das revisões.

Revisão do coordenador: pausa bloqueava a publicação do replay depois de gravar WAV. Seis regressões RED→GREEN cobrem clipe/rascunho pausado, pausa ocorrendo durante a captura, duas capturas no mesmo segundo com PCM distinto e falha real de decoder. A captura adiada preserva edição manual/fonte e não aprende vozes. **34 testes de replay/política aprovados** após a correção.

Revisão Astra: reuso de fragmentos de uma fala atravessando junção e configuração concorrente ao cache foram reproduzidos em RED. A origem completa do embedding agora é registrada antes da projeção; remover metade de uma fala montada força nova extração, enquanto manter o novo segmento contíguo completo permite reuso. Atualização de configuração usa o mesmo RLock global da inferência; metadados, chave e parâmetros pertencem à mesma execução. A fachada serializa persistência/publicação de configurações com lock próprio, sem segurar lock do store enquanto aguarda inferência, e publica parâmetros mais dicionário em uma única chamada. Corrida adicional de dois saves de dicionário também reproduzida em RED: disco e runtime agora terminam com o mesmo valor. **137 testes focados aprovados**, seguidos de **22 testes de cache/política aprovados** após consolidar a publicação única. Gate completo reservado ao fechamento das revisões.

Insight confirmado: identidade de cache deve representar trabalho acústico, não rótulos editoriais. A redução medida do replay vem de copiar menos PCM; qualidade de reconhecimento exige outro tipo de evidência. Alterar taxa de abstenção não demonstra melhoria sem observar simultaneamente cobertura, confusões e acertos no total.
