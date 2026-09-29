# Progresso

**Fase atual:** todas as fases (0 a 6) concluídas, mais as quatro melhorias pedidas depois (render rápido, seguir quem fala, transcrever de novo, fila persistente). Aguardando validação.

## Funciona
- **Fase 0:** `python -m app.doctor` checa FFmpeg, libass, GPU, chave da API, fontes e o modelo de rosto.
- **Fase 1:** CLI `python cli.py video.mp4`: transcrição (faster-whisper, cache), sugestões do Claude (structured outputs, novas tentativas), cortes ajustados às palavras, cortes manuais `--clip`.
- **Fase 2:** legendas `.ass`/`.srt` em 4 estilos (classic, karaoke, pop, boxed), gravadas no vídeo, e modo só legendas.
- **Fase 3:** vertical 1080x1920/30 fps: `fit`, `center`, `blur`.
- **Fase 4:** interface web: upload, processamento automático, editor de cortes, editor de legendas com prévia, exportação 1080p/720p com fila, cancelar e downloads.
- **Fase 5:** modo `face` (segue o rosto, YuNet + `sendcmd`/`crop`), estável e sem tremor.
- **Fase 6:**
  - estilos de legenda próprios (criar pelo formulário, validados, salvos em `data/presets/`, sem alterar os de fábrica; apagar);
  - fila global no topo com progresso e cancelar;
  - excluir projeto e apagar vídeos gerados (o vídeo original nunca é tocado), com o tamanho em disco;
  - limpeza automática de sobras (arquivos parciais, uploads interrompidos, logs com mais de 30 dias) e rotação do `clipforge.log`;
  - mensagens de erro em português, inclusive as de validação e as de erros inesperados;
  - aviso na tela se faltar FFmpeg, libass, fontes ou o modelo de rosto;
  - botão Encerrar;
  - README final.
- **Melhorias pós-Fase 6:**
  - **Render rápido** (padrão): libx264 `veryfast`, 2,3x mais rápido (30 s de corte: 44,8 s → 19,6 s), arquivo menor, SSIM 0,995. Opção "Qualidade máxima" (`medium`) na interface, na CLI (`--speed`) e no `.env` (`RENDER_SPEED`).
  - **Seguir quem fala:** com várias pessoas, o modo `face` escolhe quem mexe a boca durante a fala (pela transcrição). A troca tem histerese, segura a pessoa durante falhas curtas do detector e faz corte seco na troca.
  - **Transcrever de novo:** escolhe modelo e idioma (interface, API `force`, CLI `--retranscribe`). A troca é atômica (se falhar, a atual fica). As correções antigas vão para `captions_edited.anterior.json`, e o modelo usado fica registrado na transcrição.
  - **Fila persistente:** as tarefas ficam na tabela `jobs` do SQLite e voltam sozinhas ao reabrir (as que rodavam recomeçam do zero). No Linux, o FFmpeg morre junto se o ClipForge cair (`PR_SET_PDEATHSIG`), sem processos órfãos.
- **104 testes automatizados** (`python -m pytest -q`), sem chamar a API nem o Whisper.

## Testes reais
- Vídeo vertical de 1 min (fala gritada): transcrição, sugestão da IA, corte sem palavra cortada, legenda karaokê/pop sincronizada, saída 1080x1920.
- Vídeo de 12 min 23 s (enviado pelo usuário pela interface): transcrito e 7 cortes sugeridos (de 28 s a 71 s, notas de 65 a 85), 0 palavras cortadas no meio. **Critério de aceite da Fase 1 cumprido.**
- Face-follow com vídeos montados (pessoa deslizando ou parada): o recorte acompanha, e o rosto nunca chega perto da borda. Parado, o recorte se mexe em só 5% dos quadros.
- Fila persistente, testada de verdade: render de 35 s na fila, `kill -9` no servidor duas vezes seguidas → ao reabrir, as 2 tarefas voltaram e terminaram, com arquivos completos e nenhum FFmpeg órfão.
- Transcrever de novo, com fala real (20 s): `small` → `base`, modelo registrado, correções guardadas em backup. Com falha (vídeo sem fala), a transcrição e as correções antigas ficaram intactas (teste automático).
- Seguir quem fala, com um vídeo montado (duas pessoas lado a lado, fala troca de lado aos 10 s): 1 única troca, aos 10,9 s; 152/160 amostras na pessoa certa (as 8 erradas são o 0,9 s de confirmação) e nenhuma troca falsa.
- Interface (Chrome): todas as ações do editor de cortes, correção de legendas, render com fila/cancelar/downloads, upload → transcrição → IA, estilos próprios, apagar vídeos gerados, excluir projeto, fila global, Encerrar, atalho do menu.

## Ainda não verificado
- A prévia da legenda sobre o vídeo e o destaque da palavra atual no player: a janela de automação do Chrome estava oculta e não carregava vídeo.
- Face-follow e "seguir quem fala" numa entrevista horizontal real, com duas câmeras ou duas pessoas no mesmo quadro.

## Problemas conhecidos
- Render no processador: no modo rápido, mais ou menos a duração do corte em 1080x1920.
- Em fala gritada/rápida, o Whisper `small` erra palavras. Opções: `WHISPER_MODEL=medium` ou corrigir na aba Legendas.
- "Seguir quem fala" usa só o movimento da boca (sem identificar vozes): se quem escuta ri ou fala junto, o recorte pode trocar. Rostos de perfil ou muito pequenos (<~40 px na análise) são menos confiáveis.
- Nos estilos não-pop, uma palavra única com mais de ~27 letras pode encostar nas bordas.
