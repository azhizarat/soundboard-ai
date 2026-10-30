# E5 — aprendizado vocal supervisionado e reversível

Data: 30/10/2026. Implementação concluída; achados de Sol, quatro achados de Astra e recuperação de aprendizado interrompido corrigidos, código congelado para revalidação Sol → Astra. Sem staging/commit/reset. Baseline: E4 aprovado, incluindo alterações locais anteriores; esta entrega não parte apenas de HEAD.

## Entrega

- Schema v3 com `voice_bank` versionado. Migração v2 não reconstrói IDs de fontes/revisões; preserva coleções/metadados E2/E3 e grava backup específico `soundboard.v2.bak.json`, sem substituir `.json.bak` anterior. Versões futuras continuam rejeitadas.
- Amostras individuais: pessoa global, source/revision imutáveis, intervalos em amostras a 48 kHz, ação humana, versão do extrator, embedding, qualidade/razões/elegibilidade, peso e uso no perfil. Referências nomeadas `voice:<sample_id>` retêm fonte; snapshot da revisão permite audição após remover o clipe.
- Correção normal pelo Salvar, com **Aprender com minhas correções** habilitado, cria ação rastreável e agenda extração na fila limitada existente. Cadastro explícito funciona independentemente da preferência. A atribuição humana é persistida antes de tentar extrair a referência, e permanece quando a fila/modelo/fonte falha.
- Somente operações humanas chamam o cadastrador. Importações, histórico, inferência e revisão de correspondências não cadastram referências confirmadas. Ações registram par confundido anterior→corrigido para diagnóstico.
- Seleção do editor é intersectada com os trechos mantidos. Captura com múltiplas vozes exige intervalo. Mistura detectada pelos rótulos temporais fica pendente; intervalos removidos nunca entram no exemplo. Render de seleção conserva o contrato E2 sem gain, normalização ou fade, com arquivo temporário e source handle liberados inclusive em falha.
- Deduplicação por fonte canônica/intervalos/versão do extrator. Embeddings existentes são reutilizados. Exemplo pendente por falha de extração pode ser confirmado novamente após recuperação. Desfazer cadastro duplicado não apaga uma referência anterior; correção A→B retira a influência derivada de A, e desfazer restaura A. Limpar a pessoa também retira a influência derivada.
- Agregação reconstruível: vetores normalizados, média ponderada por atividade útil limitada a cinco segundos e fração não silenciosa, seguida de normalização. Até 64 exemplos por pessoa e 32 elegíveis ativos, escolhidos deterministicamente por peso. Não há vantagem por fazer mais comparações contra uma pessoa com banco maior: matching compara um protótipo por pessoa/modelo. Não há retreino neural.
- `identified`, `ambiguous`, `unknown`: limiar inicial 0,55 e margem 0,08 configuráveis, registrados no resultado. Nenhum cosseno é apresentado como certeza percentual. Clusters desconhecidos recebem `local:<captura>:<cluster>` separado de `speaker_id`, que fica vazio quando não identificado; não há fallback global arbitrário para interlocutor 1.
- Versão do extrator atual inclui SHA-256 do arquivo de modelo. Modelos/dimensões incompatíveis não se somam nem se comparam. Trabalhos na fila capturam o analyzer/extrator original, mesmo se a configuração for trocada antes de executar.
- Atualizações de perfil/política invalidam matching e suas propostas/gerações; não alteram gerações/transcrição ASR. A revisão do acervo reutiliza embeddings de análises, gera propostas e exige aplicação explícita, com revisão/field versions/profile versions/política revalidados. Atribuições humanas são preservadas. Itens sem embedding são listados como necessitando análise de pessoas.
- UI integrada: preferência e resultado do aprendizado no editor; cancelar/desfazer; estados/candidatas por segmento; perfil de voz com ouvir apenas no monitor, parar somente a prévia própria, remover e reatribuir exemplos; critérios configuráveis e propostas em lote. Fechar o perfil também cancela uma prévia que só responde depois do fechamento.

