# Soundboard AI

Soundboard local para Windows: capture momentos de chamadas ou gameplay, edite falas e monte diálogos, memes e sequências de áudio para disparar no cabo virtual ou ouvir no fone. Também oferece coleções, atalhos globais, normalização de loudness e análise de áudio com IA local.

O software envia áudio diretamente para o driver de cabo virtual (VB-Audio Virtual Cable) para transmissão em programas de comunicação (como Discord) ou transmissão ao vivo, enquanto mantém monitoramento simultâneo no dispositivo de áudio local do usuário.

**Versão atual: [v1.2.0 — Edição e fluxos visuais](https://github.com/azhizarat/soundboard-ai/releases/tag/v1.2.0).** Consulte as [notas do release](docs/releases/v1.2.0.md) para novidades, atualização e limites de validação.

O [guia de uso](docs/guia-de-uso.md) explica cada recurso e seus controles. As evidências mais recentes cobrem o [canvas de sequências](docs/validation/2026-11-02-sequence-canvas.md), a [edição contextual](docs/validation/2026-11-02-contextual-editor.md) e a [inicialização](docs/validation/2026-11-02-startup.md).

### Destaques da v1.2.0

- **Edite sem perder o original:** selecione pela waveform ou transcrição alinhada, retire partes do meio, una o restante, restaure trechos e desfaça alterações.
- **Monte diálogos num canvas:** nós de áudio, pausas, ramos simultâneos e espera por acionamento; biblioteca com busca e prévia, inserção pelos conectores e realce durante a execução.
- **Use IA onde o resultado aparece:** transcrição, título/categoria e identificação de vozes com andamento, cancelamento e propostas junto dos campos. Modelo e parâmetros ficam nas configurações globais.
- **Corrija e organize:** referências vocais confirmadas e reversíveis, fila de importação, coleções com posições fixas, grupos aleatórios e variantes de velocidade/tom/reverso.
- **Compartilhe:** packs compactos com o resultado final ou backups editáveis com origens e revisões.

---

## 1. Descrição Geral

O Soundboard AI resolve a integração de reprodução de clipes de áudio em chamadas de voz e gravações. O sistema inclui:

- Roteamento duplo de áudio em tempo real (driver virtual + fone de ouvido).
- Mixer digital multicanal com vozes simultâneas, crossfade e limitador lookahead.
- Normalização de volume pelo padrão EBU R128 (-16 LUFS integrado).
- Motor de decisão multimodal para categorização e extração de títulos de clipes sem necessidade de modelos de linguagem externos.
- Identificação e diarização de voz baseada em modelos ONNX (TitaNet e PyAnnote).
- Buffer circular de gravação, com captura retroativa pelo intervalo selecionado (até 120 segundos, conforme configurado).
- Interface de usuário gráfica acelerada via Microsoft Edge WebView2.

---

## 2. Arquitetura do Sistema

```
                  +--------------------------------+
                  |  Buffer Circular de Replay     |
                  |  (WASAPI Loopback - 60s)       |
                  +---------------+----------------+
                                  |
                                  v
+-------------------+     +---------------+     +--------------------+
|  Microfone Real   | --> | Mixer Digital | <-- | Banco de Clipes    |
+-------------------+     +-------+-------+     +--------------------+
                                  |
            +---------------------+---------------------+
            |                                           |
            v                                           v
+-----------------------+                   +-----------------------+
|  CABLE Input (Driver) |                   |  Monitor Local (Fone) |
|  -> Transmissão Call  |                   |  -> Escuta do Usuário |
+-----------------------+                   +-----------------------+
```

### 2.1 Roteamento e Mixer de Áudio
- **Saída Principal**: Envia os sinais combinados para o dispositivo `CABLE Input (VB-Audio Virtual Cable)`.
- **Monitor Local**: Envia áudio simultâneo para o dispositivo de saída selecionado pelo usuário com controle de volume independente.
- **Passagem de Microfone (Passthrough)**: Roteia o microfone físico diretamente para o cabo virtual.
- **Ducking Automático**: Reduz o volume dos clipes em reprodução em 12 dB quando o usuário fala no microfone.
- **Limitador Lookahead**: Controla picos na saída do mixer para reduzir saturação digital.

### 2.2 Normalização de Loudness (EBU R128)
- O sistema analisa cada arquivo de áudio segundo o algoritmo BS.1770-4 / EBU R128.
- Mede loudness integrado (LUFS) e pico de amostras; não apresenta pico de amostras como medição de true peak.
- Aplica ganho compensatório automático para atingir a meta operacional de -16.0 LUFS.
- O usuário pode ajustar o ganho individual de cada clipe entre 0% e 200%.

### 2.3 Motor de Decisão Multimodal Local
O sistema sugere categoria e título a partir do áudio e da transcrição. O tempo depende da duração, dos modelos e do equipamento; não há garantia de latência inferior a 1 ms. O motor opera localmente sem exigir serviços externos de LLM:
- **Análise Acústica**: Mede frequência fundamental média e desvio padrão ($F_0$), energia RMS, relação pico/RMS e taxa de surtos de energia.
- **Análise Léxica**: Processa transcrições com remoção de palavras vazias (stopwords) e correspondência de termos-chave em português.
- **Taxonomia de Categorias**:
  - `Rage`: Surtos de volume, gritos, palavrões ou alta frequência fundamental.
  - `Risadas & Pérolas`: Gargalhadas, padrões rítmicos de riso e vocabulário associado.
  - `Bordões & Memes`: Frases de efeito, exclamações curtas e expressões de impacto.
  - `Vinhetas & Trilhas`: Efeitos sonoros instrumentais, beats musicais ou áudio não verbal.
  - `Papo & Resenha`: Diálogos com dinâmica de fala convencional.

### 2.4 Reconhecimento e Diarização de Interlocutores
- **Segmentação**: Utiliza o modelo PyAnnote Segmentation 3.0 via ONNX Runtime para separar trechos de diferentes falantes.
- **Embeddings de Voz**: Utiliza o modelo NVIDIA NeMo TitaNet Large via ONNX Runtime para gerar vetores de características vocais (192 dimensões).
- **Classificação**: Compara embeddings com protótipos dos exemplos confirmados, usando limiar e margem para distinguir pessoa identificada, caso duvidoso e desconhecido. Similaridade não é probabilidade calibrada.
- **Cadastro**: Usa trechos originais confirmados e registra proveniência, dedupe e ações reversíveis. Arquivos legados de referências não substituem os perfis versionados; sua importação externa é explícita.

---

## 3. Requisitos do Sistema

### 3.1 Sistema Operacional
- Microsoft Windows 10 (versão 1903 ou superior) ou Microsoft Windows 11 (64 bits).

### 3.2 Dependências de Software
- **Python**: Versão 3.11, 3.12 ou 3.13 (instalado ou gerenciado via `uv`).
- **VB-Audio Virtual Cable**: Necessário para criação do canal de transmissão virtual.
- **FFmpeg**: Necessário para decodificação de formatos comprimidos (MP3, M4A, OGG, OPUS, AAC, FLAC).
- **Microsoft Edge WebView2 Runtime**: Necessário para exibição da interface de usuário.

---

## 4. Instalação

### 4.1 Clonar o Repositório
```powershell
git clone https://github.com/azhizarat/soundboard-ai.git
cd soundboard-ai
```

### 4.2 Instalar o Gerenciador de Dependências
Recomenda-se o uso do `uv` para isolamento do ambiente e instalação das dependências:
```powershell
pip install uv
```

### 4.3 Sincronizar Dependências
```powershell
uv sync
```

### 4.4 Configurar Modelos de IA (Opcional)
Configure modelo e dispositivo de transcrição na engrenagem **Configurações**. A primeira carga pode precisar baixar os pesos do modelo; a inferência é local.

Para habilitar diarização e identificação automática de voz, coloque os modelos abaixo no diretório configurado em `models_dir` (padrão `data/models/`, ou o diretório legado detectado):
- `pyannote-segmentation-3-0/model.onnx`
- `nemo_en_titanet_large.onnx`

As referências vocais são criadas e geridas pelo aplicativo a partir de exemplos confirmados. Não é necessário fornecer um `referencias_vozes.npz` para uma biblioteca nova; referências legadas têm importação explícita. Ollama é opcional para sugestões de metadados e pode apontar para um servidor local.

---

## 5. Execução

### 5.1 Execução Padrão
Para iniciar a aplicação com janela gráfica e verificação de diagnóstico:
```powershell
uv run main.py
```

Também é possível utilizar o script em lote fornecido:
```cmd
iniciar_soundboard.bat
```

### 5.2 Parâmetros de Linha de Comando
| Parâmetro | Descrição |
| :--- | :--- |
| `--data-dir <caminho>` | Define o diretório base de persistência (padrão: `data/`). |
| `--minimized` | Inicia a aplicação minimizada na bandeja do sistema. |
| `--debug` | Habilita mensagens detalhadas no console e registra logs em `data/logs/`. |

---

## 6. Configuração

Caminhos, preferências de IA e política de recursos ficam em `data/config.json`; biblioteca e ajustes de operação são persistidos pelo store em `data/`. As variáveis abaixo sobrescrevem as opções correspondentes do arquivo:

| Variável de Ambiente | Chave no Arquivo | Padrão | Descrição |
| :--- | :--- | :--- | :--- |
| `SOUNDBOARD_FFMPEG` | `ffmpeg_path` | `ffmpeg` | Caminho do executável FFmpeg. |
| `SOUNDBOARD_DATA_DIR` | `data_dir` | `data` | Pasta de armazenamento de dados e clipes. |
| `SOUNDBOARD_MODELS_DIR` | `models_dir` | `data/models` | Pasta de modelos de segmentação e embeddings. |
| `SOUNDBOARD_SEPARADOS_ROOT` | `separados_root` | Pasta de Vídeos | Pasta base para importação de lotes diarizados. |
| `SOUNDBOARD_OLLAMA_URL` | `ollama_url` | `http://127.0.0.1:11434` | Endereço de servidor Ollama (opcional). |
| `SOUNDBOARD_MIXER` | `mixer_mode` | `mixer` | Backend de reprodução; sequências e coleções exigem o mixer. |
| `SOUNDBOARD_OLLAMA_MODEL` | `ollama_model` | `qwen2.5:1.5b` | Modelo do servidor Ollama para sugestões opcionais. |

---

## 7. Operação

### 7.1 Atalhos Globais de Teclado
Os três primeiros atalhos são globais; Ctrl+K atua na busca da janela:
- `Ctrl + Shift + S`: Parar todos os áudios e cancelar as próximas etapas de sequências.
- `Alt + F10`: Capturar o intervalo de replay configurado (padrão de 15 segundos).
- `Ctrl + Shift + O`: Alternar modo Mini-Overlay (janela compacta sempre visível sobre outras aplicações).
- `Ctrl + K`: Abrir caixa de busca difusa (fuzzy search) para localizar e disparar clipes rapidamente.

### 7.2 Edição e Ajuste de Clipes
- Arraste na waveform para selecionar e ajuste pelas alças de início/fim. Arraste a alça branca ou use Ctrl+arraste para mover a agulha independentemente.
- **Play** (ou Espaço fora dos campos) toca a seleção a partir da agulha; se ela estiver fora ou no fim, recomeça pelo início. A prévia usa somente o monitor.
- **Recortar**, **Remover trecho** e **Restaurar trecho** alteram a montagem preservando a origem. Há desfazer/refazer e rascunho recuperável.
- Na transcrição alinhada, clique nas palavras para selecionar; Shift+clique amplia e duplo clique permite corrigir. Sem alinhamento, o texto continua editável, sem inventar tempos por palavra.
- Transcrever, sugerir título/categoria e identificar vozes ficam nas respectivas abas, com andamento e resultado no próprio local. Resultados antigos não substituem silenciosamente correções humanas.
- Em **Título e aparência**, ajuste nome, categoria, cor, emoji e atalho. A seção recolhida **Criar uma variante de áudio** oferece velocidade, tom e reverso.

### 7.3 Exportação e Importação de Pacotes
- **Exportar**: Gera `.zip` com manifesto versionado para clipes, coleções ou sequências e suas dependências. **Compacto** inclui somente o resultado selecionado; **Backup editável** inclui origens e revisões. Exemplos vocais locais não são exportados.
- **Importar**: Valida o grafo, remapeia IDs e informa conflitos de atalhos, preservando roteamento e banco vocal do destinatário. Importação repetida é idempotente enquanto os nós da cópia anterior existem. A leitura do formato legado continua disponível.

### 7.4 Fluxos de sons

Abra **Sequências**. Pesquise um áudio na biblioteca por título, frase, pessoa ou categoria; use ▶ para ouvi-lo antes de adicionar. Arraste até um conector **+** para inserir no meio ou num ramo; também é possível clicar no conector e depois no áudio.

Combine **Áudio**, **Pausa**, **Juntos** (ramos simultâneos) e **Aguardar** (continua em Avançar ou novo acionamento do atalho). Arraste nós entre conectores para reorganizar; os ramos recebem áudios e pausas. Cada ocorrência tem corte e volume próprios, sem modificar o clipe original.

Arraste o fundo para navegar, use Ctrl+roda para zoom e **Enquadrar** para ver o conjunto. **Ouvir fluxo** testa no monitor; **Transmitir** usa as saídas configuradas. Os nós ativos ficam destacados. Há undo/redo, duplicação, navegação por teclado e Ctrl+S para salvar.

### 7.5 Importação, vozes e coleções

- **Importação:** fila por arquivo, com estado, histórico, cancelamento, nova tentativa e deduplicação. Áudio fica disponível antes de a IA concluir; mídias longas pedem uma janela de até 120 segundos. Vídeos dependem de FFmpeg/ffprobe.
- **Vozes:** confirme exemplos, revise atribuições e remova referências incorretas. O aprendizado atualiza o banco de exemplos; não retreina continuamente a rede neural. Previsões não viram exemplos confirmados automaticamente.
- **Coleções:** organize clipes e sequências em pads com posições fixas, grupos de fala/trilha/efeito, atalhos, loop e acionamento enquanto a tecla está pressionada. Grupos aleatórios evitam repetição imediata quando há alternativas.

### 7.6 Atualizar uma instalação existente

Feche o aplicativo e faça uma cópia da pasta de dados completa antes da primeira abertura de uma versão nova. As migrações preservam áudios e criam backups do estado antigo, mas esses arquivos não substituem um backup completo da biblioteca.

```powershell
git pull --ff-only origin main
uv sync --locked
uv run main.py
```

Use o mesmo `--data-dir` se sua biblioteca estiver em outra pasta. A primeira abertura pode levar mais tempo para migrar um acervo antigo; esse custo não se repete nas aberturas seguintes com o schema atual. Para voltar à v1.1.0, use também a cópia dos dados anterior à migração.


---

## 8. Diagnóstico e Verificação do Ambiente

A aplicação possui uma rotina interna de diagnóstico acessível no ícone de engrenagem da barra superior. A rotina verifica:
1. Presença e versão do executável FFmpeg no caminho especificado.
2. Presença e conectividade do dispositivo VB-Audio Virtual Cable.
3. Integridade dos arquivos de modelo de segmentação e embedding na pasta de modelos.
4. Permissão de leitura e gravação no diretório de dados.
5. Presença do ambiente de execução Microsoft Edge WebView2.

---

## 9. Testes Automatizados

O projeto mantém cobertura de testes unitários e de integração para o motor de áudio, persistência, diagnóstico e processamento de sinal:

```powershell
# Executar todos os testes do backend
uv run pytest -v

# Executar testes da rotina de diagnóstico
uv run pytest tests/test_a_doctor.py -v

# Executar testes da interface e dos modelos de edição (Node.js)
node --test tests/*.test.cjs
```

---

Na validação da v1.2.0, passaram **770 testes Python e 131 JavaScript**, com hardware de áudio bloqueado no ensaio completo. Fluxos foram exercitados em WebView2 real com entrada de mouse em janelas de 1280 e 768 px e dados sintéticos. Esses resultados não medem a qualidade do reconhecimento em gravações reais nem certificam o roteamento físico em cada equipamento. Veja o [registro detalhado](docs/validation/2026-11-02-sequence-canvas.md).

## 10. Licença

Software distribuído sob os termos da licença MIT. Consulte o arquivo `LICENSE` para detalhes completos.
