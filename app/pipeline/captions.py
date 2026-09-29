"""Legendas: agrupamento de palavras, presets de estilo e geração de .ass/.srt."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.errors import ClipForgeError
from app.models import Word

PRESETS_DIR = Path(__file__).resolve().parent.parent / "caption_presets"
REF_W, REF_H = 1080, 1920  # os presets são pensados para a tela vertical

SENTENCE_END = re.compile(r"[.!?…]+[\"')\]]*$")
ANY_PUNCT = re.compile(r"[.,!?;:…]+[\"')\]]*$")


# --- presets ----------------------------------------------------------------

class CaptionPreset(BaseModel):
    name: str
    kind: Literal["classic", "karaoke", "pop", "boxed"]
    font: str = "Montserrat ExtraBold"
    size: int = 80                     # em pixels, na tela 1080x1920
    primary_color: str = "#FFFFFF"
    highlight_color: str = "#FFD400"
    outline_color: str = "#000000"
    outline: float = 6
    shadow: float = 3
    box_color: str = "#000000"
    box_opacity: float = 0.6           # só no estilo boxed (0 transparente, 1 opaco)
    position: float = 0.25             # distância da base, em fração da altura
    uppercase: bool = True
    words_per_line: int = 2            # 1 a 3
    max_chars: int = 20


def list_presets() -> list[str]:
    return sorted(p.stem for p in PRESETS_DIR.glob("*.json"))


def load_preset(name: str) -> CaptionPreset:
    path = PRESETS_DIR / f"{name}.json"
    if not path.exists():
        raise ClipForgeError(f"Estilo de legenda '{name}' não existe. Opções: {', '.join(list_presets())}")
    return CaptionPreset.model_validate_json(path.read_text(encoding="utf-8"))


# --- edições do usuário -----------------------------------------------------

def load_edits(path: Path) -> dict[int, str]:
    """captions_edited.json: {"<índice da palavra>": "texto corrigido"}. Os tempos não mudam."""
    if not path.exists():
        return {}
    try:
        return {int(k): str(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
    except (ValueError, AttributeError) as e:
        raise ClipForgeError(f"Arquivo de edições de legenda inválido: {path}") from e


def apply_edits(words: list[Word], edits: dict[int, str]) -> list[Word]:
    """Troca o texto das palavras editadas; texto vazio remove a palavra da legenda."""
    out = []
    for i, w in enumerate(words):
        text = edits.get(i, w.word).strip()
        if text:
            out.append(w.model_copy(update={"word": text}))
    return out


# --- agrupamento ------------------------------------------------------------

@dataclass
class Cue:
    words: list[Word]
    start: float
    end: float

    @property
    def text(self) -> str:
        return " ".join(w.word for w in self.words)


def wrap(words: list[str], max_chars: int) -> list[str]:
    """Quebra gulosa em linhas de até `max_chars` (uma palavra maior que isso fica sozinha)."""
    lines: list[str] = []
    for w in words:
        if lines and len(lines[-1]) + 1 + len(w) <= max_chars:
            lines[-1] += " " + w
        else:
            lines.append(w)
    return lines


def group_words(
    words: list[Word],
    *,
    max_words: int = 2,
    max_chars: int = 20,
    max_lines: int = 2,
    pause: float = 0.4,
    break_on: re.Pattern[str] = ANY_PUNCT,
    linger: float = 0.3,
) -> list[Cue]:
    """Junta palavras em blocos de legenda.

    Quebra quando: atinge `max_words`, não cabe em `max_lines` linhas de `max_chars`,
    a palavra termina em pontuação ou há uma pausa maior que `pause` segundos.
    """
    groups: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if cur:
            too_many = len(cur) >= max_words
            too_long = len(wrap([x.word for x in cur + [w]], max_chars)) > max_lines
            gap = w.start - cur[-1].end > pause
            punct = bool(break_on.search(cur[-1].word))
            if too_many or too_long or gap or punct:
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)

    cues: list[Cue] = []
    for i, g in enumerate(groups):
        end = g[-1].end
        nxt = groups[i + 1][0].start if i + 1 < len(groups) else None
        # Mantém a legenda na tela até a próxima (em pausas curtas) para não piscar.
        if nxt is not None and nxt - end <= pause:
            end = nxt
        else:
            end = end + linger if nxt is None else min(end + linger, nxt)
        cues.append(Cue(words=g, start=g[0].start, end=end))
    return cues


def words_in_range(words: list[Word], start: float, end: float) -> list[Word]:
    """Palavras dentro de [start, end], com tempos relativos ao início do trecho."""
    out = []
    for w in words:
        if w.end <= start or w.start >= end:
            continue
        out.append(Word(word=w.word, start=round(max(0.0, w.start - start), 3),
                        end=round(min(end, w.end) - start, 3), prob=w.prob))
    return out


# --- .ass ------------------------------------------------------------------

def ass_time(t: float) -> str:
    cs = max(0, round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_color(hex_color: str, opacity: float = 1.0) -> str:
    """'#RRGGBB' -> '&HAABBGGRR' (no ASS, alfa 00 é opaco)."""
    h = hex_color.lstrip("#")
    if len(h) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        raise ClipForgeError(f"Cor inválida no preset de legenda: '{hex_color}' (use #RRGGBB)")
    alpha = round((1 - max(0.0, min(1.0, opacity))) * 255)
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def ass_escape(text: str) -> str:
    """Evita que o texto seja lido como comando do ASS ({...}, \\N, \\h etc.)."""
    return (text.replace("\\", "\\⁠")   # word joiner invisível quebra a sequência \x
                .replace("{", "\\{").replace("}", "\\}"))


def _display(w: Word, preset: CaptionPreset) -> str:
    return ass_escape(w.word.upper() if preset.uppercase else w.word)


POP_MAX_CHARS = 16  # acima disso a palavra do estilo pop é reduzida para caber na largura


def pop_tags(n_chars: int) -> str:
    """Animação de escala do pop (80% → 112% → 100%), reduzida em palavras longas."""
    base = 100 * min(1.0, POP_MAX_CHARS / max(1, n_chars))
    f = lambda k: round(base * k)  # noqa: E731
    return (f"{{\\fscx{f(0.8)}\\fscy{f(0.8)}\\t(0,90,\\fscx{f(1.12)}\\fscy{f(1.12)})"
            f"\\t(90,160,\\fscx{f(1)}\\fscy{f(1)})}}")


def build_ass(cues: list[Cue], preset: CaptionPreset, width: int = REF_W, height: int = REF_H) -> str:
    # Escala pelo lado menor: 1080 no vertical 1080x1920 (escala 1) e também no 1920x1080.
    scale = min(width, height) / REF_W
    size = round(preset.size * scale)
    outline = round(preset.outline * scale, 1)
    shadow = round(preset.shadow * scale, 1)
    margin_v = round(preset.position * height)
    margin_h = round(width * 0.06)

    primary = ass_color(preset.primary_color)
    highlight = ass_color(preset.highlight_color)
    if preset.kind == "boxed":
        # Caixa: BorderStyle 3. Libass e VSFilter usam cores diferentes para a caixa; definimos as duas.
        border_style = 3
        outline_c = back_c = ass_color(preset.box_color, preset.box_opacity)
        outline = max(outline, round(12 * scale, 1))  # espessura = margem interna da caixa
        shadow = 0
    else:
        border_style = 1
        outline_c = ass_color(preset.outline_color)
        back_c = ass_color("#000000", 0.5)

    header = "\n".join([
        "[Script Info]",
        "; Gerado pelo ClipForge",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{preset.font},{size},{primary},{highlight},{outline_c},{back_c},"
        f"0,0,0,0,100,100,0,0,{border_style},{outline},{shadow},2,{margin_h},{margin_h},{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ])

    events: list[str] = []

    def add(start: float, end: float, text: str) -> None:
        if end > start:
            events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{text}")

    for cue in cues:
        shown = [_display(w, preset) for w in cue.words]
        if preset.kind == "karaoke":
            # Um evento por palavra: a linha inteira, com a palavra falada destacada.
            for i, w in enumerate(cue.words):
                s = cue.start if i == 0 else w.start
                e = cue.words[i + 1].start if i + 1 < len(cue.words) else cue.end
                parts = [f"{{\\c{highlight}}}{t}{{\\c{primary}}}" if j == i else t for j, t in enumerate(shown)]
                add(s, e, _join_wrapped(parts, shown, preset.max_chars))
        elif preset.kind == "pop":
            for i, w in enumerate(cue.words):
                s = cue.start if i == 0 else w.start
                e = cue.words[i + 1].start if i + 1 < len(cue.words) else cue.end
                add(s, e, pop_tags(len(w.word)) + shown[i])
        else:  # classic, boxed
            add(cue.start, cue.end, "\\N".join(wrap(shown, preset.max_chars)))

    return header + "\n" + "\n".join(events) + "\n"


def _join_wrapped(parts: list[str], plain: list[str], max_chars: int) -> str:
    """Quebra as linhas pelo texto visível (`plain`), mas monta com as tags de cor (`parts`)."""
    lines = wrap(plain, max_chars)
    out, k = [], 0
    for line in lines:
        n = len(line.split(" "))
        out.append(" ".join(parts[k:k + n]))
        k += n
    return "\\N".join(out)


def cues_for_preset(words: list[Word], preset: CaptionPreset) -> list[Cue]:
    return group_words(words, max_words=1 if preset.kind == "pop" else max(1, min(3, preset.words_per_line)),
                       max_chars=preset.max_chars)


# --- .srt ------------------------------------------------------------------

def srt_time(t: float) -> str:
    ms = max(0, round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(words: list[Word], max_chars: int = 42) -> str:
    """Legenda para leitura normal: até 2 linhas de ~42 caracteres, quebrando no fim das frases."""
    cues = group_words(words, max_words=10_000, max_chars=max_chars, max_lines=2,
                       pause=0.7, break_on=SENTENCE_END, linger=0.5)
    blocks = []
    for n, c in enumerate(cues, 1):
        text = "\n".join(wrap([w.word for w in c.words], max_chars))
        blocks.append(f"{n}\n{srt_time(c.start)} --> {srt_time(c.end)}\n{text}\n")
    return "\n".join(blocks)


# --- arquivos ---------------------------------------------------------------

def write_caption_files(
    words: list[Word], preset: CaptionPreset, ass_path: Path, srt_path: Path,
    width: int = REF_W, height: int = REF_H,
) -> None:
    """Grava .ass (estilizado) e .srt. `words` já devem estar com tempos relativos ao trecho."""
    if not words:
        raise ClipForgeError("Não há falas neste trecho para gerar legenda.")
    ass_path.write_text(build_ass(cues_for_preset(words, preset), preset, width, height), encoding="utf-8")
    srt_path.write_text(build_srt(words), encoding="utf-8")