## Migração das referências legadas

NPZ antigo não registra versão do modelo nem amostras individuais. A migração conserva cada vetor em `legacy`, com procedência, hash e backup verificável, sem inventar qualidade/intervalos/ações humanas.

**Decisão conservadora:** versão `legacy:unknown` não é comparada ao extrator atual. A UI informa que as referências antigas estão preservadas, mas precisam de novos exemplos confirmados para participar do reconhecimento. Isso pode reduzir temporariamente a cobertura de identificação de quem só possui referências antigas; não é apresentado como melhoria de qualidade. A compatibilidade histórica não foi inferida apenas pelo nome do arquivo TitaNet.

Migração automática só acessa referência dentro do `data_dir` desta biblioteca. Referência configurada fora dele exige o botão **Importar referências legadas configuradas (com backup)**. O original NPZ nunca é reescrito ou removido, inclusive na exclusão de pessoa; o registro legado recebe desativação local.

## Validação e RED → GREEN

| Ciclo | RED observado | GREEN / cobertura |
|---|---|---|
| Banco e decisão | Ausência dos módulos por asserção explícita; schema 2 diferente de 3 | Amostras, duplicatas, qualidade, reatribuição/deleção, rollback, backup NPZ e migração |
| Correções | API de aprendizado ausente | Salvar normal opt-in, extração falha, multi voz, undo duplicado, invalidar matching sem ASR |
| Identidade local | Reconhecedor retornava `interlocutor_1` para desconhecido e par confundido | Global vazio, decisão unknown/ambiguous e cluster único por captura |
| UI | Módulo de perfil inexistente | Estados sem percentuais, integração do save e UI, separação atribuição/aprendizado |
| Cache e invalidação | Duas extrações da mesma fala; influência A após corrigir B; política sem invalidar matching | Cache, retirada da influência anterior e versão da política |
| Recuperação e preview | Rascunho mantinha pessoa após undo; candidato falho não se recuperava; prévia tardia sobrevivia ao fechamento | Rascunho sincronizado, promoção reversível do candidato, stop só do play_id próprio |
| Troca de extrator | Job capturava versão antiga mas usava analyzer novo; banco não religava | Analyzer congelado por job e rebinding na configuração |
| Undo composto | A→B→undo perdia referência A | Restauração da referência retirada, inclusive ao limpar pessoa |
| Exclusão concorrente | Job vocal recriava pessoa excluída enquanto extraía | Ação invalidada pela exclusão; aplicação tardia e undo não ressuscitam pessoa/amostra |

