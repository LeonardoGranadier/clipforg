---
name: clipforge
description: Constrói e evolui o ClipForge, um sistema próprio para transformar vídeos longos em cortes curtos verticais (9:16) com legendas animadas, usando faster-whisper, FFmpeg, OpenCV/MediaPipe e a API do Claude para escolher os melhores trechos. Use SEMPRE que o usuário mencionar cortes de vídeo, clips, Shorts, Reels, TikTok, legendas automáticas, legendas animadas, transcrição com Whisper, reenquadramento vertical, ferramenta tipo OpusClip/Vizard, ou pedir para continuar/corrigir/melhorar o projeto ClipForge, mesmo que não use o nome do projeto.
---

# ClipForge: sistema de cortes e legendas

Sistema local (com interface web) que recebe um vídeo longo do próprio usuário, transcreve, sugere os melhores cortes com IA, permite ajustar tudo manualmente e exporta clips verticais com legendas queimadas no vídeo (e arquivos .srt/.ass separados).

## Regras de trabalho (leia antes de qualquer coisa)

1. **Trabalhe em fases.** Ao final de cada fase, rode os testes, mostre como executar e **pare para o usuário validar** antes de seguir. Nunca construa tudo de uma vez.
2. **Pergunte antes de assumir** apenas se algo bloquear a fase atual (SO, GPU, chave de API). Caso contrário, tome a decisão mais simples, registre em `DECISIONS.md` e siga.
3. **Mantenha `PROGRESS.md` atualizado:** fase atual, o que funciona, o que falta, problemas conhecidos.
4. **O usuário pode ser iniciante.** Explique comandos de terminal em linguagem simples e entregue instruções de instalação passo a passo.
5. **Direitos autorais:** o sistema só aceita **upload de arquivo local**. Não implemente download de vídeos do YouTube ou de outras plataformas de terceiros. Coloque um aviso na interface: "Use apenas vídeos seus ou com autorização."
6. **Segredos:** a chave `ANTHROPIC_API_KEY` vem de `.env`. Nunca escreva chaves no código nem em logs.
7. **Sem funcionalidades extras** além do pedido. Sugira melhorias no final, não implemente por conta própria.

## Stack

| Parte | Escolha |
|---|---|
| Linguagem | Python 3.11+ |
| Backend | FastAPI + Uvicorn |
| Tarefas longas | Thread pool/`BackgroundTasks` na v1 (sem Redis). Fila robusta só se o usuário pedir |
| Transcrição | `faster-whisper` (timestamps por palavra, `vad_filter=True`) |
| IA de cortes | SDK `anthropic`, saída em JSON validada com `pydantic` |
| Vídeo | FFmpeg/FFprobe via `subprocess` (nunca `shell=True`) |
| Rosto | MediaPipe ou OpenCV (fase 5) |
| Legendas | Arquivo `.ass` gerado pelo código, gravado com o filtro `ass` do FFmpeg |
| Frontend | HTML + CSS + JavaScript puro servido pelo FastAPI (sem build step) |
| Banco | SQLite (via `sqlite3` ou SQLModel) para projetos e cortes |

Modelos: pergunte ao usuário qual `model` do Claude usar e deixe em `.env` (`CLAUDE_MODEL`). Modelo Whisper padrão configurável (`WHISPER_MODEL=small`; usar `large-v3` se houver GPU).

## Estrutura de pastas

```
clipforge/
├── README.md              # instalação e uso, para iniciantes
├── PROGRESS.md
├── DECISIONS.md
├── .env.example
├── requirements.txt
├── app/
│   ├── main.py            # FastAPI, rotas
│   ├── config.py          # lê .env
│   ├── db.py
│   ├── models.py          # pydantic: Word, Segment, ClipSuggestion, Project, Clip
│   ├── pipeline/
│   │   ├── probe.py       # ffprobe: duração, resolução, fps, áudio
│   │   ├── transcribe.py
│   │   ├── suggest.py     # LLM: escolha dos cortes
│   │   ├── cut.py         # FFmpeg: corte
│   │   ├── reframe.py     # vertical: center, blur, face-follow
│   │   ├── captions.py    # gera .ass e .srt
│   │   └── render.py      # orquestra corte + reframe + legenda
│   └── static/            # index.html, app.js, style.css
├── cli.py                 # versão linha de comando de todo o pipeline
├── data/                  # projects/<id>/{source, transcript.json, clips/, exports/}
├── fonts/                 # fontes usadas nas legendas
└── tests/
```

## Fases

### Fase 0: Setup
- Verificar/instalar FFmpeg (`ffmpeg -version`), Python e dependências. Criar `README.md` com instalação para Windows, macOS e Linux.
- `requirements.txt`, `.env.example`, estrutura de pastas, script `python -m app.doctor` que checa FFmpeg, GPU (CUDA), chave da API e fontes.
- **Aceite:** `doctor` roda e mostra tudo OK ou diz exatamente o que falta.

