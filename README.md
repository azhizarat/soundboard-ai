# Soundboard AI

Sistema de soundboard e roteamento de audio digital para Windows com analise acustica local, normalizacao de loudness e identificacao de interlocutores.

O software envia audio diretamente para o driver de cabo virtual (VB-Audio Virtual Cable) para transmissao em programas de comunicacao (como Discord) ou transmissao ao vivo, enquanto mantem monitoramento simultaneo no dispositivo de audio local do usuario.

---

## 1. Descricao Geral

O Soundboard AI resolve a integracao de reproducao de clipes de audio em chamadas de voz e gravacoes. O sistema inclui:

- Roteamento duplo de audio em tempo real (driver virtual + fone de ouvido).
- Mixer digital multicanal com vozes simultaneas, crossfade e limitador lookahead.
- Normalizacao de volume pelo padrao EBU R128 (-16 LUFS integrado).
- Motor de decisao multimodal para categorizacao e extracao de titulos de clipes sem necessidade de modelos de linguagem externos.
- Identificacao e diarizacao de voz baseada em modelos ONNX (TitaNet e PyAnnote).
- Buffer circular de gravacao (replay retroativo dos ultimos 60 segundos).
- Interface de usuario grafica acelerada via Microsoft Edge WebView2.

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
|  -> Transmissao Call  |                   |  -> Escuta do Usuario |
+-----------------------+                   +-----------------------+
```

### 2.1 Roteamento e Mixer de Audio
- **Saida Principal**: Envia os sinais combinados para o dispositivo `CABLE Input (VB-Audio Virtual Cable)`.
- **Monitor Local**: Envia audio simultaneo para o dispositivo de saida selecionado pelo usuario com controle de volume independente.
- **Passagem de Microfone (Passthrough)**: Roteia o microfone fisico diretamente para o cabo virtual.
- **Ducking Automatico**: Reduz o volume dos clipes em reproducao em 12 dB quando o usuario fala no microfone.
- **Limitador Lookahead**: Impede saturacao digital e clipping sem introduzir distorcao harmonica.

### 2.2 Normalizacao de Loudness (EBU R128)
- O sistema analisa cada arquivo de audio segundo o algoritmo BS.1770-4 / EBU R128.
- Mede o valor de Loudness Integrado (LUFS) e o Pico Verdadeiro (True Peak dBFS).
- Aplica ganho compensatorio automatico para atingir a meta operacional de -16.0 LUFS.
- O usuario pode ajustar o ganho individual de cada clipe entre 0% e 200%.

### 2.3 Motor de Decisao Multimodal Local
O sistema classifica e nomeia os arquivos importados em tempo inferior a 1 milissegundo. O motor opera localmente sem servicos externos de LLM ou conexao de rede:
- **Analise Acustica**: Mede frequencia fundamental media e desvio padrao ($F_0$), energia RMS, relacao pico/RMS e taxa de surtos de energia.
- **Analise Lexica**: Processa transcricoes com remocao de palavras vazias (stopwords) e correspondencia de tokens chave em portugues.
- **Taxonomia de Categorias**:
  - `Rage`: Surtos de volume, gritos, palavroes ou alta frequencia fundamental.
  - `Risadas & Perolas`: Gargalhadas, padroes ritmicos de riso e vocabulario associado.
  - `Bordoes & Memes`: Frases de efeito, exclamacoes curtas e expressoes de impacto.
  - `Vinhetas & Trilhas`: Efeitos sonoros instrumentais, beats musicais ou audio nao-verbal.
  - `Papo & Resenha`: Dialogos com dinamica de fala convencional.

### 2.4 Reconhecimento e Diarizacao de Interlocutores
- **Segmentacao**: Utiliza o modelo PyAnnote Segmentation 3.0 via ONNX Runtime para separar trechos de diferentes falantes.
- **Embeddings de Voz**: Utiliza o modelo NVIDIA NeMo TitaNet Large via ONNX Runtime para gerar vetores de caracteristicas vocais (192 dimensoes).
- **Classificacao**: Calcula a similaridade por cosseno com centroides cadastrados no arquivo de referencias (`referencias_vozes.npz`).
- **Cadastro**: Permite registrar novos interlocutores diretamente a partir de trechos de audio.

---

## 3. Requisitos do Sistema

### 3.1 Sistema Operacional
- Microsoft Windows 10 (versao 1903 ou superior) ou Microsoft Windows 11 (64 bits).

### 3.2 Dependencias de Software
- **Python**: Versao 3.11, 3.12 ou 3.13 (instalado ou gerenciado via `uv`).
- **VB-Audio Virtual Cable**: Necessario para criacao do canal de transmissao virtual.
- **FFmpeg**: Necessario para decodificacao de formatos comprimidos (MP3, M4A, OGG, OPUS, AAC, FLAC).
- **Microsoft Edge WebView2 Runtime**: Necessario para exibicao da interface de usuario.

---

## 4. Instalacao

### 4.1 Clonar o Repositorio
```powershell
git clone https://github.com/azhizarat/soundboard-ai.git
cd soundboard-ai
```

### 4.2 Instalar o Gerenciador de Dependencias
Recomenda-se o uso do `uv` para isolamento do ambiente e instalacao das dependencias:
```powershell
pip install uv
```

### 4.3 Sincronizar Dependencias
```powershell
uv sync
```

### 4.4 Configurar Modelos de IA (Opcional)
Para habilitar diarizacao e identificacao automatica de voz, coloque os modelos na pasta `data/models/`:
- `pyannote-segmentation-3-0/model.onnx`
- `nemo_en_titanet_large.onnx`
- `referencias_vozes.npz`

---

## 5. Execucao

### 5.1 Execucao Padrao
Para iniciar a aplicacao com janela grafica e verificacao de diagnostico:
```powershell
uv run main.py
```

Tambem e possivel utilizar o script em lote fornecido:
```cmd
iniciar_soundboard.bat
```

### 5.2 Parametros de Linha de Comando
| Parametro | Descricao |
| :--- | :--- |
| `--data-dir <caminho>` | Define o diretorio base de persistencia (padrao: `data/`). |
| `--minimized` | Inicia a aplicacao minimizada na bandeja do sistema. |
| `--debug` | Habilita mensagens detalhadas no console e registra logs em `data/logs/`. |

---

## 6. Configuracao

As configuracoes sao salvas automaticamente em `data/config.json`. Tambem podem ser definidas por variaveis de ambiente:

| Variavel de Ambiente | Chave no Arquivo | Padrao | Descricao |
| :--- | :--- | :--- | :--- |
| `SOUNDBOARD_FFMPEG_PATH` | `ffmpeg_path` | `ffmpeg` | Caminho do executavel FFmpeg. |
| `SOUNDBOARD_DATA_DIR` | `data_dir` | `data` | Pasta de armazenamento de dados e clipes. |
| `SOUNDBOARD_MODELS_DIR` | `models_dir` | `data/models` | Pasta de modelos de segmentacao e embeddings. |
| `SOUNDBOARD_SEPARADOS_ROOT` | `separados_root` | Pasta de Videos | Pasta base para importacao de lotes diarizados. |
| `SOUNDBOARD_OLLAMA_URL` | `ollama_url` | `http://127.0.0.1:11434` | Endereco de servidor Ollama (opcional). |
| `SOUNDBOARD_OLLAMA_MODEL` | `ollama_model` | `qwen2.5:1.5b` | Nome do modelo externo para consultas opcionais. |

