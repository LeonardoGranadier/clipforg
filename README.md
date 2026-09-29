# ClipForge

Ferramenta local para transformar vídeos longos **seus** em cortes curtos verticais (9:16) com legendas animadas.

> Use apenas vídeos seus ou com autorização. O ClipForge só aceita arquivos do seu computador.

## Instalação

Você precisa de: **FFmpeg**, **uv** (gerenciador de Python) e, opcionalmente, uma **chave da API da Anthropic** (para as sugestões de corte por IA).

### 1. FFmpeg

| Sistema | Comando |
|---|---|
| Linux (Ubuntu/Debian) | `sudo apt install ffmpeg` |
| macOS | `brew install ffmpeg` (instale o [Homebrew](https://brew.sh) antes) |
| Windows | `winget install Gyan.FFmpeg` e depois feche e abra o terminal |

Confira com `ffmpeg -version`.

### 2. uv

- Linux/macOS: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

Feche e abra o terminal depois de instalar.

### 3. Projeto

Dentro da pasta `clipforge`:

```bash
uv python install 3.12
uv venv --python 3.12 .venv
uv pip install -r requirements.txt
cp .env.example .env        # no Windows: copy .env.example .env
```

Abra o `.env` num editor de texto e cole a sua chave em `ANTHROPIC_API_KEY=`.

### 4. Verificar

```bash
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m app.doctor
```

Os itens marcados como `FALTA` precisam ser corrigidos. Os marcados como `AVISO` não impedem o uso.

## Testes

```bash
python -m pytest -q
```

## Uso (linha de comando)

Com o ambiente ativado (`source .venv/bin/activate`):

```bash
python cli.py caminho/do/video.mp4
```

Esse comando faz quatro coisas: lê o vídeo, transcreve, pede sugestões de corte ao Claude e gera os cortes em `data/projects/<projeto>/clips/`.

Na segunda vez com o mesmo vídeo, a transcrição e as sugestões salvas são reaproveitadas, e os cortes já prontos não são gerados de novo.

| Opção | O que faz |
|---|---|
| `--clip 1:05-1:48` | Cria um corte manual (pode repetir). O início e o fim são ajustados às palavras. |
| `--no-ai` | Não chama o Claude. |
| `--resuggest` | Descarta as sugestões antigas e pede novas. |
| `--no-render` | Só transcreve e sugere, sem gerar os vídeos. |
| `--lang en` | Idioma do vídeo (`pt`, `en` ou `auto`). |
| `--whisper-model medium` | Troca o modelo de transcrição. |
| `--vertical blur` | Saída vertical 1080x1920: `fit` (barras pretas), `center` (recorte central) ou `blur` (fundo desfocado). Vídeos que já são verticais sempre usam `fit`. |
| `--captions` | Grava a legenda nos cortes e salva `.ass` e `.srt` ao lado de cada `.mp4`. |
| `--style karaoke` | Estilo da legenda: `classic`, `karaoke`, `pop` ou `boxed` (padrão: `classic`). |
| `--captions-only` | Só gera `.srt` e `.ass` do vídeo inteiro, em `exports/`, sem cortar e sem IA. |

### Legendas

Os estilos ficam em `app/caption_presets/*.json`. Neles dá para ajustar fonte, tamanho, cores, contorno, posição (fração da altura, a partir da base), maiúsculas e palavras por linha.

Para corrigir palavras da transcrição (nomes, gírias), crie `data/projects/<projeto>/captions_edited.json` no formato `{"<número da palavra>": "texto corrigido"}`. Texto vazio remove a palavra. Na Fase 4 isso será feito pela interface.

Os logs ficam em `data/logs/`.
