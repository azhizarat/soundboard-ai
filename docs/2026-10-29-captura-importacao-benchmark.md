# Captura e importação de clipes — diagnóstico e proposta

Plano de execução consolidado: [etapas, prioridades, dependências e validação](superpowers/plans/2026-10-29-evolucao-consolidada.md).

Data: 29/10/2026. Status: proposta para discussão; não é especificação aprovada nem implementação.

**Atualização após esclarecimentos do usuário:** a captura em segundo plano já atende ao uso. A prioridade passou a ser qualidade da transcrição e edição: cortar as pontas, remover intervalos internos, unir as partes restantes e selecionar áudio pelo texto. A recomendação de caixa de entrada abaixo fica como hipótese anterior, fora da primeira entrega. A proposta atual está em [Editor por transcrição e múltiplos trechos](superpowers/specs/2026-10-29-editor-transcricao-design.md).

## Direção recomendada

O uso principal informado é extrair trechos de chamadas ou gameplay capturados no PC. A prioridade deve ser transformar uma captura em clipes utilizáveis com pouco trabalho: **capturar → revisar → selecionar trechos → ouvir no fone → salvar na biblioteca**.

Recomendo uma caixa de entrada de capturas, com processamento em segundo plano e um editor focado em trechos. Importação por URL, catálogos de sons e funcionalidades sociais ficam para depois. Há recursos suficientes no app para evoluir incrementalmente, reaproveitando o motor de áudio, waveform, preview e persistência.

## Escopo e evidência

- Inspeção estática dos arquivos atuais, incluindo alterações locais preexistentes. Nenhum código de aplicação foi alterado e nenhum teste de execução foi realizado nesta avaliação.
- Benchmark pesquisado na web pelo agente GPT-6.1 Terra, a pedido do usuário, com fontes oficiais. Não foram executados os produtos comparados.
- As especificações de 28/10 foram consultadas como decisões anteriores, não como prova de implementação.
- Mantêm-se os princípios anteriores: degradação graciosa, erros explicados pela UI, testes sem dependência de hardware e operações reversíveis. Não há justificativa demonstrada para trocar a stack ou migrar a persistência agora.

## O que já existe e onde está o atrito

| Evidência atual | Implicação para o produto |
|---|---|
| O botão chama `capture_replay_draft` e abre o editor (`ui/app.js:1532`); o caminho do atalho salva direto (`main.py:1149`). | Capturar tem resultados diferentes conforme o ponto de entrada. Uma caixa de entrada pode uniformizar o destino sem abrir um modal durante o jogo. |
| `_capture_replay_analysis` salva o áudio e executa análise antes de devolver o resultado (`main.py:1108`). | A revisão depende da conclusão da análise. Separar persistência da captura, edição e enriquecimento evita que a IA fique no caminho crítico. Não foi medida a latência real. |
| `save_replay_wav` remove silêncio nas bordas antes de gravar (`audio_engine.py:949`). | O arquivo salvo já perdeu esse contexto. Preservar o original e representar o corte como metadados permite desfazer a sugestão. |
| Waveform, arraste de limites, scrubber e preview no fone já existem (`ui/app.js:1973`, `ui/app.js:2141`). | O investimento deve melhorar precisão e velocidade de edição; não reimplementar essas capacidades. |
| O motor já aplica microfades de 4 ms contra cliques (`audio_engine.py:58`). | Fades ajustáveis seriam uma função de edição adicional. Não devem ser vendidos como correção de ausência total de fades. |
| Há separação por interlocutores no fluxo de replay (`main.py:1159`, `main.py:1239`). | A evolução é permitir escolher vários momentos, inclusive manualmente, sem depender de acertar a identidade de quem falou. |
| Importação de arquivos copia, analisa e salva item a item, sem etapa de revisão (`main.py:941`). | O mesmo fluxo de entrada/revisão pode atender arquivos locais numa etapa seguinte. |
| O lote tem `finally`, mas não tratamento de falha individual ao redor de cópia/análise (`main.py:969`). | Uma exceção pode interromper os demais itens e impedir a atualização final da UI, mesmo com itens anteriores salvos. É risco observado no código, não falha reproduzida. |
| Handlers de importação comum tratam sucesso, mas não mostram `error`/`warnings` (`ui/app.js:1548`). | O usuário não recebe explicação adequada para falha ou resultado parcial. |
| Importar pasta usa categoria/interlocutor atualmente filtrado para decidir o modo (`ui/app.js:1570`). | Navegação altera implicitamente a operação. O modo de importação deve estar explícito. |
| Arquivos comuns recebem um novo nome de destino; o store compara ID/caminho (`main.py:978`, `store.py:306`). | Não há deduplicação por conteúdo nesse caminho. O mecanismo existente de packs pode servir de referência. |

