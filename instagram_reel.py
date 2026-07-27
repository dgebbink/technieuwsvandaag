"""
Instagram Reel-video componeren uit stilstaande 9:16-slides (zie
INSTAGRAM_PLAN.md fase 7). Minimale versie: statische slides met harde cuts,
geen Ken Burns-zoom, geen audio — dat kan later als polish.

Puur ffmpeg via subprocess, geen netwerk. Handmatig testen:
    venv/bin/python3 instagram_reel.py <out.mp4> <slide1.jpg> <slide2.jpg> ...
"""
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

SLIDE_SECONDS = 3.0
FPS = 30
_FADE_SECONDS = 2.0


def build_reel_video(
    slide_paths: list[str],
    dest_path: str,
    seconds_per_slide: float = SLIDE_SECONDS,
    fps: int = FPS,
    audio_path: str = "",
) -> str | None:
    """Plakt stilstaande 9:16-slides aaneen tot één MP4 met een stil audiospoor.

    Er moet altijd een audiostream in: Instagram weigert een Reel zonder audio
    (container-status ERROR, Meta-foutcode 2207076 — zo mislukte de eerste echte
    Reel op 2026-07-26). "Silent" betekent dus een leeg audiospoor, niet géén
    audiospoor.

    Pre:  slide_paths bevat >=1 bestaande JPEG's, allemaal dezelfde afmeting
          (zie compose_instagram_image met canvas_w/canvas_h=1080x1920).
          audio_path is leeg (→ stil spoor) of verwijst naar een audiobestand
          waarvan jij de rechten hebt; het wordt geloopt tot de videolengte en
          eindigt met een fade-out.
    Post: MP4 op dest_path, elke slide seconds_per_slide lang, harde cuts, met
          een AAC-spoor even lang als de video; retourneert dest_path, of None
          bij elke fout (nooit raisen — zelfde contract als de rest van de
          social-pijplijn)
    """
    if not slide_paths:
        return None

    try:
        cmd = ["ffmpeg", "-y"]
        for path in slide_paths:
            cmd += ["-loop", "1", "-t", str(seconds_per_slide), "-i", path]

        n = len(slide_paths)
        total_seconds = n * seconds_per_slide

        # Audiobron als extra input (index n): een echt bestand als dat is
        # opgegeven en bestaat, anders een stille bron. Terugvallen op stilte
        # i.p.v. falen — een ontbrekend muziekbestand mag de wekelijkse Reel
        # niet tegenhouden.
        use_music = bool(audio_path) and Path(audio_path).is_file()
        if audio_path and not use_music:
            logger.warning("Audiobestand niet gevonden (%s) — stil spoor gebruikt", audio_path)

        if use_music:
            cmd += ["-stream_loop", "-1", "-i", str(audio_path)]
        else:
            cmd += [
                "-f", "lavfi",
                "-t", str(total_seconds),
                "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            ]

        concat_inputs = "".join(f"[{i}:v]" for i in range(n))
        filter_complex = f"{concat_inputs}concat=n={n}:v=1:a=0[outv]"

        cmd += ["-filter_complex", filter_complex, "-map", "[outv]", "-map", f"{n}:a"]

        if use_music:
            # Afkappen op de videolengte en de laatste 2s uitfaden, zodat het
            # nummer niet midden in een maat wordt afgebroken.
            fade_start = max(total_seconds - _FADE_SECONDS, 0)
            cmd += ["-af", f"afade=t=out:st={fade_start}:d={_FADE_SECONDS}"]

        cmd += [
            "-r", str(fps),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-shortest",
            str(dest_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
        logger.info("Reel-video gebouwd: %s (%d slides)", dest_path, n)
        return dest_path

    except subprocess.CalledProcessError as exc:
        logger.error("ffmpeg mislukt: %s", (exc.stderr or "")[-2000:])
        return None
    except Exception as exc:
        logger.error("Reel-video bouwen mislukt: %s", exc)
        return None


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(sys.argv) < 3:
        sys.exit("Gebruik: instagram_reel.py <out.mp4> <slide1.jpg> <slide2.jpg> ...")
    result = build_reel_video(sys.argv[2:], sys.argv[1])
    print(result or "MISLUKT")
