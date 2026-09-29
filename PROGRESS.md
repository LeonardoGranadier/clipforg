# Progresso

**Fase atual:** 4 (Interface web) concluída, aguardando validação.

## Funciona
- Fase 0: `python -m app.doctor`.
- `python cli.py video.mp4`: probe → transcrição (faster-whisper, cache) → sugestões do Claude (validação + novas tentativas) → corte 16:9 com libx264 e barra de progresso.
- Cortes manuais com `--clip INICIO-FIM`, ajustados às palavras.
- Se a API falhar, a CLI avisa e segue (cortes manuais continuam funcionando).
- Erros tratados: FFmpeg ausente, arquivo inexistente/corrompido, vídeo sem áudio, sem fala, chave ausente ou inválida, modelo inexistente, limite da API, sem internet, JSON inválido, disco cheio.
- Legendas: 4 estilos (`classic`, `karaoke`, `pop`, `boxed`) em `app/caption_presets/`, `.ass` + `.srt` por corte, `--captions` grava no vídeo, `--captions-only` exporta o vídeo inteiro. Correções de texto via `captions_edited.json`.
- Vertical 1080x1920 a 30 fps: `--vertical fit|center|blur`. Vídeos verticais não são reenquadrados (só `fit`). Corte + reenquadramento + legenda numa única passada do FFmpeg.
- Interface web (`python -m app` ou atalho no menu): projetos com upload e progresso; processamento automático (transcrição + IA) com porcentagem e cancelar; editor de cortes (player, linha do tempo, ±0,1 s, encaixar nas palavras, marcar início/fim, criar corte pela seleção da transcrição, duplicar, reordenar, excluir com desfazer, salvamento automático); editor de legendas (estilos com prévia sobre o vídeo, correção de palavras); exportação (formato, estilo, resolução 1080p/720p, render de um ou de todos com progresso e cancelar, downloads MP4/SRT/ASS, só legendas).
- SQLite (`data/clipforge.db`) para projetos e cortes; projetos antigos (clips.json) são importados automaticamente.
- Log por corte em `data/logs/<projeto>-<corte>.log`.
- 73 testes (`python -m pytest -q`): ajuste às palavras, validação do JSON e novas tentativas (cliente simulado), pós-processamento, divisão em blocos, correção de tempos, agrupamento de legendas, `.ass`/`.srt`, escape, filtros verticais, integração com FFmpeg (3 modos em vídeo horizontal, vídeo vertical 3:4), API web (upload, cortes, render, Range, cancelamento, legendas, segurança de caminhos) (incluindo legenda gravada numa pasta com caracteres especiais).

## Teste real (2026-09-29)
Vídeo vertical de 1 min, fala em português, gritada/cômica:
- Transcrição (`small`, CPU): 50 s para 60 s de vídeo. 145 palavras, 39 com baixa confiança (algumas palavras erradas).
- Claude (`claude-sonnet-5`): 1 corte de 54,9 s, score 78, com título e motivo coerentes. Structured outputs funcionou de primeira.
- Corte: nenhuma palavra cortada no meio.
- Legenda karaokê: palavra destacada = palavra falada nos 5 pontos conferidos.
- Fase 3: o mesmo vídeo com `--vertical` + pop saiu em 1080x1920/30 fps, com a legenda sincronizada.
- Fase 4, testada no navegador (Chrome): editor de cortes (todas as ações), correção de legendas, render pela interface com progresso/cancelar/downloads, fluxo completo upload → transcrição → IA.
- Ainda falta testar com um vídeo de ~10 min (aceite da Fase 1: de 3 a 8 cortes).

## Falta
- Fases 5 e 6.

## Problemas conhecidos
- A prévia da legenda sobre o vídeo e o destaque da palavra atual não puderam ser conferidos no teste automático, porque a janela de automação do Chrome estava oculta e não carregava vídeo. Precisam ser vistos pelo usuário.
- Tarefas (fila) ficam só na memória: se o servidor for fechado no meio de um render, o corte volta para "pendente" e é preciso renderizar de novo.
- Não há botão para encerrar o servidor nem para apagar projetos (Fase 6).
- Render vertical lento no processador: ~2x a duração do corte (55 s → 117 s) com `libx264 -preset medium`.
- Nos estilos classic/karaoke/boxed, uma palavra única com mais de ~27 letras pode encostar nas bordas (o pop já reduz palavras longas).
- Em fala gritada/rápida, o Whisper `small` erra várias palavras. Opções: `--whisper-model medium` (mais lento) ou corrigir pelo `captions_edited.json` (editor na Fase 4).
- A transcrição roda no processador (a GPU 940MX não ajuda). Veja `DECISIONS.md`.