## Benchmark: padrões úteis

| Produto | Capacidade documentada | Aplicação ao nosso caso |
|---|---|---|
| [Soundpad — tutorial](https://www.leppsoft.com/soundpad/en/help/manual/tutorial/) e [gravador](https://www.leppsoft.com/soundpad/en/help/manual/tutorial/recorder/) | Entrada múltipla/arraste, editor de corte e volume; gravador com corte de silêncio e normalização opcionais. | Um ciclo curto entre capturar, recortar e usar. Tornar explícito o que é ajuste opcional. |
| [Soundpad — configurações](https://www.leppsoft.com/soundpad/en/help/manual/config/) | Normalização dinâmica relacionada ao volume da voz e opções de reprodução por destino. | Consistência percebida em relação à conversa, com destino claro antes do play. |
| [Voicemod — gravador](https://support.voicemod.net/hc/en-us/articles/28280255185170-Voicemod-Recorder-How-to-Create-Recordings-for-Your-Soundboards) e [soundboard](https://www.voicemod.net/en/soundboard/) | Gravação, ajuste de duração antes de salvar, sons próprios e da comunidade. | Revisar antes de incorporar à biblioteca é parte do fluxo, não um trabalho posterior escondido. |
| [Resanance](https://resanance.com/) | Busca, cores, ordenação, atalhos/MIDI e volumes local/microfone. | Rapidez para localizar e disparar; separar claramente ouvir e transmitir. |
| [Farrago — manual](https://www.rogueamoeba.com/support/manuals/farrago/?print=true) | Editor com waveform, seleção, corte e fades; preview em dispositivo secundário, sets e favoritos. Produto para macOS. | Referência para a bancada de edição e audição privada, não indicação de substituto para Windows. |

Ausência de uma capacidade nas páginas consultadas não prova ausência no produto. Importação por URL arbitrária não foi confirmada. Preços e limites de planos não foram usados como critérios.

## Três caminhos possíveis

| Caminho | Benefício | Limite |
|---|---|---|
| Ajustes pontuais no fluxo atual | Menor intervenção: erros visíveis, ações mais claras, melhores atalhos e corte. | Mantém análise antes da revisão e a fragmentação entre botão/atalho/importação. |
| **Caixa de entrada + editor de trechos — recomendado** | Resolve a jornada principal e aproveita os recursos existentes. Permite capturar durante a partida e organizar depois. | Exige estados de processamento, rascunhos persistentes e política de limpeza. |
| Estúdio completo de captura | Trilhas separadas, processamento avançado e edição extensa. | Complexidade de dispositivos, sincronização e interface muito maior. Só se justifica se a mistura entre call/jogo/microfone for o problema dominante. |

## Primeira entrega proposta

1. **Capturar sem interromper.** Atalho salva uma captura com ID único, confirma discretamente e mantém foco no jogo. O botão pode abrir a revisão. Ambos usam a mesma caixa de entrada. Manter uma preferência explícita de salvamento direto se o usuário desejar preservar o comportamento atual.
2. **Analisar depois de capturar.** Estados visíveis: capturado, analisando, pronto, análise falhou. Falha de IA não inutiliza o áudio; permitir revisar e salvar com título manual. Resultados tardios não sobrescrevem alterações manuais.
3. **Revisar com precisão.** Waveform com zoom, teclado para marcar início/fim, repetir seleção e salvar vários clipes da mesma captura. Começar pelo corte manual; sugestões de fala/silêncio vêm depois, sempre editáveis.
4. **Prévia e qualidade compreensíveis.** Destacar “Ouvir no fone”. Medir loudness sobre o trecho selecionado, mostrar ajustes automáticos e permitir comparar com/sem tratamento. Fades ajustáveis para entrada/saída, sem remover os microfades existentes.
5. **Salvar com pouco atrito.** Título, categoria e interlocutor opcionais; sugestões editáveis. Atalhos não são requisito para concluir. A captura original continua disponível até descarte explícito ou política de retenção visível.

Os rascunhos atuais de replay são temporários e a especificação anterior prevê purga no boot. Uma caixa de entrada durável exige outro ciclo de vida: não basta renomear os rascunhos existentes na interface.

## Qualidade de áudio: ordem de trabalho

- Validar primeiro **o que está sendo capturado**: dispositivo, presença da própria voz, áudio do jogo, chamada e eventual retorno do soundboard. A topologia real não foi testada.
- Preservar o original e aplicar cortes/ganhos de forma reversível.
- Validar o ganho efetivamente aplicado na reprodução, considerando volume manual, normalização e mixer juntos. Adicionar outro controle não garante maior consistência.
- Diferenciar loudness integrado, estimativa em clipes muito curtos, pico de amostra e true peak. O cálculo inspecionado em `loudness.py:143` usa pico de amostras; o nome da constante não demonstra medição de true peak.
- Redução de ruído e separação de fontes só entram após ouvir exemplos representativos. Podem degradar risadas, gritos e efeitos; não recomendo processamento agressivo automático por padrão.

## Evoluções seguintes

- Importar gravações locais de áudio/vídeo para a mesma bancada, incluindo arquivos longos, sem exigir análise integral antes de selecionar um trecho.
- Fila de arquivos com cancelamento, repetição apenas dos itens que falharam, avisos por item e deduplicação por conteúdo.
- Edição de metadados em lote, histórico de importação e origem da captura.
- Sugestões de trechos a partir de fala/transcrição, sem assumir que segmentação por interlocutor identifica o melhor momento.
- Coleções por jogo/grupo e busca por transcrição, se o volume de biblioteca demonstrar essa necessidade.
- Captura por aplicação ou trilhas separadas somente depois de confirmar limitações do áudio misturado; isso é mudança de captura, não simples filtro de UI.

## Critérios de aceite para detalhar na implementação

- Duas capturas rápidas geram itens distintos; nova captura funciona durante análise anterior.
- A IA indisponível não impede captura, edição, preview ou salvamento.
- O atalho não rouba foco. Prévia envia zero áudio ao barramento de transmissão, comprovado com teste de roteamento e smoke test real.
- Reiniciar o app preserva capturas pendentes e os cortes já feitos.
- Extrair dois trechos mantém ambos válidos e preserva o original.
- Resultado tardio de análise não substitui título/categoria já editados.
- Erro em um item não interrompe o restante do lote; o resumo apresenta sucessos, falhas e duplicatas.
- Cortes e normalização são verificados com sinais sintéticos; qualidade percebida e seleção de dispositivos são verificadas com gravações representativas e hardware real.
- Medir tempo até confirmação de captura e até primeira prévia antes/depois; definir metas a partir da linha de base, sem prometer latência não medida.

## Insights registrados

- Para este uso, captura e curadoria têm prioridade sobre aquisição de sons externos.
- O principal diferencial possível é rapidez para transformar momentos de conversas em clipes, com análise local opcional.
- “Mais IA” não equivale a melhor UX: análise deve enriquecer o áudio já disponível para uso.
- O sucesso deve ser medido pela facilidade de capturar, encontrar e salvar o trecho, não pela quantidade de recursos expostos na tela.
- Próxima decisão: confirmar o escopo da primeira entrega acima antes de detalhar e implementar. Nenhuma aprovação de implementação foi inferida desta avaliação.
