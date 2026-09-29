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

## 2026-09-29: Fase 3

- **fit:** `scale=1080:1920:force_original_aspect_ratio=decrease` + `pad`, em vez de `scale=1080:-2`. Assim também funciona com vídeos mais "altos" que 9:16, que passariam de 1920 de altura.
- **center:** o recorte usa `min(iw, ih*9/16)` para não falhar em vídeos mais estreitos que 9:16.
- **blur:** o fundo é desfocado em 1/4 da resolução (270x480) e depois ampliado, o que é bem mais rápido que desfocar em 1080x1920. Ele também fica 8% mais escuro para destacar o vídeo da frente.
- **Vídeo já vertical (altura > largura):** sempre `fit`, sem recortar. A CLI avisa se outro modo foi pedido.
- **Saída sempre com `fps=30,setsar=1`**, e o reenquadramento vem antes da legenda no mesmo filtro. O `.ass` usa `PlayRes` 1080x1920.
- **Pop:** palavras com mais de 16 letras são reduzidas proporcionalmente para caber na largura.
- **Cache do render:** um corte é gerado de novo se o estilo **ou** o modo vertical mudar.

## 2026-09-29: Fase 4

- **SQLite (`data/clipforge.db`) para projetos e cortes.** O servidor e as tarefas de render escrevem ao mesmo tempo, e o SQLite evita as condições de corrida que um JSON teria. Uso uma conexão por operação, em modo WAL. A transcrição continua em JSON. A CLI usa o mesmo banco.
- **Fila com um único trabalhador**, em memória. Transcrição e render já ocupam todo o processador, então rodar dois ao mesmo tempo não adiantaria. O cancelamento usa `threading.Event`: o FFmpeg é encerrado, e a transcrição para no próximo segmento.
- **Rota de render `/api/projects/{pid}/clips/{cid}/render`**, e não `/api/clips/{id}/render`, porque o id do corte (`c01`, `m01`) só é único dentro do projeto. Também incluí `/render-all`, `/snap`, `/process`, `/captions/cues`, `/captions/export` e `/jobs/{id}/cancel`.
- **Salvar cortes = PUT da lista inteira.** Mudar só o título mantém o vídeo já gerado. Mudar início ou fim marca o corte como "pending".
- **A prévia da legenda é aproximada (CSS)**, mas usa os mesmos blocos de legenda do backend (`/captions/cues`, calculados por `group_words`), para não duplicar a lógica em JS.
- **Nenhuma janela de diálogo (alert/confirm):** excluir tem "Desfazer", e substituir as sugestões da IA pede um segundo clique de confirmação.
- **Resolução na exportação:** 1080x1920 ou 720x1280.
- **Vídeo vertical:** o corte registra o modo realmente aplicado (`fit`), e não o pedido.
- **Sem terminal:** `scripts/install-launcher.sh` cria um atalho no menu de aplicativos, que chama `scripts/start.sh` (abre o navegador e sobe o servidor se ele ainda não estiver rodando).
- **Pasta de dados configurável** (`CLIPFORGE_DATA`), para os testes nunca tocarem em `data/`.

## 2026-09-29: Fase 5

- **Detector YuNet (OpenCV `FaceDetectorYN`)**, com o modelo de 230 KB em `models/` (licença MIT, do opencv_zoo), em vez do MediaPipe. O OpenCV já estava instalado e não precisou de nova dependência. O YuNet é rápido e robusto a ângulos.
- **Amostragem pelo FFmpeg** (`fps=4,scale=640`, BGR bruto por pipe para o numpy). Isso respeita a rotação do vídeo e é rápido: ~2,6 s para 20 s de vídeo.
- **Aplicação com `sendcmd` + `crop@face`** (o comando muda o `x` do crop), escolhida como a forma mais simples que funciona. Uma expressão gigante no `crop` ficaria ilegível, e mandar os quadros pelo OpenCV por pipe seria mais lento e mais complexo. As posições são interpoladas para 30 comandos por segundo, e só entra no arquivo o que muda.
- **Suavização:**
  - zona morta de 5% com histerese: o recorte começa a andar quando o rosto sai da zona e só para quando ele recentraliza;
  - só começa a andar se o rosto ficar fora por 2 amostras seguidas, para um falso positivo isolado não mover nada;
  - velocidade máxima de 35% da largura por segundo;
  - salto instantâneo se o rosto mudar mais de 25% e ficar lá (troca de câmera);
  - sem rosto: mantém a posição por 1 s e depois volta ao centro devagar.
