# Editor por transcrição e múltiplos trechos

Plano de execução consolidado: [etapas, prioridades, dependências e validação](../plans/2026-10-29-evolucao-consolidada.md).

Data: 29/10/2026. Status: proposta de design para revisão; implementação não iniciada.

Avaliação complementar: [inteligência local, confiabilidade e otimizações](../../2026-10-29-inteligencia-local-otimizacoes.md). O diagnóstico identifica pré-requisitos para melhorar a transcrição, incluindo reamostragem adequada e reanálise que respeite a edição.

## Problema e requisitos confirmados

A captura contínua em segundo plano já atende ao usuário. O trabalho excessivo acontece depois: corrigir a transcrição, localizar o conteúdo útil e editar o áudio. O usuário confirmou que precisa tanto ajustar início/fim quanto remover partes internas e juntar o restante. Selecionar áudio pela transcrição faz parte do escopo desejado.

O resultado esperado é chegar ao editor com uma transcrição utilizável e limites sugeridos, localizar uma frase pelo texto, ouvir, remover o que não interessa e salvar um clipe pronto. A captura existente continua sendo o ponto de entrada.

## Evidência atual

- `ai_analyzer.py:516`: Whisper `base` fixo, com tentativa de CUDA e fallback para CPU.
- `ai_analyzer.py:589`: transcrição em português, `beam_size=3`, VAD desativado; devolve somente texto concatenado, descartando tempos.
- `ai_analyzer.py:699`: segmentos de interlocutores são transcritos separadamente. A influência dessa divisão na qualidade ainda precisa ser medida.
- `ui/index.html:331`: texto editável em textarea separado do corte.
- `ui/app.js:1973`: já há controles de waveform; `ui/app.js:2141`: já há preview no monitor.
- `store.py:367`: volume e um único intervalo `trim_start`/`trim_end` por clipe.

Esses fatos foram observados no código. Não houve avaliação auditiva nem medição da qualidade de reconhecimento nesta pesquisa.

## Escolha de abordagem

| Alternativa | Consequência |
|---|---|
| Melhorar apenas waveform e modelo de transcrição | Ajuda na precisão, mas mantém a busca manual da frase no áudio e não resolve cortes internos. |
| **Editor sincronizado de texto e áudio, com lista de trechos preservados** | Atende aos requisitos com edição reversível e aproveita o app existente. Recomendado. |
| Editor multitrilha completo | Permite montagem mais ampla, mas adiciona mixagem, sincronização e organização que não foram solicitadas. |

## Experiência proposta

O editor ocupa uma área ampla, com transcrição e waveform sincronizadas, transporte sempre visível e ações de corte próximas à seleção. A disposição deve se adaptar à largura disponível, mantendo texto e controles essenciais acessíveis.

### Seleção e correção de texto

- Clicar em uma palavra posiciona o cursor no áudio; reprodução exige a ação de ouvir.
- Selecionar palavras marca o intervalo entre a primeira e a última palavra na waveform.
- Selecionar áudio destaca as palavras que intersectam o intervalo. Os limites manuais continuam válidos mesmo quando atravessam uma palavra.
- **Corrigir texto** altera somente a transcrição. Backspace/Delete dentro do campo de correção nunca remove áudio.
- **Manter seleção** remove o conteúdo anterior e posterior da montagem atual.
- **Remover trecho** exclui o intervalo selecionado e fecha o espaço entre as partes restantes.
- **Criar outro clipe** salva a seleção como uma nova montagem, preservando o clipe em edição.
- Partes removidas permanecem visíveis de forma atenuada na visão da origem, podendo ser restauradas. A transcrição do resultado inclui apenas o conteúdo mantido.

Exemplo: na captura “pera aí… [pausa] olha isso… não, volta… agora foi”, selecionar “não, volta” e usar Remover trecho produz a montagem sem essa fala. Apagar uma palavra ao corrigir a escrita não executa esse corte.

### Waveform e junções

- Zoom na seleção, ajuste à largura e deslocamento horizontal.
- Marcar início/fim pelo cursor; mover limites em passos de 10 ms ou 100 ms, limitados pela duração disponível.
- Ouvir a seleção em repetição e ouvir cada junção com até 500 ms de contexto de cada lado.
- Exibir marcadores de corte e duração final; permitir corrigir cada borda após ouvir.
- Aplicar pequenas rampas contra cliques nas junções, limitadas pelo comprimento dos trechos. Não sobrepor falas automaticamente.
- Desfazer/refazer cortes, restaurações e correções durante a sessão de edição. Salvar preserva a montagem e a origem; o histórico completo de comandos não precisa sobreviver ao reinício nesta primeira versão.

### Sugestões iniciais

Sugerir limites externos com base em fala e pausas, com margem ajustável e prévia. As sugestões não descartam áudio automaticamente. Risadas, gritos, efeitos e falas sobrepostas continuam selecionáveis pela waveform, inclusive quando não há texto reconhecido.

Não se promete detectar automaticamente “a parte engraçada”. Identificar fala é diferente de entender qual momento vale guardar.

## Transcrição e alinhamento

Preservar palavras e segmentos com tempos na origem, texto reconhecido, texto corrigido e identificação da versão da análise. Sinais de confiança do reconhecedor servem como indícios para revisão, sem exibir uma porcentagem de certeza não calibrada.

Uma substituição simples de palavra mantém sua âncora temporal. Correções que dividem ou unem palavras conservam o intervalo do bloco corrigido: até realinhar, esse bloco é selecionável como unidade e seus limites podem ser ajustados no áudio. Não inventar tempos exatos distribuindo palavras igualmente no intervalo.

