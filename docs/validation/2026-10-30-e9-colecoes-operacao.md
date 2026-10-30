# E9 — coleções e operação

Data: 30/10/2026. Implementação local, sem staging/commit. E0–E8 e alterações anteriores preservados. Revisões externas e smoke WebView são coordenados separadamente; este registro não substitui esses aceites.

## Comportamento entregue

- Preparar e Tocar compartilham a biblioteca. A área Tocar usa referências e posições fixas de uma coleção salva; atualização de metadados/IA não repagina seus botões ou atalhos. Categorias e pessoas continuam filtros da biblioteca.
- Coleções têm nome, número de colunas, posições com lacunas, rótulos, alvos clipe/sequência, atalhos, grupo, redisparo, segurar, loop e duração da saída suave. Mesma mídia pode aparecer em várias coleções sem cópia.
- Schema 5 migra versões anteriores com backup de coleções verificado antes da gravação. Salvar usa revisão otimista e rollback em falha. Excluir clipe/sequência referenciado é recusado, informando os nomes dos usos. Excluir coleção não exclui mídia e não cancela áudio em andamento.
- Atalhos globais e de biblioteca são reservados; normalização reconhece ordem de modificadores, maiúsculas e aliases (por exemplo `control`/`ctrl`). Atalhos de coleções inativas podem coincidir. Conflitos aparecem na UI e impedem novas configurações conflitantes.
- Disparos da UI e atalhos usam o mesmo dispatcher. Reiniciar cria uma nova identidade UUID; parar e ignorar são opções explícitas. Repetição de tecla é ignorada. Trocar a coleção preserva áudio e o key-up encerra a execução originalmente sustentada, mesmo que o novo layout não use a tecla.
- Falas substituem somente falas no mesmo domínio monitor/transmissão. Trilhas e efeitos de outros grupos permanecem. Parar agora e saída suave são ações separadas. Loops de clipe usam a voz do mixer; loops/avanços de sequência usam o executor E8.
- Ensaio abrange clipes públicos, prévias, atalhos de biblioteca/globais/coleção e sequências. Ativar ou desativar interrompe ativos e invalida pedidos anteriores pelo epoch do engine. Mudar para ensaio usa parada sem fade residual no cabo. O modo não é restaurado automaticamente após reiniciar o app.
- IDs de dispositivo definem isolamento; nomes não comprovam separação. Ensaio recusa monitor igual à transmissão, ausente ou mudo. A troca de dispositivo que romperia isolamento é recusada enquanto o ensaio está ativo.
- Perda de rota ou falha nativa cancela execuções e mantém bloqueio até selecionar uma saída explicitamente. Enumerar dispositivos não faz auto-rebind; reconexão não retoma som antigo. A UI mantém destino, estado, falhas e controles de parada visíveis.
- Sequência manual avança apenas quando o runner E8 está no bloco de acionamento; autorepeat não avança nem reinicia. Continua existindo um único executor de sequências: uma segunda sequência requer encerramento/substituição explícita da ativa.

## Contrato do bridge

```python
get_collections()
save_collection(document)
delete_collection(collection_id)
activate_collection(collection_id)
trigger_collection_pad(collection_id, pad_id, phase='press', repeat=False, token=None)
stop_collection_pad(execution_id, fade=False)
set_rehearsal(enabled)
get_operation_status()
```

Documento mínimo:

```json
{
  "schema_version": 1, "id": "show", "name": "Apresentação", "revision": 0,
  "columns": 4,
  "pads": [{
    "id": "fala", "position": 5, "label": "Abertura",
    "target": {"type": "clip", "id": "clip-id"}, "hotkey": "f1",
    "group": "speech", "retrigger": "restart", "hold": false,
    "loop": false, "fade_ms": 250
  }]
}
```

`position` começa em zero. Grupos: `speech`, `track`, `effect`. Redisparo: `restart`, `stop`, `ignore`. O token identifica um gesto único, não a coleção corrente: frontend cria um UUID antes do await e envia o release imediatamente, sem aguardar o press. Se release chegar antes da admissão do press, um tombstone cancela aquele gesto; a retenção é limitada a 4096 tokens. Teclado faz registro leve síncrono e prepara em worker, mantendo key-up e Stop All livres. Para hold sem token, o backend gera `hold:UUID` e retorna o valor; o chamador deve passá-lo no release. `execution_id` identifica a execução para parada explícita. Ativação/edição não refaz atalhos de uma tecla ainda pressionada.

## Evidência TDD