Gate final em dados/modelos isolados:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:SOUNDBOARD_MODELS_DIR='D:\repos\soundboard-ai\.worktrees\e5-isolated-models'
$env:SOUNDBOARD_SEPARADOS_ROOT='D:\repos\soundboard-ai\.worktrees\e5-isolated-separados'
$env:NODE_BINARY='C:\Users\Hylex\AppData\Local\Author Software\nvm\installs\v24.21.0\node.exe'
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.worktrees/e5-astra-full
& $env:NODE_BINARY --test (Get-ChildItem tests -Filter '*.test.cjs').FullName
git diff --check
```

- Python completo após os quatro ajustes de Astra: **466 passed**, 66,89 s. Após o delta posterior de recuperação no startup, **108 testes focados passaram**, com dois testes novos; o full final desse último delta será executado pelo coordenador após as revisões. Gates intermediários: 459 completos após P1/Sol, 89 focados após P2/Sol e 59 focados durante os ajustes de Astra; todos verdes.
- JavaScript final: **41 passed**. Baseline 36; cinco testes novos E5.
- `git diff --check`: somente aviso preexistente `tests/test_e_mixer.py:431: new blank line at EOF`; não corrigido nesta etapa. Portanto o gate de whitespace não está integralmente limpo, embora não haja novo aviso.
- Logs finais: `.worktrees/e5-astra-full.log`, `.worktrees/e5-astra-js.log`. Logs anteriores preservados: `.worktrees/e5-sol-p1-full.log`, `.worktrees/e5-sol-p1-js.log`, `.worktrees/e5-sol-p1-focused-final.log`, `.worktrees/e5-sol-p2-focused.log`.
- Testes anteriores de cadastro foram adaptados ao contrato assíncrono, mantendo a verificação exata do PCM cru selecionado e da limpeza em sucesso/falha. O teste de bloqueio de replay agora isola a capacidade de IA para não depender de modelos da máquina.

Smoke nativo executado pelo agente coordenador, com UI/APIs reais, fonte sintética e extrator falso: 1280 e janela estreita 768 aprovados. Cobriu salvar pessoa/learnON com seleção de 96.000 amostras, duplicata+undo, monitor de dois segundos, reatribuir/remover/recriar/undo, falha do extrator mantendo pessoa e candidato pendente, fechamento durante prévia e reinício do Store. Captura final informada pelo coordenador: `.worktrees/e5-webview-data-mfptnv74/editor.png`; harness `.worktrees/e5-webview-smoke.py`. Não é avaliação do extrator real nem prova auditiva em hardware.

## Incidente de isolamento no primeiro gate e mitigação

O primeiro gate completo revelou que `config._default_models_dir` preexistente pode escolher `~/Videos/.modelos_voz` mesmo com `data_dir` temporário. A nova migração automática inicial acessou o NPZ desse fallback e criou backup externo antes de a restrição de migração ser aplicada. Isso também contaminou nomes de pessoas em três testes de packs; não era defeito do round-trip.

Metadados verificados, sem listar nomes/vetores pessoais: `C:\Users\Hylex\Videos\.modelos_voz\referencias_vozes.npz.bak`, 8.494 bytes, criação e última escrita **2026-10-30 03:35:36 UTC**, coerentes com o primeiro gate. O original NPZ não foi reescrito. Nenhum arquivo externo foi apagado. Não houve reprodução/captura de áudio pessoal, inicialização deliberada de modelos reais ou hotkeys; houve acesso não pretendido às referências legadas e criação do backup.

Mitigações implementadas e verificadas: migração automática restrita ao `data_dir`; importação externa explícita; teste com NPZ externo inválido prova que não é lido nem recebe backup implicitamente; gates seguintes usam `SOUNDBOARD_MODELS_DIR` isolado. O incidente permanece registrado, não é apagado pelo gate verde.

## Correção do P1 identificado por Sol

Sol reproduziu uma violação de elegibilidade: uma captura legada com `speaker_ids=['a','b']` e sem segmentos temporais perdia sua evidência de múltiplas vozes após atribuir manualmente B. A primeira tentativa sem intervalo era rejeitada; a segunda passava a considerar a fonte inteira como voz única. O caminho Salvar → Cadastrar também podia apagar a mesma evidência, inclusive quando B já era a pessoa primária.

O delta preserva a observação de múltiplas vozes em `source.voice_evidence`, separada da atribuição manual. Um marcador equivalente no clipe transporta essa evidência enquanto a fonte ainda não foi canonizada. A observação é conservada antes de salvar/cadastrar e sobrevive ao reinício e a outro clipe da mesma origem. São metadados adicionais nas entidades existentes, sem nova coleção ou mudança de schema.

Uma fonte conhecida como multivoz continua exigindo intervalo. Fornecer a seleção inicial `[0,total_samples]` não equivale a confirmar uma única voz. Sem âncoras suficientes para verificar a seleção, o exemplo permanece pendente com `uncertain_mix`; com mais de uma voz nas âncoras selecionadas, permanece pendente com `mixed`. Somente uma voz com âncoras cobrindo integralmente os trechos selecionados permite `mixed=False`. A UI já traduz esses motivos e informa que o exemplo não foi usado; não foi adicionada uma nova etapa de interface.

O cache reutiliza o embedding, mas reavalia mistura com a evidência atual. A deduplicação não impede rebaixar uma amostra anteriormente elegível para pendente nem retirá-la do protótipo. Desfazer o recadastro não restaura uma elegibilidade desmentida pela fonte; a propriedade da ação original permanece rastreável e seu undo ainda remove a amostra.

RED: oito falhas para repetição/save→enroll/seleção inicial/reinício/origem compartilhada, mais duas falhas de cache/dedupe sob nova evidência. Controle positivo: intervalo de uma voz ancorada permaneceu elegível. GREEN: 11 novas regressões, **88 testes focados**, **459 Python + 40 JavaScript** completos. Arquivos do delta: `main.py`, `voice_service.py`, `voice_profiles.py`, `tests/test_voice_corrections.py` e este relatório. O smoke anterior permanece evidência da interface; o delta não alterou UI e não é apresentado como nova medição acústica.

## Correção do P2 identificado na revalidação de Sol

A guarda de 64 amostras por pessoa também bloqueava a reavaliação de uma entrada existente. Com o banco cheio, uma amostra antes elegível podia permanecer no perfil mesmo após surgir evidência de mistura, porque a tentativa de rebaixamento era rejeitada pelo limite.

O limite agora se aplica somente quando `existing is None`. Atualização/rebaixamento conserva o número de registros e segue a avaliação de qualidade, enquanto uma 65ª amostra continua rejeitada. RED reproduziu exatamente a exceção de limite no rebaixamento; GREEN verificou banco de 64, rejeição de entrada nova, preservação do ID e rebaixamento da entrada existente para `pending/uncertain_mix`, sem uso no perfil e sem aumentar o banco.

Delta: uma guarda em `voice_profiles.py`, um teste em `tests/test_voice_profiles.py` e este relatório. Gate focado: **89 passed**, 12,02 s, com modelos/separados isolados. Logs `.worktrees/e5-sol-p2-red.log` e `.worktrees/e5-sol-p2-focused.log`. Conforme escopo do coordenador, naquele patch não foi repetida a suíte completa; o gate completo posterior aos ajustes de Astra está registrado acima. Nenhuma mudança de UI nesse delta.

## Correções dos quatro achados de Astra

1. **Ação de correção copiada para um clipe filho.** RED: Criar trecho herdava `voice_action_id` do pai, e a UI habilitava seu desfazer no filho. A criação agora exclui esse vínculo; a UI confere `action.clip_id` antes de habilitar desfazer/cancelar. As APIs `undo_voice_correction(action_id, clip_id?)` e `cancel_voice_learning(action_id, clip_id?)` recusam alvo divergente antes de cancelar jobs ou alterar dados; a UI sempre passa o clipe atual. O argumento é opcional para consumidores anteriores. Uma correção nova num clipe com vínculo histórico de outro dono também não altera a ação do pai. Testes verificam pai/amostra intactos ao rejeitar o alvo errado, filho preservado ao desfazer no pai e botões desabilitados no filho.
2. **Job pendente recriando amostra removida.** RED: cadastro duplicado cacheado, enfileirado com worker parado, recriava o exemplo após a remoção. A exclusão agora grava uma época por chave de amostra nos metadados da fonte; cada ação captura a época vigente. A aplicação rejeita a chave removida depois do início da ação, inclusive em clipes que compartilham a fonte. Não chama métodos da fila sob o lock do Store, preservando a ordem de locks e a liberação de source handles. A remoção não bloqueia uma ação posterior nem um intervalo diferente. O contador `sample_epoch` e `source.voice_sample_removals` são metadados aditivos nas estruturas existentes; leituras anteriores usam defaults explícitos, sem nova coleção de nível superior.
3. **Revisão incompleta descartando segmentos.** RED: um clipe com dois segmentos, só um contendo embedding, gerava uma proposta filtrada e não informava análise pendente. A revisão agora recusa proposta incompleta e inclui o clipe em `requires_speaker_analysis`. Nenhum segmento, texto ou tempo é alterado. A UI existente informa a necessidade de analisar pessoas antes da revisão.
4. **Reatribuição ultrapassando o limite do destino.** RED: transferir B→A quando A tinha 64 exemplos criava o 65º. A guarda é aplicada antes de qualquer mutação, dentro da transação. A rejeição conserva integralmente amostra, ações e perfis. Reatribuir para o próprio dono continua sem mudança de estado, incluindo propriedade da ação original e versões dos perfis.

REDs registrados em `.worktrees/e5-astra-red.log` e `.worktrees/e5-astra-red-valid.log`; o segundo corrige somente o fixture de renderização dos testes de criação para que a falha seja a herança indevida, não a ausência de um mock de áudio. O teste JS também falhou por habilitar indevidamente o botão. GREEN focado: **59 Python + 5 JavaScript**; um controle positivo adicional de intervalo independente foi incluído no gate completo final, **466 Python + 41 JavaScript**.

Arquivos deste delta: `editor_service.py`, `voice_profiles.py`, `voice_service.py`, `ui/voice-profiles.js`, `tests/test_voice_corrections.py`, `tests/test_voice_profiles.py`, `tests/test_voice_profiles.test.cjs` e este relatório. Não foram alterados o guia de uso nem o log central de execução. A mudança de UI é somente a guarda de propriedade da ação; o coordenador revalidará o fluxo de criação de filho no smoke nativo.

## Recuperação de aprendizado interrompido no startup

O coordenador reproduziu uma ação persistida como `pending` após reiniciar a API, mas sem job correspondente na nova fila. A interface continuaria exibindo avaliação e consultando repetidamente uma tarefa inexistente. RED reabriu uma API real a partir de um snapshot durável anterior ao encerramento limpo e confirmou que a ação permanecia `pending`.

A inicialização da API agora chama uma recuperação única, antes de aceitar novos trabalhos: ações `pending` da sessão anterior passam para `interrupted`, com mensagem em português orientando novo cadastro ou desfazer. Não retoma inferência, não cria jobs e não muda a pessoa salva. `learned` e `pending_review` são preservados integralmente. A recuperação não é chamada por `get_voice_profiles`, portanto consultar um aprendizado ativo da nova sessão não o interrompe.

Testes de reinício usam o construtor real de `SoundboardAPI`, o mesmo diretório de dados, dispositivos/hotkeys falsos, extrator sintético e modelos/separados isolados. Cobrem ausência de retomada, atribuição preservada, novo cadastro explícito, undo do cadastro novo e da correção interrompida, estados prontos idênticos e leituras sucessivas sem cancelar o trabalho atual. GREEN focado: **108 passed**, 33,42 s. Logs `.worktrees/e5-restart-red.log` e `.worktrees/e5-restart-focused.log`.

Delta restrito a `voice_profiles.py`, chamada única no startup em `main.py`, dois testes em `tests/test_voice_corrections.py` e este relatório. Não houve mudança de UI nem repetição do full neste patch, conforme orientação do coordenador; o full anterior e o foco posterior são evidências separadas.

## Ajuste de portabilidade do teste no PowerShell nativo

Após aprovação de Sol e Astra, o gate completo do coordenador no PowerShell nativo, sem `PYTHONUTF8`, retornou **1 failed / 467 passed**, 64,32 s. A falha foi exclusiva do teste `test_nested_transaction_failure_never_commits_partial_bank`: duas chamadas a `Path.read_text()` tentavam ler o JSON UTF-8 com o encoding padrão cp1252. Não foi identificada falha do produto nesse gate.

As duas leituras desse teste agora especificam `encoding='utf-8'`. Nenhum arquivo do produto foi alterado nem foi imposto UTF-8 global ao processo para ocultar a dependência. Verificação focada no PowerShell nativo, removendo `PYTHONUTF8` somente do ambiente do subprocesso: **1 passed**, 1,27 s, com `--basetemp=.worktrees/e5-native-utf8-test`. O coordenador repetirá o gate completo; este resultado focado não o substitui. JavaScript (41 testes), sintaxe dos módulos UI e o único aviso antigo de whitespace já haviam sido confirmados pelo coordenador.

## APIs e arquivos

APIs: `get_voice_profiles(speaker_id?)`, `import_legacy_voice_profiles()`, `enroll_speaker_from_clip(clip_id, speaker_id, name, interval?)`, `remove_voice_sample`, `reassign_voice_sample`, `undo_voice_correction(action_id, clip_id?)`, `cancel_voice_learning(action_id, clip_id?)`, `preview_voice_sample`, `request_voice_review`, `apply_voice_review`. O Salvar recebe `voice_interval` em coordenadas da fonte. Operações manuais retornam `assignment_saved`, `action_id` e `learning.analysis_id`; estado final fica em `get_voice_profiles().actions[action_id]` e na fila existente.

Novos: `voice_profiles.py`, `speaker_matching.py`, `voice_service.py`, `ui/voice-profiles.js`, quatro suítes E5 e este relatório. `voice_service.py` é uma separação adicional em relação à lista sugerida, para manter a orquestração fora de `main.py`.

Alterados nesta etapa: `store.py`, `ai_analyzer.py`, `main.py`, `ui/app.js`, `ui/index.html`, `ui/styles.css`, `tests/test_a_api.py`, `tests/test_f2_replay.py`, `tests/test_edit_persistence.py`, `tests/test_voice_enrollment.py`. Os dois últimos já pertenciam às entregas anteriores, apesar de aparecerem como untracked em Git. Nenhuma mudança no relatório central de execução pelo implementer.

## Limitações e insights para E6

Não há corpus fornecido. Os testes usam áudio/embeddings sintéticos e não demonstram redução real de erros, precisão, cobertura, latência neural ou esforço de correção. A comparação de múltiplos protótipos/extratores e calibração independente permanecem em E6/E11.

Atividade de fala é estimada por energia em frames de 20 ms; isso não prova fala humana. Mistura usa seleção confirmada e metadados temporais quando disponíveis, sem separação acústica garantida. Critérios de qualidade, pesos, limite do banco, limiar e margem são provisórios e registrados. Variação distante do centro atual não é, por si só, motivo de rejeição.

O banco de referências participantes é limitado; histórico de ações preserva rastreabilidade/undo. Uma revisão em lote sem embedding anterior informa a necessidade de análise, não dispara ASR nem transforma rótulos antigos em exemplos. Revisões com rascunhos abertos são rejeitadas como obsoletas na aplicação para não sobrescrever trabalho do editor.

O insight principal é que desfazer exige propriedade da ação sobre a referência: deduplicação, reatribuição, falha recuperável e correções sucessivas não são equivalentes a apagar o último vetor. Outro contrato essencial é congelar analyzer e versão juntos ao enfileirar. Por fim, isolamento de `data_dir` sozinho não isola modelos/referências quando a configuração contém fallback histórico; toda validação futura deve fixar ambos explicitamente.

## Encerramento pelo coordenador

SPEC_PASS Sol e QUALITY_PASS Astra após as correções. Gate final nativo: **468 Python em 64,37 s + 41 JavaScript**, sintaxe de todos os módulos UI aprovada. Somente whitespace preexistente em `tests/test_e_mixer.py:431` no diff check. Smoke nativo final em 768×900 inclui clipe derivado sem ação vocal herdada, revisão preservando pessoa manual e persistência após reabrir Store; captura `.worktrees/e5-webview-data-yndmrblx/editor.png`. E5 concluída, sem alegação de qualidade vocal real medida.