Permitir **Retranscrever seleção**, incluindo contexto ao redor para reconhecimento, mas propondo mudanças apenas para o intervalo solicitado. Mostrar o resultado para aplicação explícita, preservando correções manuais. Resultados de uma análise antiga não podem sobrescrever uma edição mais recente.

Clipes antigos com somente texto continuam editáveis pela waveform. Seleção temporal pelo texto é habilitada quando houver alinhamento válido; falhas de transcrição não bloqueiam reprodução, corte ou salvamento.

## Qualidade do reconhecimento

Manter processamento local como premissa. Antes de escolher outro modelo padrão, comparar configurações nos mesmos áudios representativos: fala limpa, gírias/nomes, jogo ao fundo, interrupções e sobreposição de vozes.

A avaliação deve comparar o comportamento atual com opções de maior qualidade e com transcrição usando mais contexto, registrando erros, tempo, consumo de recursos e esforço de correção. Trocar o modelo é uma hipótese de melhoria, não uma conclusão já comprovada.

Disponibilizar configuração de qualidade e vocabulário auxiliar para nomes/gírias, sem tratar esse vocabulário como substituição automática do que foi ouvido. Não enviar áudio a serviços externos. Downloads ou instalação de modelos serão tratados de forma explícita na implementação, conforme tamanho e disponibilidade.

## Modelo de edição e integração

- Preservar o arquivo de origem disponível na abertura do editor. Edição não sobrescreve esse arquivo. Isso não recupera áudio que o pipeline atual já descartou antes de abrir o editor.
- Representar a montagem como uma lista ordenada de intervalos preservados da origem. A primeira versão mantém a ordem cronológica: não inclui reorganizar ou repetir trechos.
- Separar os tempos da origem dos tempos do resultado. Após cortar, a posição do cursor no resultado precisa mapear corretamente para a palavra e o intervalo originais.
- Preview e reprodução final devem consumir a mesma definição de montagem. Uma renderização derivada pode servir ao motor atual, mas não substitui a origem nem se torna uma segunda fonte de verdade.
- Para compatibilidade, um clipe sem montagem explícita equivale ao intervalo legado de trim. Não aplicar o trim antigo uma segunda vez sobre áudio já montado.
- Recalcular duração e loudness do resultado final. Um corte invalida áudio derivado, waveform derivada e medidas que dependem da montagem.
- Manter identificação de origem e referências quando vários clipes compartilham uma captura. Excluir um clipe não pode apagar uma origem ainda utilizada por outro.
- Salvamento persiste origem, montagem, correções e metadados de forma consistente. Na falha, o editor permanece aberto com as alterações disponíveis.
- Packs existentes continuam importáveis. Nesta primeira versão, exportar um clipe editado inclui o áudio final renderizado e a transcrição correspondente, com trim reiniciado para a duração total desse resultado. Não inclui o histórico nem as partes removidas da origem. A UI informa que o pack transporta o resultado, enquanto a edição reversível permanece na biblioteca local. Reimportar permite novos cortes sobre o resultado, sem recuperar partes removidas.

## Entrega em etapas

1. **Fundação de edição:** intervalos preservados, cortes internos, junção, preview fiel, desfazer/refazer e compatibilidade com clipes existentes.
2. **Edição pelo texto:** transcrição temporal, seleção sincronizada, correções ancoradas e retranscrição parcial com aplicação explícita.
3. **Redução do trabalho inicial:** avaliação e configuração de qualidade do reconhecimento, limites sugeridos, controles refinados e criação de vários clipes.

Cada etapa deve produzir comportamento utilizável e validado. A entrega solicitada só está completa quando as três partes funcionarem juntas.

## Validação

- Remover o meio de um sinal conhecido mantém exatamente a ordem dos dois trechos restantes, considerando apenas as rampas documentadas.
- Cortar nas pontas e no meio, desfazer e refazer preserva a origem e reconstrói a montagem esperada.
- Testar mapas temporais antes, dentro e depois de cada corte; cursor, palavra e preview permanecem sincronizados.
- Corrigir texto não altera amostras de áudio. Remover trecho não apaga o arquivo original.
- Uma correção que muda o número de palavras não produz timestamps fictícios.
- Retranscrição tardia não sobrescreve correções nem muda limites de corte silenciosamente.
- Clipes antigos, clipes com montagem e clipes sem transcrição reproduzem e persistem corretamente.
- Salvar, fechar e reabrir mantém montagem e correções; criar dois clipes da mesma origem não causa exclusão cruzada.
- Prévia não envia áudio ao cabo virtual; verificar com backend falso e depois com hardware real.
- Confirmar auditivamente junções, fala nas bordas e consistência de volume. Testes sintéticos não substituem essa validação.
- Comparar o número de correções e ações necessárias para produzir os mesmos clipes antes/depois, além do tempo de processamento. Não declarar melhoria de reconhecimento com base apenas em teste unitário.

## Fora da primeira entrega

Nova caixa de entrada de capturas, mudança da captura em segundo plano, importação por URL, edição multitrilha, reorganização livre de falas, separação avançada de fontes, processamento em nuvem e migração de banco de dados.

## Insights e estado de decisão

Os requisitos de cortar pontas, retirar conteúdo interno, unir o restante e selecionar pelo texto foram confirmados pelo usuário. A mecânica detalhada neste documento é proposta para revisão. O ponto central é separar correção textual de edição sonora e preservar a relação entre texto, origem e montagem.

O próximo passo é validar este design e detalhar a implementação; nenhum código de aplicação foi alterado nesta etapa.