---

## 7. Operacao

### 7.1 Atalhos Globais de Teclado
Os atalhos operam em segundo plano durante a execucao de jogos ou outros programas:
- `Ctrl + Shift + S`: Parar todos os audios em reproducao (interrupcao imediata com corte suave).
- `Alt + F10`: Capturar e processar os ultimos 15 segundos do buffer circular de audio da chamada.
- `Ctrl + Shift + O`: Alternar modo Mini-Overlay (janela compacta sempre visivel sobre outras aplicacoes).
- `Ctrl + K`: Abrir caixa de busca difusa (fuzzy search) para localizar e disparar clipes rapidamente.

### 7.2 Edicao e Ajuste de Clipes
No modal de edicao do clipe, o operador pode:
- Definir pontos exatos de inicio e fim do trecho em segundos ou arrastando os cursores na forma de onda.
- Navegar pelo tempo do clipe utilizando a barra de reproducao interativa abaixo da forma de onda.
- Testar o corte diretamente no monitor local sem fechar a janela.
- Reanalisar o nivel de volume e inspecionar o indicador de loudness em LUFS.
- Associar uma tecla de atalho global especifica ao clipe.

### 7.3 Exportacao e Importacao de Pacotes
- **Exportar**: Salva os clipes selecionados, categorias e perfis de interlocutores em um unico arquivo comprimido (`.sbpack` em formato ZIP com manifesto JSON).
- **Importar**: Carrega arquivos `.sbpack` e integra os clipes ao banco existente sem duplicar arquivos identicos.

---

## 8. Diagnostico e Verificacao do Ambiente

A aplicacao possui uma rotina interna de diagnostico acessivel no icone de engrenagem da barra superior. A rotina verifica:
1. Presenca e versao do executavel FFmpeg no caminho especificado.
2. Presenca e conectividade do dispositivo VB-Audio Virtual Cable.
3. Integridade dos arquivos de modelo de segmentacao e embedding na pasta de modelos.
4. Permissao de leitura e gravacao no diretorio de dados.
5. Presenca do ambiente de execucao Microsoft Edge WebView2.

---

## 9. Testes Automatizados

O projeto mantem cobertura de testes unitarios e de integracao para o motor de audio, persistencia, diagnostico e processamento de sinal:

```powershell
# Executar todos os testes do backend
uv run pytest -v

# Executar testes da rotina de diagnostico
uv run pytest tests/test_a_doctor.py -v

# Executar testes de correspondencia difusa (Node.js)
node --test tests/test_fuzzy.test.cjs
```

---

## 10. Licenca

Software distribuido sob os termos da licenca MIT. Consulte o arquivo `LICENSE` para detalhes completos.