### Fase 1: CLI de ponta a ponta (sem interface)
`python cli.py video.mp4` executa: probe → transcrição → sugestão de cortes → render dos cortes 16:9 sem legenda.
- Salva `transcript.json` e `clips.json` em `data/projects/<id>/`.
- Mostra progresso no terminal.
- **Aceite:** um vídeo de 10 minutos gera de 3 a 8 cortes cujo início e fim não cortam palavras no meio.

### Fase 2: Legendas
- Gerar `.ass` com legendas por palavra (ver "Legendas" abaixo), exportar também `.srt`.
- Flag `--captions` grava as legendas no vídeo; `--captions-only` exporta só o `.srt`/`.ass` do vídeo inteiro sem cortar.
- **Aceite:** legenda sincronizada (erro visível menor que ~100 ms) e legível no celular.

### Fase 3: Formato vertical simples
- Modos `center` (corte central), `blur` (vídeo original no centro, fundo desfocado) e `fit` (barras pretas).
- Saída 1080x1920, 30 fps, H.264 + AAC.
- **Aceite:** os três modos funcionam em vídeos horizontais e verticais.

### Fase 4: Interface web
Telas:
1. **Projetos:** lista, criar novo (upload com barra de progresso).
2. **Processamento:** status por etapa (transcrevendo, analisando, pronto) com porcentagem.
3. **Editor de cortes:**
   - Player do vídeo original com linha do tempo.
   - Lista de cortes sugeridos com título, nota (0 a 100) e motivo.
   - Ajuste de início/fim por campo numérico **e** botões de -0,1 s / +0,1 s, com "encaixar na palavra mais próxima".
   - Criar corte manual (marcar início e fim no player), duplicar, excluir, reordenar.
   - Transcrição clicável: clicar numa palavra leva o player até ela; selecionar um trecho cria um corte.
4. **Editor de legendas:** editar o texto de cada palavra/linha (corrigir nomes e gírias), escolher estilo (ver abaixo), prévia ao vivo sobre o vídeo.
5. **Exportar:** escolher modo vertical, estilo, resolução; renderizar um corte ou todos; baixar `.mp4`, `.srt`, `.ass`.
- **Aceite:** o usuário faz o fluxo completo (upload → ajustar → exportar) sem usar o terminal.

### Fase 5: Rosto seguindo o falante
- Detectar rosto a cada ~0,25 s, suavizar o centro com média móvel/filtro exponencial, limitar velocidade do movimento, manter o recorte dentro do quadro.
- Sem rosto detectado: manter última posição por 1 s, depois voltar ao centro.
- Vários rostos: v1 segue o maior; diarização (pyannote) só se o usuário pedir.
- **Aceite:** recorte estável, sem tremor, num vídeo de entrevista com 1 pessoa.

### Fase 6: Acabamento
Presets de estilo salvos, fila de renderização com cancelamento, limpeza de projetos antigos, mensagens de erro claras, testes automatizados, README final.

## Transcrição

```python
from faster_whisper import WhisperModel
model = WhisperModel(model_name, device=device, compute_type=compute_type)
segments, info = model.transcribe(
    audio_path, language=lang, word_timestamps=True,
    vad_filter=True, beam_size=5,
)
```
- `device="cuda"` com `compute_type="float16"` se houver GPU, senão `"cpu"` com `"int8"`.
- Extrair áudio antes: `ffmpeg -i in.mp4 -vn -ac 1 -ar 16000 audio.wav`.
- Idioma padrão `pt` (configurável, com opção de detecção automática).
- Salvar em `transcript.json`: `{language, duration, segments:[{id,start,end,text,words:[{word,start,end,prob}]}]}`.
- Cachear: se o `transcript.json` já existe, não transcrever de novo.
- Corrigir palavras com `end <= start` ou timestamps ausentes interpolando entre vizinhas.

## Escolha dos cortes com IA

- Montar a transcrição como linhas `[mm:ss.s] texto`, por segmento.
- Vídeos longos: dividir em blocos de ~10 a 15 min com sobreposição de 30 s, pedir sugestões por bloco, unir, remover sobreposições e ficar com os melhores.
- Pedir ao Claude **apenas JSON** neste formato, validar com pydantic e tentar de novo (até 2 vezes) se inválido:

```json
{"clips":[{"start":125.4,"end":171.2,"title":"...","hook":"frase de abertura","reason":"por que funciona","score":87}]}
```

Instruções de sistema para a chamada (ajuste e itere nelas):
- Duração alvo configurável (padrão 30 a 60 s; mínimo 15, máximo 90).
- Gancho forte nos primeiros 3 segundos: pergunta, afirmação polêmica, número, promessa ou história.
- Ideia **completa** com começo, meio e fim natural. Não terminar no meio de raciocínio.
- Começar e terminar em fronteira de frase.
- Não repetir o mesmo assunto em dois cortes.
- Score honesto de 0 a 100 com justificativa curta.
- Idioma dos títulos igual ao do vídeo.

