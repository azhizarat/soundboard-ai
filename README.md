# Soundboard AI

Sistema de soundboard e roteamento de áudio digital para Windows com análise acústica local, normalização de loudness e identificação de interlocutores.

O software envia áudio diretamente para o driver de cabo virtual (VB-Audio Virtual Cable) para transmissão em programas de comunicação (como Discord) ou transmissão ao vivo, enquanto mantém monitoramento simultâneo no dispositivo de áudio local do usuário.

---

## 1. Descrição Geral

O Soundboard AI resolve a integração de reprodução de clipes de áudio em chamadas de voz e gravações. O sistema inclui:

- Roteamento duplo de áudio em tempo real (driver virtual + fone de ouvido).
- Mixer digital multicanal com vozes simultâneas, crossfade e limitador lookahead.
- Normalização de volume pelo padrão EBU R128 (-16 LUFS integrado).
- Motor de decisão multimodal para categorização e extração de títulos de clipes sem necessidade de modelos de linguagem externos.
- Identificação e diarização de voz baseada em modelos ONNX (TitaNet e PyAnnote).
- Buffer circular de gravação (replay retroativo dos últimos 60 segundos).
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
- **Limitador Lookahead**: Impede saturação digital e distorção sem introduzir artefatos harmônicos.

### 2.2 Normalização de Loudness (EBU R128)
- O sistema analisa cada arquivo de áudio segundo o algoritmo BS.1770-4 / EBU R128.
- Mede o valor de Loudness Integrado (LUFS) e o Pico Verdadeiro (True Peak dBFS).
- Aplica ganho compensatório automático para atingir a meta operacional de -16.0 LUFS.
- O usuário pode ajustar o ganho individual de cada clipe entre 0% e 200%.

### 2.3 Motor de Decisão Multimodal Local
O sistema classifica e nomeia os arquivos importados em tempo inferior a 1 milissegundo. O motor opera localmente sem serviços externos de LLM ou conexão de rede:
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
- **Classificação**: Calcula a similaridade por cosseno com centroides cadastrados no arquivo de referências (`referencias_vozes.npz`).
- **Cadastro**: Permite registrar novos interlocutores diretamente a partir de trechos de áudio.

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
Para habilitar diarização e identificação automática de voz, coloque os modelos na pasta `data/models/`:
- `pyannote-segmentation-3-0/model.onnx`
- `nemo_en_titanet_large.onnx`
- `referencias_vozes.npz`

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

As configurações são salvas automaticamente em `data/config.json`. Também podem ser definidas por variáveis de ambiente:

| Variável de Ambiente | Chave no Arquivo | Padrão | Descrição |
| :--- | :--- | :--- | :--- |
| `SOUNDBOARD_FFMPEG_PATH` | `ffmpeg_path` | `ffmpeg` | Caminho do executável FFmpeg. |
| `SOUNDBOARD_DATA_DIR` | `data_dir` | `data` | Pasta de armazenamento de dados e clipes. |
| `SOUNDBOARD_MODELS_DIR` | `models_dir` | `data/models` | Pasta de modelos de segmentação e embeddings. |
| `SOUNDBOARD_SEPARADOS_ROOT` | `separados_root` | Pasta de Vídeos | Pasta base para importação de lotes diarizados. |
| `SOUNDBOARD_OLLAMA_URL` | `ollama_url` | `http://127.0.0.1:11434` | Endereço de servidor Ollama (opcional). |
| `SOUNDBOARD_OLLAMA_MODEL` | `ollama_model` | `qwen2.5:1.5b` | Nome do modelo externo para consultas opcionais. |

---

## 7. Operação

### 7.1 Atalhos Globais de Teclado
Os atalhos operam em segundo plano durante a execução de jogos ou outros programas:
- `Ctrl + Shift + S`: Parar todos os áudios em reprodução (interrupção imediata com corte suave).
- `Alt + F10`: Capturar e processar os últimos 15 segundos do buffer circular de áudio da chamada.
- `Ctrl + Shift + O`: Alternar modo Mini-Overlay (janela compacta sempre visível sobre outras aplicações).
- `Ctrl + K`: Abrir caixa de busca difusa (fuzzy search) para localizar e disparar clipes rapidamente.

### 7.2 Edição e Ajuste de Clipes
No modal de edição do clipe, o operador pode:
- Definir pontos exatos de início e fim do trecho em segundos ou arrastando os cursores na forma de onda.
- Navegar pelo tempo do clipe utilizando a barra de reprodução interativa abaixo da forma de onda.
- Testar o corte diretamente no monitor local sem fechar a janela.
- Reanalisar o nível de volume e inspecionar o indicador de loudness em LUFS.
- Associar uma tecla de atalho global específica ao clipe.

### 7.3 Exportação e Importação de Pacotes
- **Exportar**: Salva os clipes selecionados, categorias e perfis de interlocutores em um único arquivo comprimido (`.sbpack` em formato ZIP com manifesto JSON).
- **Importar**: Carrega arquivos `.sbpack` e integra os clipes ao banco existente sem duplicar arquivos idênticos.

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

# Executar testes de correspondência difusa (Node.js)
node --test tests/test_fuzzy.test.cjs
```

---

## 10. Licença

Software distribuído sob os termos da licença MIT. Consulte o arquivo `LICENSE` para detalhes completos.
