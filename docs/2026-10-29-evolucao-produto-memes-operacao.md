# Evolução de produto: memes internos e operação ao vivo

Plano de execução consolidado: [etapas, prioridades, dependências e validação](superpowers/plans/2026-10-29-evolucao-consolidada.md).

Data: 29/10/2026. Propostas para priorização; nenhuma função abaixo foi implementada nesta etapa.

## Direção de produto

O uso predominante é brincadeira e memes de um grupo, mas o app também precisa servir a apresentações, lives e outras situações de soundboard profissional. Isso favorece uma base compartilhada com comportamento configurável e uma interface simples durante o disparo.

“Profissional” deve significar previsibilidade, rapidez para encontrar o som, clareza de saída e recuperação de falhas. O aumento da quantidade de controles, isoladamente, não entrega essas propriedades.

## Base existente

O código atual já contempla atalhos globais, monitor local e cabo virtual, volumes separados, sobreposição global, ducking, loop durante a reprodução, captura de replay, edição de trim e pacotes. Essas capacidades devem ser reaproveitadas.

Evidências desta inspeção: `store.py:22` para configurações; `ui/index.html:26` para roteamento; `ui/index.html:248` e `ui/app.js:1725` para loop; `ui/app.js:1433` para sobreposição global.

As propostas seguintes ampliam essa base. Não foram feitos testes com hardware, usuários ou situações ao vivo nesta avaliação.

## 1. Separar Preparar de Tocar

**Preparar:** editor por transcrição, cortes, identificação de pessoas, correções, referências vocais e organização.

**Tocar:** botões estáveis, busca rápida, grupo ativo, volume, indicação do que toca e ação de parar sempre acessível. Tarefas de análise não movem cartões nem mudam atalhos enquanto se opera.

O objetivo é permitir montar a biblioteca com ferramentas completas e usar uma superfície simples durante a partida ou apresentação. Não exige duas aplicações nem duas bibliotecas.

Prioridade: alta, depois da base de edição já proposta.

## 2. Coleções com posição e atalhos estáveis

Criar coleções como “Resenha”, “Bordões de uma pessoa”, “Live”, “Apresentação” ou “Efeitos rápidos”. Um clipe pode aparecer em várias coleções sem duplicar seu áudio.

- Ordem manual e posição fixa dos botões; filtros e sugestões de IA não reorganizam uma coleção em uso.
- Atalhos por coleção ativa, com conflitos explicados e atalhos globais reservados.
- Cor, rótulo curto e imagem opcional por botão.
- Alternar coleção não interrompe áudio automaticamente. Esse comportamento fica explícito na configuração.
- Categoria, interlocutor e coleção são conceitos diferentes: o mesmo som pode pertencer a uma categoria e a diversos conjuntos de uso.

Benefício para memes: acessar os bordões relevantes sem procurar na biblioteca inteira. Benefício profissional: preparar uma superfície de operação com posições memorizáveis.

Prioridade: alta.

## 3. Comportamento por som e por grupo

A sobreposição atual é global. Evoluir para regras compreensíveis:

| Tipo de uso | Comportamento sugerido |
|---|---|
| Falas e bordões | Um novo som substitui o anterior do mesmo grupo |
| Trilhas | Permanecem tocando enquanto outros grupos disparam |
| Vinhetas e efeitos | Podem sobrepor outros grupos, com limite de instâncias |
| Som sustentado | Toca enquanto o botão/tecla estiver pressionado |
| Loop | Primeiro acionamento inicia, segundo encerra com saída configurada |

Definir o que acontece ao apertar novamente o mesmo botão: reiniciar, parar ou ignorar. Proteger contra repetição involuntária de tecla pressionada. Manter stop imediato e acrescentar parada suave do grupo ou de todos, como ações distintas.

Não elevar automaticamente o volume para competir com outros sons. A soma precisa respeitar o processamento de saída, e o resultado deve ser validado no mixer.

Prioridade: alta; dependente de testes de áudio e roteamento.

## 4. Grupos aleatórios com curadoria

Um botão pode escolher entre clipes selecionados, por exemplo “risada”, “reação” ou “bordão do grupo”. Usar uma ordem embaralhada que evite repetição imediata e percorra as opções antes de recomeçar, quando houver mais de uma.

- O usuário escolhe quais clipes entram no grupo.
- A interface mostra qual som foi escolhido e permite repetir exatamente o último.
- Intervalo mínimo entre disparos é opcional e configurável.
- Não tocar automaticamente só porque a transcrição contém uma palavra; a graça e o contexto continuam sob controle de quem opera.

Prioridade: média; bom ganho de diversão com escopo relativamente pequeno após coleções e comportamento por grupo.

## 5. Variações de meme preservando o original

Criar variantes do mesmo clipe: velocidade, tom, reverso e pequenos presets de efeito. Ouvir antes de salvar e manter acesso ao original. Começar com poucas operações previsíveis, sem transformar o editor em uma estação de produção musical.

As variantes podem ocupar botões próprios sem duplicar toda a informação de origem. Duração, waveform e loudness precisam refletir o resultado processado.

