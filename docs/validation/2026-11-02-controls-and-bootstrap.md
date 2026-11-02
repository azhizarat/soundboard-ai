# Abertura utilizável e ajustes dos controles

## Alterações

- O botão Clipar Call não repete mais o intervalo do dropdown. Os handlers
  continuam usando o valor selecionado para capturar e salvar a preferência.
- Seletores têm seta própria com margem interna; Preparar e Tocar têm gap
  de 6 px; a engrenagem usa alinhamento central e remove a margem vertical.
- “Ensaio · só monitor” passa a “Testar só no fone”, com explicação acessível
  junto ao controle. Afeta clipes, coleções, sequências e atalhos; não altera
  microfone ou captura. Alternar interrompe os sons em reprodução.
- Recusas de ativação aparecem junto ao controle, com “Configurar fone”. Não
  aumenta o volume silenciosamente. Volume zero, saída igual à transmissão
  e indisponibilidade continuam sendo recusados pelo backend.
- A UI aguarda confirmação antes de mostrar o teste como ativo, bloqueia
  trocas concorrentes e ignora respostas de polling anteriores à transição.
  Foi corrigido também o clique de Configurar fone: sua propagação fechava
  imediatamente o popover que ele acabara de abrir.
- O primeiro grid usa `get_bootstrap_state`. Após dois frames, solicita
  `notify_ui_ready` e carrega os diagnósticos/pastas em segundo plano.
  Resultados tardios não sobrescrevem uma configuração ou diagnóstico mais
  recente. Falhas deixam aviso acessível; resultados posteriores aparecem
  no banner, sem abrir um modal de surpresa durante o uso.

Detalhes de backend e medições controladas: [inicialização](2026-11-02-startup.md).
Os números sintéticos daquele ensaio não medem o tempo real do launcher,
Windows, drivers ou WebView2 do usuário. Logs Startup registram construção
da API, criação da janela, sinal de UI pronta e conclusão dos detalhes na
próxima abertura, sem dados da biblioteca.

## Verificação

- **736 testes Python passaram em 17,59 s**, com hardware bloqueado e modelos,
  dados e temporários isolados. Suíte completa via harness
  `.worktrees/startup-probe/run_pytest.py -q -x -p no:cacheprovider
  --basetemp=.worktrees/pytest-opening-controls`.
- **85 testes JavaScript passaram**: `node --test tests/*.test.cjs`.
  Os cinco casos novos de UI tiveram RED antes da correção: bootstrap antes
  do diagnóstico, preservação de configuração posterior, falha do diagnóstico,
  recusa do ensaio e corrida entre polling e confirmação.
- WebView2 nativo em 1280 × 820, 1920 × 1080 e 768 × 900, com 88 clipes
  sintéticos. Controles principais visíveis, sem erros JS ou overflow horizontal.
- Clique real no teste com monitor mudo produziu a mensagem de recusa;
  Configurar fone abriu o roteamento; após volume válido o estado foi confirmado
  e desativado novamente. Nenhum áudio foi enviado a hardware real.
- No smoke de 768, um Event bloqueou `get_startup_details`: biblioteca e
  handlers ficaram disponíveis e cliques nativos alternaram Preparar/Tocar
  enquanto `state.environment` ainda estava pendente. Liberar o Event completou
  o diagnóstico. Isso comprova independência do grid, sem inferir benchmark
  de abertura total a partir desse teste.
- `git diff --check` passou. Sem alteração de launcher, dados pessoais ou
  reinício da aplicação em uso.

Capturas: [controles](2026-11-02-controls/library.png),
[fone mudo](2026-11-02-controls/monitor-muted.png),
[teste ativo](2026-11-02-controls/monitor-active.png),
[biblioteca antes do diagnóstico](2026-11-02-controls/startup-pending.png).

Regra confirmada: rejeições de mudança de saída precisam de feedback junto
ao controle; estado de isolamento só pode ser anunciado após confirmação.
Polling não pode sobrescrever a intenção mais recente. Para startup, medir
separadamente pintura/interação e verificações opcionais posteriores.

## Revisão posterior: remoção do ensaio global

Por solicitação do usuário, o controle global, ajuda, mensagens e handlers de
ensaio foram removidos. A prévia no fone em cada card continua sendo o caminho
de teste. A primeira leitura de estado desativa um ensaio legado caso a janela
seja recarregada sem reiniciar o processo; a transição existente para os sons
antes de retornar ao roteamento normal. Não há chamada extra no início quando
o modo já está desligado.

Validação desta remoção: 84 testes JavaScript passaram; smoke nativo em
1280 × 820 abriu as oito telas sem erros JS, com os 17 controles restantes
visíveis. Backend e reprodução individual não foram alterados. Regra de UX:
priorizar a prévia por item, sem reintroduzir um modo global redundante.
