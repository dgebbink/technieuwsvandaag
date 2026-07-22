"""
Instagram Reel-video componeren uit stilstaande 9:16-slides (zie
INSTAGRAM_PLAN.md fase 7). Minimale versie: statische slides met harde cuts,
geen Ken Burns-zoom, geen audio — dat kan later als polish.

Puur ffmpeg via subprocess, geen netwerk. Handmatig testen:
    venv/bin/python3 instagram_reel.py <out.mp4> <slide1.jpg> <slide2.jpg> ...
"""
import logging
import subprocess

logger = logging.getLogger(__name__)

SLIDE_SECONDS = 3.0
FPS = 30


def build_reel_video(
    slide_paths: list[str],
    dest_path: str,
    seconds_per_slide: float = SLIDE_SECONDS,
    fps: int = FPS,
) -> str | None:
    """Plakt stilstaande 9:16-slides aaneen tot één silent MP4.

    Pre:  slide_paths bevat >=1 bestaande JPEG's, allemaal dezelfde afmeting
          (zie compose_instagram_image met canvas_w/canvas_h=1080x1920)
    Post: MP4 op dest_path, elke slide seconds_per_slide lang, harde cuts,
          geen audiospoor; retourneert dest_path, of None bij elke fout
          (nooit raisen — zelfde contract als de rest van de social-pijplijn)
    """
    if not slide_paths:
        return None

    try:
        cmd = ["ffmpeg", "-y"]
        for path in slide_paths:
            cmd += ["-loop", "1", "-t", str(seconds_per_slide), "-i", path]

        n = len(slide_paths)
        concat_inputs = "".join(f"[{i}:v]" for i in range(n))
        filter_complex = f"{concat_inputs}concat=n={n}:v=1:a=0[outv]"

        cmd += [
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-r", str(fps),
            "-pix_fmt", "yuv420p",
            "-an",
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