Reconhecimento de pessoa e cadastro de referência usam a origem vocal confirmada, não o áudio alterado por efeitos. Caso contrário, a função de diversão poderia piorar o aprendizado de vozes.

Prioridade: média, depois da edição não destrutiva.

## 6. Modo ensaio e destino evidente

Já existe preview individual no monitor. Acrescentar um modo de ensaio que encaminha todos os disparos — inclusive atalhos — somente ao monitor até o usuário voltar ao modo de transmissão.

- Mostrar estado e destino de forma persistente, perto dos controles de reprodução.
- Se monitor e transmissão apontarem ao mesmo dispositivo, informar que essa configuração não oferece audição separada. Não chamar de privado um destino que também está sendo transmitido.
- Ao perder o dispositivo de transmissão, parar/suspender essa saída e informar o ocorrido. Não escolher silenciosamente outro destino.
- Ao reconectar, deixar claro o estado da reprodução; não disparar novamente um som antigo de surpresa.
- Manter a prévia com as mesmas edições e efeitos da reprodução final.

Benefício para memes: testar um som sem soltá-lo no meio da conversa. Benefício profissional: ensaiar uma sequência antes de entrar no ar.

Prioridade: alta junto da área Tocar.

## 7. Sequência de sons para apresentações e lives

**Escopo ampliado pelo usuário:** compor diálogos com trechos sequenciais e simultâneos, preservando originais. A [proposta de fluxos sonoros](superpowers/specs/2026-10-29-fluxos-sonoros-design.md) substitui a lista simples abaixo como direção deste recurso, incluindo referências versionadas, grupos paralelos e espera por acionamento.

Uma lista ordenada com título, duração, notas opcionais e botões “disparar próximo”, “pular” e “parar”. Selecionar o próximo item não o reproduz automaticamente.

Começar com avanço manual, adequado a quem acompanha a fala ou o evento. A sequência usa os mesmos clipes e regras de grupos; não exige um novo motor. Reprodução encadeada automática e transições elaboradas podem ficar para depois de validar o uso.

Benefício profissional: executar abertura, vinheta, efeito e encerramento sem procurar cada item. No uso de memes, pode servir a pequenas sequências preparadas.

Prioridade: média; posterior a coleções e regras de disparo.

## 8. Pacotes que transportam uma coleção pronta

Os packs já existem. Evoluir para transportar também ordem, aparência dos botões e regras de reprodução da coleção, com tratamento explícito de conflitos de atalhos ao importar.

Vínculos de voz vindos de outra biblioteca não viram automaticamente referências confirmadas de pessoas locais. Da mesma forma, endereços/IDs de dispositivos não devem sobrescrever o roteamento do destinatário.

Manter importação compatível com packs antigos, resumo do que foi adicionado e opção de preservar ajustes locais. Exportar a coleção selecionada é diferente de criar um backup completo de origens e edições.

Prioridade: média, após estabilizar o formato das montagens e coleções.

## 9. Controle externo quando o fluxo estiver estável

Teclado numérico, MIDI ou controlador físico podem acionar as mesmas operações da interface. A base importante é um conjunto único de ações e estados: disparar, parar grupo, ajustar volume, próxima coleção e próximo item.

Isso evita que atalhos, botões e futuros controladores tenham regras diferentes. A disponibilidade ou compatibilidade de dispositivos específicos não foi pesquisada nesta avaliação.

Prioridade: posterior; escolher integrações a partir do equipamento e uso efetivos.

## Priorização consolidada

1. **Reduzir trabalho manual:** editor por texto e múltiplos trechos, transcrição melhor e aprendizado pelas correções de vozes.
2. **Facilitar o uso cotidiano:** área Tocar, coleções estáveis, regras por grupo e modo ensaio.
3. **Ampliar a diversão:** grupos aleatórios e variantes de efeitos.
4. **Expandir uso profissional:** sequência de sons, packs de coleções e controle externo.

As novas ideias não substituem os problemas já relatados. Primeiro tornar os clipes fáceis de preparar e confiáveis de identificar; depois tornar a operação mais rápida e versátil.

## Como avaliar

- Tempo e número de ações para encontrar e disparar o som correto.
- Disparos errados, repetições involuntárias e conflitos de atalhos.
- Clareza sobre o que está tocando e em qual destino.
- Impacto de análises em segundo plano sobre reprodução e jogo.
- Operação por teclado e estabilidade das posições dos botões.
- Recuperação após desconexão de dispositivos, sem áudio inesperado.
- Fluxos completos: bordão durante gameplay; teste no fone; trilha com efeitos; sequência manual de apresentação; exportação/importação da coleção.

## Insights registrados

O caráter de meme favorece descoberta rápida, variação e personalização. O caráter profissional favorece previsibilidade e controle do resultado. Coleções, grupos de reprodução e separação entre preparação e operação atendem aos dois sem tornar a tela cotidiana complexa.

Evitar automação de disparos baseada em suposições sobre o contexto da conversa. A IA é mais útil preparando, organizando e sugerindo conteúdo; o acionamento continua intencional.
