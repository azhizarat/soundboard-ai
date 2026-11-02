# Recuperação da edição precisa e simplificação do editor — 2026-11-02

## Contratos corrigidos

- Waveform em largura integral; alças I/O distintas da agulha. Clique/arraste navega sem destruir a seleção, Shift+arraste seleciona e Alt+arraste move o intervalo sem mudar sua duração. Roda/+/− controlam zoom; I/O marcam limites e setas movem 10 ms (Shift: 100 ms).
- Espaço toca/para seleção; Shift+Espaço parte da agulha. Seek usa somente o ID da prévia própria, convertendo origem → montagem → variante → ranges da sessão. Cortes, lacunas, velocidade e reverso continuam preservados.
- Campos numéricos de corte, seletor de borda do teclado, botões de marcar borda e sugestão de bordas removidos da tela. Junções são acionadas pelos marcadores laranja da waveform, sem seletor vazio. Comandos de corte ficam junto ao áudio.
- Transcrição alinhada seleciona áudio por palavras; lápis/duplo clique abre correção. Texto sem alinhamento permanece editável, sem caixa de palavras vazia. O texto de resultado duplicado foi removido.
- Abas substituem a pilha de expansíveis. Modelo/ASR e recursos são configurações globais, inicializadas sem abrir clipe. IA do clipe mostra tarefas diretas e mantém proteção das correções humanas.
- Análise aplicada atualiza transcrição e campos intocados sem reabrir a janela. Aceite persistido prevalece sobre o cache da fila; proposta removida do histórico não reaparece como aceitável.
- Sequências: roteiro compacto selecionável, um inspetor, inserção no topo, ramos visíveis. Atalhos contextuais preservam edição nativa e limites de revisão dos clipes originais.

## Validação

- 737 testes Python passaram com harness bloqueando hardware real de áudio; inclui regressão de aceite e reapertura.
- 108 testes JavaScript passaram; a suíte cobre gesto relativo, preservação da seleção, atalhos, precisão de mapeamento, correção pendente, busca por ID próprio e respostas tardias de stop/seek.
- WebView2 nativo em 1280×820, 768×900 e 1920×1080, biblioteca e áudio sintéticos: clique/drag da agulha, alças, Shift/Alt drag, reprodução por Espaço, seek com ID, I/O/setas, remover/undo, ouvir junção, duplo clique/Ctrl+Enter para correção, abas, áudio legado, Ctrl+D/Z/S nas sequências e salvar configuração global sem clipe aberto. Sem erros JS capturados ou acesso ao acervo pessoal/modelos.
- Reprodução exercitou sessões reais do motor com mixer isolado; o harness avança blocos PCM sem dispositivo físico. Não constitui avaliação perceptual de áudio nem medição de qualidade do ASR real.
- Capturas: [editor](2026-11-02-editor/editor.png), [texto legado](2026-11-02-editor/legacy.png), [sequências](2026-11-02-editor/sequence.png).

## Regras confirmadas para próximas mudanças

Manter waveform, transporte e corte na mesma área; gestos de navegação não devem recriar seleção. Não reconstruir botões de palavra a cada movimento: além do custo, isso quebra duplo clique e foco. Identidade da prévia deve ser invalidada antes de aguardar stop; respostas de poll anteriores à conclusão do seek não podem devolver a agulha ao tempo antigo. Configurações do motor pertencem ao aplicativo, enquanto a edição oferece tarefas sobre o clipe. Resultado aplicado precisa atualizar a projeção visível sem substituir digitação em andamento.
