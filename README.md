# ClipForge

Ferramenta local que transforma vídeos longos **seus** (podcast, aula, live, entrevista) em cortes curtos verticais (9:16) com legendas animadas:

1. Transcreve o vídeo com o tempo de cada palavra (faster-whisper, no seu computador).
2. Sugere os melhores cortes com IA (Claude), com título, nota e motivo.
3. Deixa você criar e ajustar os cortes à mão. Um corte nunca começa nem termina no meio de uma palavra.
4. Gera legendas sincronizadas e editáveis em 4 estilos (ou nos seus próprios).
5. Exporta em 1080x1920 com a legenda no vídeo, além de `.srt` e `.ass`.

> **Use apenas vídeos seus ou com autorização.** O ClipForge só aceita arquivos do seu computador e não baixa vídeos de nenhuma plataforma.

Tudo roda localmente. A única coisa que sai do seu computador é o **texto** da transcrição, enviado ao Claude para escolher os cortes. O vídeo nunca é enviado. Sem chave da API, tudo funciona, menos as sugestões automáticas.

---

## Instalação

Você vai precisar de **FFmpeg**, **uv** (instala o Python certo para o projeto) e, opcionalmente, de uma **chave da API da Anthropic**.

### 1. FFmpeg

| Sistema | Comando |
|---|---|
| Linux (Ubuntu/Debian) | `sudo apt install ffmpeg` |
| macOS | `brew install ffmpeg` (instale o [Homebrew](https://brew.sh) antes) |
| Windows | `winget install Gyan.FFmpeg`, depois feche e abra o terminal |

Confira com `ffmpeg -version`.

### 2. uv

- Linux/macOS: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

Feche e abra o terminal depois de instalar.

### 3. ClipForge

Dentro da pasta `clipforge`:

```bash
uv python install 3.12
uv venv --python 3.12 .venv
uv pip install -r requirements.txt
cp .env.example .env        # no Windows: copy .env.example .env
```

Abra o `.env` num editor de texto e cole a sua chave em `ANTHROPIC_API_KEY=` (crie uma em https://console.anthropic.com/). Nunca compartilhe esse arquivo.

### 4. Verificar

```bash
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m app.doctor
```

Os itens `FALTA` precisam ser corrigidos. Os `AVISO` não impedem o uso (por exemplo, sem placa de vídeo NVIDIA tudo roda no processador, só que mais devagar).

### 5. Atalho (Linux)

```bash
bash scripts/install-launcher.sh
```

Isso cria **ClipForge** no menu de aplicativos. A partir daí, não é preciso abrir o terminal.

---

## Uso (interface web)

Abra **ClipForge** pelo menu de aplicativos, ou rode `python -m app` com o ambiente ativado. O navegador abre em http://127.0.0.1:8000.

1. **Projetos:** arraste um vídeo seu. A transcrição e as sugestões da IA começam sozinhas. Um vídeo de 10 minutos leva de 3 a 6 minutos para transcrever num notebook comum.
2. **Cortes:**
   - Ajuste início e fim pelo campo numérico, pelos botões −/+ (0,1 s) ou por **Encaixar nas palavras**.
   - Crie cortes com **Marcar início / Marcar fim** no player ou selecionando um trecho da transcrição.
   - Clique numa palavra da transcrição para ir até ela no vídeo.
   - Dá para duplicar, reordenar e excluir (com **Desfazer**). Tudo é salvo automaticamente.
3. **Legendas:**
   - Escolha o estilo (a prévia aparece sobre o vídeo durante o play).
   - Para corrigir uma palavra, clique nela e digite. Enter salva, Esc cancela, e texto vazio remove a palavra.
   - Em **Criar um estilo a partir do selecionado** você ajusta fonte, cores, tamanho, posição etc. e salva como um estilo novo. Os estilos de fábrica nunca são alterados.
4. **Exportar:**
   - Escolha o formato: original; vertical **fit** (barras), **center** (recorte central), **blur** (fundo desfocado) ou **seguir quem fala**. Esse último acompanha o rosto e, com várias pessoas, corta para quem está falando (pelo movimento da boca durante a fala).
   - Escolha a legenda, a resolução (1080x1920 ou 720x1280) e a velocidade (**Rápido**, o padrão, ou **Qualidade máxima**, cerca de 2x mais lento e com diferença quase invisível).
   - Renderize um corte ou todos e baixe **MP4**, **SRT** e **ASS**.
   - **Só legendas** gera `.srt` e `.ass` do vídeo inteiro, sem cortar.

**No topo da tela:**
- **Fila:** mostra tudo o que está rodando em todos os projetos, com progresso e botão de cancelar.
- **Encerrar:** fecha o ClipForge. O servidor continua rodando em segundo plano até você clicar em Encerrar.

**Espaço em disco:** na aba Exportar, **Apagar vídeos gerados** libera espaço mantendo os cortes. Na lista de projetos, **Excluir** apaga o projeto inteiro. Em nenhum dos dois casos o vídeo original no seu computador é apagado.

---

## Uso (linha de comando)

Com o ambiente ativado:

```bash
python cli.py caminho/do/video.mp4 --vertical blur --captions --style karaoke
```

| Opção | O que faz |
|---|---|
| `--clip 1:05-1:48` | Cria um corte manual (pode repetir). O início e o fim são ajustados às palavras. |
| `--no-ai` | Não chama o Claude. |
| `--resuggest` | Descarta as sugestões antigas e pede novas. |
| `--no-render` | Só transcreve e sugere, sem gerar os vídeos. |
| `--lang en` | Idioma do vídeo (`pt`, `en` ou `auto`). |
| `--whisper-model medium` | Troca o modelo de transcrição. |
| `--vertical blur` | Saída 9:16: `fit`, `center`, `blur` ou `face` (segue o rosto de quem fala). |
| `--speed qualidade` | Velocidade do render: `rapido` (padrão) ou `qualidade`. |
| `--captions` | Grava a legenda nos cortes e salva `.ass` e `.srt` ao lado de cada `.mp4`. |
| `--style karaoke` | Estilo da legenda: `classic`, `karaoke`, `pop`, `boxed` ou um estilo seu. |
| `--captions-only` | Só gera `.srt` e `.ass` do vídeo inteiro, em `exports/`. |

A CLI e a interface usam os mesmos projetos. Uma transcrição feita por uma é reaproveitada pela outra.

---

## Configuração (`.env`)

| Variável | Padrão | Para quê |
|---|---|---|
| `ANTHROPIC_API_KEY` | vazio | Chave da API do Claude (sugestões de corte). |
| `CLAUDE_MODEL` | `claude-sonnet-5` | Modelo do Claude. |
| `WHISPER_MODEL` | `small` | `base` (rápido), `small`, `medium` (mais preciso, mais lento), `large-v3` (com GPU). |
| `WHISPER_DEVICE` | `cpu` | `cpu`, `cuda` ou `auto`. |
| `LANGUAGE` | `pt` | `pt`, `en` ou `auto`. |
| `RENDER_SPEED` | `rapido` | `rapido` (~2,3x mais rápido) ou `qualidade`. |
| `CLIP_MIN_SECONDS` / `CLIP_MAX_SECONDS` | `15` / `90` | Faixa de duração dos cortes sugeridos. |

## Onde ficam os arquivos

```
data/
├── clipforge.db              # projetos e cortes
├── presets/                  # estilos de legenda criados por você
├── logs/                     # clipforge.log + um log por render
└── projects/<projeto>/
    ├── source/               # link (ou cópia) do vídeo original
    ├── transcript.json
    ├── captions_edited.json  # suas correções de legenda
    ├── clips/                # cortes renderizados (.mp4, .srt, .ass)
    └── exports/              # legendas do vídeo inteiro
```

## Problemas comuns

| Mensagem ou sintoma | O que fazer |
|---|---|
| "ffmpeg não foi encontrado" | Instale o FFmpeg (passo 1) e rode `python -m app.doctor`. |
| "ANTHROPIC_API_KEY não está definida" | Cole a chave no `.env` e reinicie o ClipForge (Encerrar e abrir de novo). Os cortes manuais funcionam sem ela. |
| "Chave da API inválida" | Confira se a chave foi copiada inteira, começando com `sk-ant-`. |
| Transcrição com palavras erradas | Corrija na aba Legendas, ou use `WHISPER_MODEL=medium` no `.env` e transcreva de novo (apague `transcript.json` do projeto). |
| "O vídeo não tem áudio" | O ClipForge precisa da fala para transcrever. |
| "Disco cheio" | Use **Apagar vídeos gerados** ou exclua projetos antigos. |
| Render lento | No modo Rápido, um corte leva mais ou menos a própria duração para renderizar no processador. A resolução 720x1280 é ainda mais rápida. |
| "Seguir quem fala" fica na pessoa errada | Funciona melhor com rostos de frente e bem iluminados. Se a pessoa que escuta ri ou fala junto, o recorte pode trocar. Nesse caso use **center** ou divida o corte. |
| A página não abre | O servidor pode ter sido encerrado. Abra pelo atalho de novo. |
| Outro erro | Veja `data/logs/clipforge.log`. Ele nunca contém a sua chave da API. |

## Desenvolvimento

```bash
python -m pytest -q     # testes (não chamam a API do Claude nem o Whisper)
```

As decisões técnicas estão em `DECISIONS.md` e o estado do projeto em `PROGRESS.md`.

## Licenças de terceiros

- Fonte Montserrat: SIL Open Font License (`fonts/OFL.txt`).
- Modelo de detecção de rosto YuNet (OpenCV Zoo): MIT (`models/YUNET_LICENSE`).