- RED inicial de persistência: 9 falhas por APIs de coleção ausentes/schema ainda 4; GREEN coleção + sequências: 21.
- RED inicial de operação: fixture afirmou ausência da API, reportada pelo pytest como 9 erros de setup; após implementação, operação/ensaio/sequence_api/editor_api: 27 PASS.
- RED adicional: colisão criada por atalho global, fallback involuntário após perda do cabo, alteração que quebrava isolamento. Corrigidos com validação antes da publicação e bloqueio até nova seleção.
- RED de áudio observado: depois de confirmar ensaio, o cabo ainda emitia fade de 10 ms. Teste usa PCM real do mixer; GREEN após parada imediata na transição.
- RED de ciclo de vida: falha do stream com dispositivo ainda enumerado não aparecia; pump eliminava fade explícito de sequência; preparação cancelada retinha entrada. Todos corrigidos e verificados.
- RED de reconexão: `refresh_devices` alterava IDs silenciosamente. Agora só enumera.
- RED de concorrência: hotkeys globais de clipe/sequência capturavam modo/epoch depois da fila da thread. Agora capturam no recebimento e pedidos antigos não iniciam após mudança de modo, inclusive mudança dupla.
- RED de frontend: 5 testes falharam pela ausência do módulo. GREEN cobre lacunas/identidade, referências compartilhadas, resposta de save obsoleta, release enquanto press aguarda e integração HTML.
- Revisão Sol identificou dois bloqueadores de hold: release da UI aguardava resolver press e callback de teclado preparava áudio na fila de eventos. RED específico reproduziu ambos e o caso de release antes da admissão; GREEN posterior: 22 Python direcionados e 6 JS. Agora release é imediato, tokens são UUID por gesto, backend reconhece soltura antecipada e o teclado prepara fora da fila serial. Teste espera o worker terminar antes de afirmar ausência de áudio tardio. WebView adversarial do coordenador confirmou que press retido não inicia depois de release e que um gesto novo funciona normalmente.
- Revisão Astra identificou cinco falhas de concorrência/ciclo de vida, todas reproduzidas em RED: ACK de ensaio ultrapassava Stop All concorrente; lote de atalhos validava campos contra estados diferentes; publicação de settings podia ultrapassar validação de ensaio; cancelamento na reiteração de loop podia ficar em preparing; completed intermediário permitia ao polling remover o dono do loop, deixando áudio voltar depois de Stop All.
- Correção Astra: Stop All oferece espera por condição sem prender callbacks sob lock do engine; reentrância simples permanece e ensaio aguarda parada imediata antes do ACK. Transição bloqueia novas admissões sem segurar lock da API durante a espera. Validação/publicação de settings compartilha exclusão com ensaio e valida o candidato inteiro, inclusive troca válida de atalhos, com revalidação no store sob lock. Loop publica preparing (não terminal) entre iterações, conserva dono, verifica epoch e trata cancelamento antes/depois da preparação. GREEN do conjunto E9 + playback_groups + sequence_runner: 60 PASS em 8,47 s (`.worktrees/e9-astra-green2.log`). Teste adicional verifica callback de dono que chama API durante a espera, sem deadlock.

## Gates

Ambiente isolado antes dos imports:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:SOUNDBOARD_MODELS_DIR='D:\repos\soundboard-ai\.worktrees\e9-models'
$env:SOUNDBOARD_SEPARADOS_ROOT='D:\repos\soundboard-ai\.worktrees\e9-separados'
.venv\Scripts\python.exe -m pytest -q --basetemp=.worktrees/e9-full-2
```

- Full Python anterior à correção de hold da revisão: **606 PASS em 41,45 s**; `.worktrees/e9-full-2.log`. Full final aguarda re-review.
- Full JavaScript anterior à correção de hold (todos `tests/*.test.cjs`, Node 24.21.0 instalado): **60 PASS**, zero falhas; `.worktrees/e9-node-full.log`. Delta hold: 6 testes JS PASS.
- Primeiro full: 594 PASS/6 falhas; quatro asserts de migração ainda exigiam schema 4 e dois fixtures parciais não construíam estado operacional. Ajustes limitados à expectativa schema 5 e compatibilidade dos fixtures; full subsequente acima passou.
- Backend smoke do coordenador: engine/store/mixer reais, WAV sintético e streams falsos. Confirmou trilha preservada na substituição de fala, efeitos sobrepostos, hold atravessando coleção, barreira ensaio sem cauda no cabo, todas as entradas públicas, sequência manual, perda/reconexão e seleção explícita, hashes de fonte intactos.
- WebView smoke do coordenador: criação/edição por controles em 768 px, posições 3/6 estáveis depois de refresh, monitor-only e edição feita durante save preservada. Contraste dos botões e wrap do topo ajustados após inspeção; nova verificação em 768 e 1280 px passou. Hold adversarial adicional passou com release anterior ao press e gesto posterior independente.

## Arquivos e limites

Novos módulos: `sound_collections.py`, `operation_service.py`, `ui/collections.js`, `ui/collections.css`. Integrações: `store.py`, `main.py`, `hotkeys.py`, `audio_engine.py`, `sound_sequences.py`, `sequence_service.py`, `sequence_runner.py`, `ui/app.js`, `ui/index.html`. Novos testes: `test_collections.py`, `test_trigger_policy.py`, `test_rehearsal_routing.py`, `test_collections.test.cjs`. Asserções de versão anteriores atualizadas em `test_sound_sequences.py`, `test_edit_persistence.py`, `test_voice_profiles.py`.

Não foram abertos streams de hardware, executados modelos de IA ou utilizadas bibliotecas/vozes/capturas pessoais nos testes. Mixer legado mantém a indisponibilidade explícita de grupos/coleções/sequências definida em E7; reprodução avulsa permanece compatível. Testes com streams falsos comprovam decisão, PCM produzido e cancelamento, não latência física, isolamento de aplicativo de chamada, qualidade perceptiva ou comportamento de um driver real. E10 (variantes/shuffle/packs de coleções) não foi incluída.
