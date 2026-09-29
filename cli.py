"""ClipForge pela linha de comando.

Exemplos:
    python cli.py meu_video.mp4
    python cli.py meu_video.mp4 --clip 1:05-1:48 --clip 300-345.5   # cortes manuais
    python cli.py meu_video.mp4 --resuggest                          # pede novas sugestões à IA
    python cli.py meu_video.mp4 --captions --style karaoke           # cortes com legenda gravada
    python cli.py meu_video.mp4 --vertical blur --captions            # 9:16 com fundo desfocado
    python cli.py meu_video.mp4 --captions-only                      # só .srt/.ass do vídeo inteiro
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from app.config import settings
from app.errors import ClipForgeError
from app.models import Clip, Transcript
from app.pipeline.captions import list_presets
from app.pipeline.probe import probe
from app.pipeline.reframe import MODES, is_vertical
from app.pipeline.render import export_full_captions, render_clip
from app.pipeline.snap import snap_range
from app.pipeline.suggest import fmt_time, suggest_clips
from app.pipeline.transcribe import transcribe
from app.projects import open_project


def setup_logging() -> None:
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=settings.logs_dir / "clipforge.log", level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # As bibliotecas HTTP registram URLs e cabeçalhos em DEBUG; mantemos fora do log.
    for noisy in ("httpx", "httpcore", "anthropic", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class Bar:
    """Barra de progresso simples no terminal."""

    def __init__(self, label: str) -> None:
        self.label, self.t0, self.last = label, time.monotonic(), -1

    def __call__(self, frac: float) -> None:
        pct = int(frac * 100)
        if pct == self.last:
            return
        self.last = pct
        filled = pct * 30 // 100
        elapsed = time.monotonic() - self.t0
        sys.stdout.write(f"\r  {self.label:<22} [{'#' * filled}{'.' * (30 - filled)}] {pct:3d}%  {elapsed:5.0f}s")
        sys.stdout.flush()
        if pct >= 100:
            sys.stdout.write("\n")


def parse_time(text: str) -> float:
    """Aceita '75.5', '1:15.5' ou '0:01:15.5'."""
    parts = text.strip().split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise argparse.ArgumentTypeError(f"tempo inválido: '{text}'")
    total = 0.0
    for n in nums:
        total = total * 60 + n
    return total


def parse_range(text: str) -> tuple[float, float]:
    if "-" not in text:
        raise argparse.ArgumentTypeError(f"use INICIO-FIM, por exemplo 1:05-1:48 (recebido: '{text}')")
    a, b = text.split("-", 1)
    start, end = parse_time(a), parse_time(b)
    if end <= start:
        raise argparse.ArgumentTypeError(f"o fim precisa ser depois do início: '{text}'")
    return start, end


def manual_clips(ranges: list[tuple[float, float]], transcript: Transcript, project_id: str,
                 existing: list[Clip]) -> list[Clip]:
    words = transcript.all_words()
    used = {c.id for c in existing}
    seen = {(c.start, c.end) for c in existing}
    out = []
    n = 1
    for a, b in ranges:
        start, end = snap_range(a, b, words, transcript.duration)
        if (start, end) in seen:  # mesmo corte de uma execução anterior
            continue
        seen.add((start, end))
        while f"m{n:02d}" in used:
            n += 1
        clip = Clip(id=f"m{n:02d}", project_id=project_id, start=start, end=end,
                    title=f"Corte manual {fmt_time(start)}")
        used.add(clip.id)
        out.append(clip)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="ClipForge: cortes curtos a partir de um vídeo longo seu.")
    p.add_argument("video", type=Path, help="arquivo de vídeo local")
    p.add_argument("--lang", default=None, help="idioma: pt, en ou auto (padrão: LANGUAGE do .env)")
    p.add_argument("--whisper-model", default=None, help="modelo Whisper (padrão: WHISPER_MODEL do .env)")
    p.add_argument("--clip", action="append", type=parse_range, default=[], metavar="INICIO-FIM",
                   help="corte manual; pode repetir. Ex.: --clip 1:05-1:48")
    p.add_argument("--no-ai", action="store_true", help="não pedir sugestões ao Claude")
    p.add_argument("--resuggest", action="store_true", help="descartar sugestões anteriores e pedir de novo")
    p.add_argument("--no-render", action="store_true", help="só transcrever e sugerir, sem gerar os vídeos")
    p.add_argument("--captions", action="store_true", help="gravar a legenda nos cortes (e salvar .ass/.srt)")
    p.add_argument("--captions-only", action="store_true",
                   help="só gerar .srt/.ass do vídeo inteiro, sem cortar e sem IA")
    p.add_argument("--vertical", choices=MODES, default=None,
                   help="saída vertical 1080x1920: fit (barras), center (recorte central) ou blur (fundo desfocado)")
    p.add_argument("--style", default="classic", choices=list_presets(), help="estilo da legenda (padrão: classic)")
    args = p.parse_args(argv)

    setup_logging()
    try:
        return run(args)
    except ClipForgeError as e:
        print(f"\nErro: {e}", file=sys.stderr)
        logging.error("%s", e)
        return 1
    except KeyboardInterrupt:
        print("\nCancelado.", file=sys.stderr)
        return 130


def run(args: argparse.Namespace) -> int:
    print("1/4  Lendo o vídeo...")
    info = probe(args.video)
    print(f"     {fmt_time(info.duration)} · {info.width}x{info.height} · {info.fps:.2f} fps")
    if not info.has_audio:
        raise ClipForgeError("O vídeo não tem áudio; não há o que transcrever.")

    project = open_project(args.video)
    print(f"     Projeto: {project.dir}")

    print("2/4  Transcrevendo" + (" (usando cache)" if project.transcript_path.exists() else "..."))
    transcript = transcribe(project.source, project.transcript_path, language=args.lang,
                            model_name=args.whisper_model, on_progress=Bar("transcrição"))
    n_words = len(transcript.all_words())
    print(f"     {len(transcript.segments)} frases, {n_words} palavras, idioma: {transcript.language}")

    if args.captions_only:
        ass_path, srt_path = export_full_captions(project, transcript, info, args.style)
        print(f"\nLegendas do vídeo inteiro:\n  {srt_path}\n  {ass_path}")
        return 0

    clips = [] if args.resuggest else project.load_clips()
    ai_clips = [c for c in clips if c.id.startswith("c")]
    print("3/4  Sugerindo cortes...")
    if args.no_ai:
        print("     IA desligada (--no-ai).")
    elif ai_clips:
        print(f"     Usando {len(ai_clips)} sugestões já salvas (use --resuggest para pedir de novo).")
    else:
        try:
            new = suggest_clips(transcript, project.id, on_progress=Bar("análise com IA"))
            clips = new + [c for c in clips if not c.id.startswith("c")]
            print(f"     {len(new)} cortes sugeridos.")
        except ClipForgeError as e:
            print(f"     Aviso: {e}")
            print("     Seguindo sem sugestões da IA.")

    if args.clip:
        clips += manual_clips(args.clip, transcript, project.id, clips)
    project.save_clips(clips)

    if not clips:
        print("\nNenhum corte para gerar. Use --clip INICIO-FIM para criar cortes manualmente.")
        return 0

    print()
    for c in clips:
        print(f"  {c.id}  [{fmt_time(c.start)} → {fmt_time(c.end)}] {c.duration:5.1f}s  "
              f"score {c.score:3d}  {c.title}")
        if c.reason:
            print(f"       {c.reason}")
    print()

    if args.no_render:
        print(f"Cortes salvos em {project.clips_path} (sem render, --no-render).")
        return 0

    style = args.style if args.captions else None
    vertical = args.vertical
    if vertical and is_vertical(info) and vertical != "fit":
        print(f"     O vídeo já é vertical: o modo '{vertical}' não se aplica, usando 'fit'.")
        vertical = "fit"
    todo = [c for c in clips if not (c.status == "done" and c.style == style and c.vertical_mode == vertical
                                     and c.output_path and Path(c.output_path).exists())]
    if not todo:
        print(f"4/4  Todos os cortes já foram gerados: {project.clips_dir}")
        return 0
    print(f"4/4  Gerando {len(todo)} corte(s)" + (f" ({len(clips) - len(todo)} já prontos)" if len(todo) < len(clips) else "") + "...")
    failures = 0
    for c in todo:
        c.status = "rendering"
        try:
            dst = render_clip(project, c, transcript, info, style=style, vertical=vertical,
                              on_progress=Bar(c.id))
            c.status, c.output_path, c.style, c.vertical_mode = "done", str(dst), style, vertical
        except ClipForgeError as e:
            print(f"\n     {c.id}: {e}")
            c.status = "error"
            failures += 1
        project.save_clips(clips)

    print(f"\nPronto: {len(todo) - failures} corte(s) gerado(s) em {project.clips_dir}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
