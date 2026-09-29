# Progresso

**Fase atual:** 2 (Legendas) concluída, aguardando validação.

## Funciona
- Fase 0: `python -m app.doctor`.
- `python cli.py video.mp4`: probe → transcrição (faster-whisper, cache) → sugestões do Claude (validação + novas tentativas) → corte 16:9 com libx264 e barra de progresso.
- Cortes manuais com `--clip INICIO-FIM`, ajustados às palavras.
- Se a API falhar, a CLI avisa e segue (cortes manuais continuam funcionando).
- Erros tratados: FFmpeg ausente, arquivo inexistente/corrompido, vídeo sem áudio, sem fala, chave ausente ou inválida, modelo inexistente, limite da API, sem internet, JSON inválido, disco cheio.
- Legendas: 4 estilos (`classic`, `karaoke`, `pop`, `boxed`) em `app/caption_presets/`, `.ass` + `.srt` por corte, `--captions` grava no vídeo, `--captions-only` exporta o vídeo inteiro. Correções de texto via `captions_edited.json`.
- Log por corte em `data/logs/<projeto>-<corte>.log`.
- 51 testes (`python -m pytest -q`): ajuste às palavras, validação do JSON e novas tentativas (cliente simulado), pós-processamento, divisão em blocos, correção de tempos, agrupamento de legendas, `.ass`/`.srt`, escape, integração com FFmpeg (incluindo legenda gravada numa pasta com caracteres especiais).

## Teste real (2026-09-29)
Vídeo vertical de 1 min, fala em português, gritada/cômica:
- Transcrição (`small`, CPU): 50 s para 60 s de vídeo. 145 palavras, 39 com baixa confiança (algumas palavras erradas).
- Claude (`claude-sonnet-5`): 1 corte de 54,9 s, score 78, com título e motivo coerentes. Structured outputs funcionou de primeira.
- Corte: nenhuma palavra cortada no meio.
- Legenda karaokê: palavra destacada = palavra falada nos 5 pontos conferidos.
- Ainda falta testar com um vídeo de ~10 min (aceite da Fase 1: de 3 a 8 cortes).

## Falta
- Fases 3 a 6.

## Problemas conhecidos
- Em fala gritada/rápida, o Whisper `small` erra várias palavras. Opções: `--whisper-model medium` (mais lento) ou corrigir pelo `captions_edited.json` (editor na Fase 4).
- A transcrição roda no processador (a GPU 940MX não ajuda). Veja `DECISIONS.md`.
