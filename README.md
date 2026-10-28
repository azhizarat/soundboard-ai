# 🎛️ Soundboard AI — VB-Audio Cable Studio

Soundboard digital para Windows projetado para enviar áudios direto no **CABLE Input (VB-Audio Virtual Cable)** (saindo no **CABLE Output** no Discord/jogos) enquanto toca simultaneamente no seu fone de ouvido (**Monitor Local**), com IA local integrada para identificar quem falou, transcrever, medir a emoção da voz e nomear/categorizar os botões automaticamente.

## ✨ Recursos Principais

1. **Saída Dupla Simultânea (`VB-Cable` + `Seu Fone`)**:
   - Detecta automaticamente o `CABLE Input (VB-Audio Virtual Cable)` e o seu fone padrão.
   - Volumes independentes para o cabo virtual e para o seu fone, além de botão **🎧 Testar só no Fone** em cada card.
   - **🎙️ Mic no Cabo (Passthrough)**: Encaminha seu microfone físico direto para o `CABLE Input` em tempo real.
2. **⚡ Clipar Call (Replay Instantâneo dos Últimos 15s)**:
   - Grava em memória (WASAPI Loopback) os últimos 15 segundos da call/sistema.
   - Aperte `Alt+F10` (ou clique em **⚡ Clipar Call**) quando alguém falar uma pérola: ele corta o silêncio, identifica quem falou, transcreve, classifica o humor e cria o botão na hora.
3. **🧠 Pipeline de IA Local Avançada**:
   - **Reconhecimento de Interlocutor (`TitaNet` + `sherpa-onnx`)**: Reaproveita seus modelos e o `referencias_vozes.npz` de `C:\Users\Usuario\Videos\.modelos_voz`. Renomeie `Amigo 1`, `Amigo 2` para o nome real de cada amigo na barra lateral!
   - **Análise Acústica de Emoção (`numpy`)**: Mede energia RMS/pico, frequência fundamental ($F_0$) e modulação rítmica para detectar **🔥 Rage / Grito**, **😂 Risada**, **🎙️ Fala Animada**, **🤫 Calmo / Sussurro** ou **🔊 Efeito**.
   - **Transcrição (`Faster-Whisper`) + Auto-Naming (LLM Local / Qwen2.5 via Ollama ou Heurística PT-BR)**: Cria títulos curtos e engraçados, escolhe emoji, cor e categoria automaticamente.
4. **📌 Modo Mini-Overlay & Atalhos Globais**:
   - Funciona em segundo plano enquanto você joga.
   - `Ctrl+Shift+S`: Parar todos os áudios (**Stop All**).
   - `Alt+F10`: Clipar os últimos 15s da call.
   - `Ctrl+Shift+O`: Alternar modo compacto **Mini-Overlay Always-on-Top**.

## 🚀 Como Iniciar

Basta dar dois cliques em:
```bat
iniciar_soundboard.bat
```
Ou via terminal:
```powershell
uv run --python 3.13 main.py
```