Pós-processamento obrigatório: ajustar `start`/`end` para a palavra mais próxima, adicionar folga (padrão 0,15 s antes e 0,25 s depois, sem passar dos limites do vídeo), descartar cortes fora da faixa de duração e ordenar por score.

## Corte com FFmpeg

Para precisão de quadro, recodifique (não use `-c copy`):

```bash
ffmpeg -y -ss {start} -to {end} -i input.mp4 \
  -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p \
  -c:a aac -b:a 160k -movflags +faststart clip.mp4
```
- `-ss` **antes** do `-i` para rapidez; com recodificação continua preciso.
- Usar aceleração de GPU (`h264_nvenc`, `h264_videotoolbox`) só como opção detectada no `doctor`, com fallback para `libx264`.
- Sempre verificar o código de saída e guardar o `stderr` em log por corte.

## Vertical (9:16, 1080x1920)

- **fit:** `scale=1080:-2` + `pad=1080:1920:(ow-iw)/2:(oh-ih)/2`.
- **center:** `crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920`.
- **blur:** fundo = vídeo escalado para preencher e desfocado (`boxblur`), frente = vídeo escalado à largura 1080, sobreposto ao centro com `overlay`.
- **face-follow:** calcular `crop_x` por instante, suavizar, e aplicar com `crop` por expressão ou gerando o recorte por quadro via OpenCV e mandando para o FFmpeg por pipe. Escolha a abordagem mais simples que funcione e registre em `DECISIONS.md`.
- Se o vídeo já for vertical, não reenquadrar.

## Legendas

Gerar `.ass` (Advanced SubStation Alpha) com `PlayResX=1080`, `PlayResY=1920`.

**Agrupamento:** juntar palavras em linhas de 1 a 3 palavras (padrão 2), quebrando em pontuação ou pausa maior que 0,4 s. Máximo ~20 caracteres por linha e no máximo 2 linhas.

**Estilos (presets em JSON):**
1. `classic`: texto branco, contorno preto, sombra, embaixo do centro.
2. `karaoke`: linha inteira visível, palavra atual destacada (ex.: amarelo) usando tags `{\kf}` ou trocando a cor por palavra.
3. `pop`: uma palavra por vez, grande, com pequena animação de escala (`\t` com `\fscx`/`\fscy`).
4. `boxed`: texto sobre caixa semitransparente.

Parâmetros editáveis por preset: fonte, tamanho, cor principal, cor de destaque, contorno, posição vertical (padrão 25% acima da base para não cobrir a interface das redes), maiúsculas sim/não.

**Detalhes que costumam dar erro:**
- Tempos do `.ass` no formato `H:MM:SS.cc` (centésimos).
- Os tempos das legendas do corte devem ser **relativos ao início do corte** (`palavra.start - clip.start`).
- Escapar `{`, `}` e `\` no texto.
- No filtro do FFmpeg, escapar o caminho: no Windows trocar `\` por `/` e escapar `:` (`C\:/...`). Use `ass=filename='...':fontsdir='fonts'`.
- Incluir fonte com suporte a acentos do português (ex.: Montserrat ExtraBold, Poppins Bold, Inter Bold, todas livres) e apontar `fontsdir`.
- `.srt` exportado deve usar linhas mais longas (2 linhas, até ~42 caracteres) para leitura normal.

## Modelos de dados (resumo)

- `Word{text,start,end}`; `Segment{id,start,end,text,words}`.
- `Clip{id,project_id,start,end,title,hook,reason,score,style,vertical_mode,status,output_path}`.
- Edições de legenda feitas pelo usuário ficam em `captions_edited.json` (sobrescrevem o texto, mantendo os tempos).

## API (rotas mínimas)

- `POST /api/projects` (upload) · `GET /api/projects` · `GET /api/projects/{id}`
- `POST /api/projects/{id}/transcribe` · `POST /api/projects/{id}/suggest`
- `GET/PUT /api/projects/{id}/clips` · `POST /api/clips/{id}/render`
- `GET /api/projects/{id}/transcript` · `PUT /api/projects/{id}/captions`
- `GET /api/jobs/{id}` (status e progresso) · `GET /media/...` (vídeo com suporte a `Range` para o player)

## Qualidade e testes

- Testes com `pytest`: agrupamento de legendas, geração de `.ass`/`.srt`, ajuste às palavras, validação do JSON do LLM (com resposta simulada, sem gastar API).
- Teste de integração com um vídeo curto gerado pelo próprio FFmpeg (`testsrc` + `sine`), sem depender de arquivos externos.
- Tratar erros esperados: vídeo sem áudio, arquivo corrompido, chave da API ausente, FFmpeg não instalado, disco cheio, falha de JSON do LLM.
- Logs em `data/logs/`, sem dados sensíveis.
- Limpar arquivos temporários ao terminar cada render.

## Como reportar ao final de cada fase

1. O que foi feito (3 a 5 linhas).
2. Como rodar e testar (comandos exatos).
3. O que o usuário deve conferir.
4. Problemas conhecidos.
5. Pergunta: "Posso seguir para a Fase X?"
