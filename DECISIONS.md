# Decisões

## 2026-09-29: Fase 0

- **Python 3.12 via `uv`**, isolado em `.venv`. O Python do sistema é 3.14, novo demais para garantir que `faster-whisper`/`ctranslate2` tenham versão pronta.
- **Whisper no processador (`cpu`, `int8`), modelo `small`.** A GPU (GeForce 940MX, Maxwell) só suporta `float32` no ctranslate2 e tem pouca memória; o ganho seria pequeno. Dá para mudar com `WHISPER_DEVICE` no `.env`.
- **Renderização com `libx264`.** O FFmpeg lista `h264_nvenc`, mas o teste real falha nesta placa. O `doctor` testa o NVENC de verdade, em vez de só verificar se ele aparece na lista.
- **Modelo do Claude: `claude-sonnet-5`** (em `CLAUDE_MODEL`).
- **Fonte das legendas: Montserrat ExtraBold/Bold** (licença OFL, em `fonts/`), versões estáticas em vez da variável, porque o libass lida mal com fontes variáveis.
- **Projeto em `corte/clipforge/`.** O `SKILL.md` está em `corte/.claude/skills/clipforge/`, e uma cópia fica na raiz do projeto.

## 2026-09-29: Fase 1

- **Sem SQLite por enquanto.** Na CLI, cada projeto é uma pasta `data/projects/<nome>-<hash>/` com `transcript.json` e `clips.json`. O banco entra na Fase 4, junto com a interface.
- **ID do projeto = nome do arquivo + impressão digital** (tamanho + primeiro e último MB). Assim o mesmo vídeo reabre o mesmo projeto e reaproveita a transcrição.
- **O vídeo de origem é ligado ao projeto por hard link** (sem cópia) quando está no mesmo disco. Caso contrário, é copiado.
- **Palavras no `transcript.json` usam o campo `word`**, como na seção "Transcrição" do SKILL.md.
- **A transcrição enviada ao Claude usa `[início → fim] texto`**, e não só o início, para ele saber onde cada frase termina.
- **Structured outputs** (`client.messages.parse(output_format=ClipSuggestions)`), com até 3 tentativas se a resposta vier inválida ou cortada.
- **A folga (0,15 s antes, 0,25 s depois) nunca invade a palavra vizinha.** Se a próxima palavra começa antes, a folga é menor. Isso garante que um corte não comece nem termine no meio de uma palavra.
- **Sugestões são reaproveitadas** entre execuções, para não gastar API de novo. O `--resuggest` força novas sugestões.
- **IDs:** `c01…` para sugestões da IA e `m01…` para cortes manuais. Um `--clip` repetido não duplica o corte.
- **Sem NVENC:** sempre `libx264` (veja a Fase 0).
- **Cortes manuais pela CLI (`--clip`)** foram incluídos já nesta fase para cumprir o requisito "usável sem a API do Claude" enquanto a interface não existe.

## 2026-09-29: Fase 2

- **Corte e legenda numa única passada do FFmpeg.** Com `-ss` antes do `-i`, os tempos do vídeo recomeçam em 0, e o `.ass` do corte já usa tempos relativos (`palavra.start - clip.start`).
- **O `PlayRes` do `.ass` é a resolução real de saída.** O tamanho da fonte escala pelo lado menor do vídeo (os presets foram pensados para 1080x1920). Assim a legenda fica legível tanto em 16:9 quanto em 9:16. Na Fase 3, a saída vertical usa exatamente 1080x1920.
- **Karaokê: um evento por palavra**, com a linha inteira visível e só a palavra falada destacada. Isso destaca exatamente a palavra atual (o `\kf` preencheria de forma progressiva e manteria as anteriores coloridas).
- **Pop: uma palavra por evento**, com animação de escala 80% → 112% → 100% em 160 ms.
- **Boxed:** `BorderStyle=3`, com `OutlineColour` e `BackColour` iguais, porque libass e VSFilter usam cores diferentes para a caixa.
- **Legenda contínua:** em pausas até 0,4 s, a legenda fica na tela até a próxima, para não piscar.
- **Escape do texto:** `{` e `}` viram `\{` e `\}`. Uma `\` recebe um caractere invisível (word joiner) logo depois, para `\N` e similares não virarem comandos. Testado renderizando de verdade.
- **Escape de caminho no filtro:** dois níveis (valor da opção e grafo de filtros), sem aspas. Testado com uma pasta chamada `pasta: d'água [x]`.
- **`.srt` em 2 linhas de até 42 caracteres**, quebrando no fim das frases ou em pausas maiores que 0,7 s.
- **Cache do render:** um corte só é gerado de novo se o estilo mudar (`Clip.style`, onde `null` = sem legenda).
- **`captions_edited.json` = `{"índice_da_palavra": "texto"}`**, com índice na lista de todas as palavras da transcrição. Texto vazio remove a palavra da legenda.