- **Vários rostos:** segue o maior. Diarização (pyannote) só se o usuário pedir.
- **Progresso do render no modo face:** 25% para a análise e 75% para o render.

## 2026-09-29: Fase 6

- **Estilos do usuário em `data/presets/`**, separados dos de fábrica (`app/caption_presets/`, só leitura). Um nome igual a um estilo existente é recusado ("crie como um novo preset sem alterar os existentes"). Todos os campos são validados (cores `#RRGGBB`, tamanho 20–200, posição 2–80%, fonte só entre as incluídas).
- **A prévia do formulário de estilo** aplica na hora cores, tamanho e posição. Mudanças de tipo ou de palavras por legenda aparecem depois de salvar, porque o agrupamento vem do backend.
- **A fila continua em memória**, com um único trabalhador (Fase 4). Nesta fase ganhou a visão global no topo, o cancelamento de todas as tarefas ao encerrar e a limpeza de tarefas antigas (guarda as últimas 200).
- **Excluir projeto:** cancela as tarefas do projeto, apaga a pasta e as linhas do banco. A pasta `source/` tem só um hard link ou uma cópia, então o arquivo original do usuário nunca é apagado (há teste para isso).
- **Limpeza ao iniciar:** arquivos `.part.*`, `.face.txt`, `audio.tmp.wav`, uploads interrompidos e logs por corte com mais de 30 dias. O `clipforge.log` gira em 5 MB × 3.
- **Erros:**
  - de validação (422) em português, com o nome do campo;
  - erros inesperados viram "Erro inesperado (Tipo). Detalhes em data/logs/clipforge.log", com o traceback só no log;
  - problemas críticos do ambiente (FFmpeg, libass, fontes, modelo de rosto) aparecem num aviso na tela. As verificações lentas (NVENC/CUDA) ficam só no `doctor`.
- **Encerrar pela interface:** `POST /api/shutdown` (com `force=true` se houver tarefas, que são canceladas antes) envia SIGINT ao próprio processo. Assim o uvicorn encerra de forma limpa, e o atalho do menu sobe o servidor de novo quando necessário.
- **Confirmações em dois cliques** em todo lugar (excluir projeto, apagar vídeos, apagar estilo, encerrar), sem `alert`/`confirm`. A lista de projetos não se redesenha enquanto há uma confirmação pendente.
- **Não implementado (fica como sugestão):** trocar o `-preset` do libx264 para acelerar o render; diarização para seguir quem fala; fila persistente.

## 2026-09-29: Melhorias pós-Fase 6 (pedidas pelo usuário)

- **Render rápido como padrão (`RENDER_SPEED=rapido` → libx264 `veryfast`).** Medido num corte de 30 s em 1080x1920 blur: 19,6 s contra 44,8 s do `medium`, arquivo 9% menor, SSIM 0,995. `qualidade` mantém o `medium`. O `crf 20` continua igual nos dois.
- **"Seguir quem fala" sem pyannote.** A diarização diz *quando* cada voz fala, mas não *qual rosto* é de quem; para isso é preciso a imagem de qualquer jeito. Por isso usei a imagem: o YuNet já dá o nariz e os cantos da boca, e com eles o recorte da boca é alinhado ao rosto (compensa o movimento da cabeça) e normalizado. A atividade é a diferença média entre amostras seguidas da mesma pessoa (trilha). Isso não precisou de nova dependência (pyannote traria PyTorch, ~2 GB, e exige conta no Hugging Face).
- **Escolha de quem fala:**
  - só conta movimento de boca **durante a fala** (pelas palavras da transcrição, com ±0,15 s);
  - média móvel de 0,75 s;
  - troca só se a outra pessoa tiver 1,5x mais atividade por 0,5 s seguidos;
  - se o rosto de quem fala some por menos de 1 s (falha do detector), segura a pessoa;
  - a pessoa inicial é a que mais fala no primeiro 1,5 s (e não o maior rosto).
- **Análise a 8 amostras/s** (antes 4), porque a boca muda rápido. As constantes de suavização passaram a ser definidas em segundos, para não depender da taxa.
- **Na troca de pessoa, o recorte faz corte seco** (sem interpolar), como numa edição, em vez de deslizar pela tela.
