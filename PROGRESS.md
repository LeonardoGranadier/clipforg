# Progresso

**Fase atual:** 6 (Acabamento) concluída. **Todas as fases (0 a 6) estão prontas**, aguardando a validação final.

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
- **94 testes automatizados** (`python -m pytest -q`), sem chamar a API nem o Whisper.

## Testes reais
- Vídeo vertical de 1 min (fala gritada): transcrição, sugestão da IA, corte sem palavra cortada, legenda karaokê/pop sincronizada, saída 1080x1920.
- Vídeo de 12 min 23 s (enviado pelo usuário pela interface): transcrito e 7 cortes sugeridos (de 28 s a 71 s, notas de 65 a 85), 0 palavras cortadas no meio. **Critério de aceite da Fase 1 cumprido.**
- Face-follow com vídeos montados (pessoa deslizando ou parada): o recorte acompanha, e o rosto nunca chega perto da borda. Parado, o recorte se mexe em só 5% dos quadros.
- Interface (Chrome): todas as ações do editor de cortes, correção de legendas, render com fila/cancelar/downloads, upload → transcrição → IA, estilos próprios, apagar vídeos gerados, excluir projeto, fila global, Encerrar, atalho do menu.

## Ainda não verificado
- A prévia da legenda sobre o vídeo e o destaque da palavra atual no player: a janela de automação do Chrome estava oculta e não carregava vídeo.
- Face-follow numa entrevista horizontal real.

## Problemas conhecidos
- O render no processador é lento: ~2x a duração do corte em 1080x1920 (`libx264 -preset medium`).
- Em fala gritada/rápida, o Whisper `small` erra palavras. Opções: `WHISPER_MODEL=medium` ou corrigir na aba Legendas.
- A fila fica na memória: fechar o servidor no meio de um render faz o corte voltar para "pendente".
- Face-follow com várias pessoas segue o maior rosto, não quem fala.
- Nos estilos não-pop, uma palavra única com mais de ~27 letras pode encostar nas bordas.
