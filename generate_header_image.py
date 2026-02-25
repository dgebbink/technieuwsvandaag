"""
generate_header_image.py — Genereert een header logo image voor TechNieuwsVandaag.

Stappen:
  1. Genereer 3 varianten via FAL.ai (landscape_16_9, resize naar 400x80)
  2. Genereer 1 Pillow-fallback (tekst-gebaseerd logo)
  3. Sla alles op in /tmp/tnv-news/ voor review
  4. Vraag gebruiker welke variant te activeren
  5. Upload geselecteerde afbeelding naar WordPress media library
  6. Update header.php op server (voeg theme_mod logo check toe)
  7. Voeg .tnv-header-logo CSS toe aan style.css
  8. Voeg customizer-registratie toe aan functions.php
  9. Stel theme_mod tnv_header_logo_url in via WP-CLI

Gebruik:
  python3 generate_header_image.py
  python3 generate_header_image.py --variant 2   # sla keuze-stap over
"""
import argparse
import base64
import os
import subprocess
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont, ImageOps

load_dotenv()

# ---------------------------------------------------------------------------
# Configuratie
# ---------------------------------------------------------------------------

FAL_API_KEY     = os.environ.get("FAL_API_KEY", "")
WP_URL          = os.environ.get("WP_URL", "https://technieuwsvandaag.nl")
WP_USERNAME     = os.environ.get("WP_USERNAME", "")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")

FAL_ENDPOINT = "https://fal.run/fal-ai/flux/dev"
OUTPUT_DIR   = Path("/tmp/tnv-news")
SSH_KEY      = Path.home() / ".ssh/ssh-key-oracle-web.key"
SERVER       = "ubuntu@141.144.195.65"
WP_PATH      = "/var/www/technieuwsvandaag/wordpress"
THEME_PATH   = f"{WP_PATH}/wp-content/themes/tnv-news"

LOGO_W, LOGO_H = 400, 80

# FAL.ai prompts — 3 varianten
PROMPTS = [
    # Variant 1: Circuit board + T-letter
    (
        "Ultra-wide horizontal logo banner for Dutch tech news website. "
        "Bold circuit board pattern forming stylized letter T on deep navy blue #0A1628 background. "
        "Glowing cyan #00D4FF data streams, white highlights. "
        "Flat design, no text, no people. Clean professional editorial style. "
        "Extreme panoramic wide format, high contrast."
    ),
    # Variant 2: Data wave / particle stream
    (
        "Horizontal header banner for tech news brand. "
        "Abstract digital wave pattern with flowing blue-to-cyan gradient on dark background. "
        "Glowing particle trails and network node connections in motion. "
        "Deep navy blue #0A1628 with bright cyan and white light streaks. "
        "No text, no people. Wide panoramic, ultra-clean modern design."
    ),
    # Variant 3: Binary + hexagons met rode accenten
    (
        "Wide tech news header image. "
        "Abstract binary data streams and floating glowing hexagons on deep dark navy background. "
        "Vibrant red #CC0000 accent highlights mixed with bright white and cyan. "
        "Clean, minimalist, professional atmosphere. "
        "No text, no logos. Ultra-wide horizontal banner format."
    ),
]


# ---------------------------------------------------------------------------
# FAL.ai genereren
# ---------------------------------------------------------------------------

def generate_fal_variants() -> list[Path]:
    """Genereer 3 logo-varianten via FAL.ai en resize naar 400x80.

    Pre:  FAL_API_KEY geconfigureerd; OUTPUT_DIR beschikbaar
    Post: JPEG-bestanden in OUTPUT_DIR; lege lijst als FAL_API_KEY ontbreekt
    """
    if not FAL_API_KEY:
        print("  ✗ FAL_API_KEY niet geconfigureerd — FAL.ai varianten overgeslagen")
        return []

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for i, prompt in enumerate(PROMPTS, start=1):
        dest_raw   = OUTPUT_DIR / f"header_variant_{i}_raw.jpg"
        dest_final = OUTPUT_DIR / f"header_variant_{i}.jpg"

        print(f"  FAL.ai variant {i}/3 genereren…", end="", flush=True)

        for attempt in range(1, 3):
            try:
                resp = requests.post(
                    FAL_ENDPOINT,
                    headers={
                        "Authorization": f"Key {FAL_API_KEY}",
                        "Content-Type":  "application/json",
                    },
                    json={
                        "prompt":                prompt,
                        "image_size":            "landscape_16_9",
                        "num_inference_steps":   35,
                        "guidance_scale":        4.0,
                        "num_images":            1,
                        "enable_safety_checker": True,
                    },
                    timeout=120,
                )
                resp.raise_for_status()
                image_url = resp.json()["images"][0]["url"]

                img_bytes = requests.get(image_url, timeout=30)
                img_bytes.raise_for_status()
                dest_raw.write_bytes(img_bytes.content)

                # Center crop + resize naar 400x80
                img = Image.open(dest_raw).convert("RGB")
                img = ImageOps.fit(img, (LOGO_W, LOGO_H), Image.LANCZOS)
                img.save(dest_final, "JPEG", quality=92)

                print(f" ✓  {dest_final}")
                paths.append(dest_final)
                break

            except Exception as exc:
                if attempt == 1:
                    print(" poging 2…", end="", flush=True)
                    time.sleep(5)
                else:
                    print(f" ✗ mislukt: {exc}")

    return paths


# ---------------------------------------------------------------------------
# Pillow fallback
# ---------------------------------------------------------------------------

def generate_pillow_logo() -> Path:
    """Genereer tekst-gebaseerd logo via Pillow (geen FAL.ai vereist).

    Pre:  OUTPUT_DIR beschikbaar; truetype-fonts aanwezig (Ubuntu/DejaVu/Liberation)
    Post: PNG-bestand in OUTPUT_DIR
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUTPUT_DIR / "header_variant_4.png"

    img  = Image.new("RGB", (LOGO_W, LOGO_H), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    # Font laden — probeer Ubuntu Bold, daarna DejaVu Bold, daarna Liberation Bold
    font_candidates = [
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    font_large = None
    font_small = None
    for fp in font_candidates:
        if Path(fp).exists():
            try:
                font_large = ImageFont.truetype(fp, 30)
                font_small = ImageFont.truetype(fp, 13)
                break
            except Exception:
                continue

    if font_large is None:
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Tekst-segmenten met kleur (op witte achtergrond)
    parts = [
        ("Tech",    (26,  26,  26 )),   # zwart
        ("Nieuws",  (26,  26,  26 )),   # zwart
        ("Vandaag", (204, 0,   0  )),   # rood
    ]

    spacing = 5
    # Bereken totale breedte via textlength
    widths = [draw.textlength(text, font=font_large) for text, _ in parts]
    total_w = sum(widths) + spacing * (len(parts) - 1)
    x = (LOGO_W - total_w) / 2
    y = (LOGO_H / 2) - 18  # boven-offset

    for (text, color), w in zip(parts, widths):
        draw.text((x, y), text, font=font_large, fill=color)
        x += w + spacing

    # Tagline
    tagline = "aangedreven door AI"
    tl_w = draw.textlength(tagline, font=font_small)
    draw.text(((LOGO_W - tl_w) / 2, y + 34), tagline, font=font_small, fill=(153, 153, 153))

    img.save(dest, "PNG")
    print(f"  ✓ Pillow fallback opgeslagen: {dest}")
    return dest


# ---------------------------------------------------------------------------
# Variant-selectie
# ---------------------------------------------------------------------------

def select_variant(fal_paths: list[Path], pillow_path: Path, forced: int | None) -> Path:
    """Toon beschikbare varianten en vraag gebruiker om keuze.

    Pre:  fal_paths bevat 0–3 paden; pillow_path bestaat
    Post: geselecteerd pad
    """
    all_paths: list[tuple[int, Path]] = []
    for path in fal_paths:
        num = int(path.stem.split("_")[2])   # header_variant_1 → 1
        all_paths.append((num, path))
    all_paths.append((4, pillow_path))

    if forced is not None:
        for num, path in all_paths:
            if num == forced:
                print(f"  Variant {forced} geselecteerd via --variant")
                return path
        print(f"  ✗ --variant {forced} niet beschikbaar")
        sys.exit(1)

    print("\nBeschikbare varianten:")
    for num, path in all_paths:
        label = "Pillow fallback" if num == 4 else f"FAL.ai variant {num}"
        print(f"  [{num}] {label}: {path}")
    print()

    while True:
        raw = input("Welke variant wil je gebruiken? [1-4]: ").strip()
        try:
            choice = int(raw)
        except ValueError:
            print("  Voer een getal in tussen 1 en 4")
            continue
        for num, path in all_paths:
            if num == choice:
                return path
        print(f"  Ongeldige keuze: {choice}")


# ---------------------------------------------------------------------------
# WordPress upload
# ---------------------------------------------------------------------------

def upload_to_wordpress(image_path: Path) -> str:
    """Upload afbeelding naar WordPress media library via REST API.

    Pre:  WP_USERNAME en WP_APP_PASSWORD geconfigureerd; bestand bestaat
    Post: publieke URL van geüpload bestand
    Raises: RuntimeError bij ontbrekende credentials of HTTP-fout
    """
    if not WP_USERNAME or not WP_APP_PASSWORD:
        raise RuntimeError("WP_USERNAME / WP_APP_PASSWORD niet geconfigureerd in .env")

    token = base64.b64encode(f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()).decode()
    mime  = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"

    with open(image_path, "rb") as f:
        resp = requests.post(
            f"{WP_URL}/wp-json/wp/v2/media",
            headers={
                "Authorization":       f"Basic {token}",
                "Content-Type":        mime,
                "Content-Disposition": f'attachment; filename="{image_path.name}"',
            },
            data=f.read(),
            timeout=30,
        )
    resp.raise_for_status()
    url = resp.json().get("source_url", "")
    if not url:
        raise RuntimeError(f"Geen source_url in WP-response: {resp.text[:200]}")
    return url


# ---------------------------------------------------------------------------
# SSH hulpfuncties
# ---------------------------------------------------------------------------

def _ssh(cmd: str) -> str:
    """Voer SSH-commando uit op de server en geef stdout terug."""
    result = subprocess.run(
        ["ssh", "-i", str(SSH_KEY), SERVER, cmd],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"SSH-fout (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout


def _ssh_write(remote_path: str, content: str) -> None:
    """Schrijf content naar remote_path via SSH pipe (met sudo tee)."""
    result = subprocess.run(
        ["ssh", "-i", str(SSH_KEY), SERVER, f"sudo tee {remote_path} > /dev/null"],
        input=content.encode(),
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"SSH schrijffout: {result.stderr.decode().strip()}")


# ---------------------------------------------------------------------------
# Theme bestanden bijwerken
# ---------------------------------------------------------------------------

def update_header_php() -> None:
    """Voeg theme_mod logo check toe aan header.php op de server.

    Idempotent: als de check al aanwezig is, wordt niets gewijzigd.
    """
    current = _ssh(f"cat {THEME_PATH}/header.php")

    if "tnv_header_logo_url" in current:
        print("  ✓ header.php al bijgewerkt (skip)")
        return

    old_snippet = "            <?php if ( has_custom_logo() ) : ?>"
    new_snippet = (
        "            <?php $header_logo = get_theme_mod( 'tnv_header_logo_url', '' ); ?>\n"
        "            <?php if ( $header_logo ) : ?>\n"
        "                <a href=\"<?php echo esc_url( home_url( '/' ) ); ?>\" rel=\"home\" class=\"tnv-header-logo-link\">\n"
        "                    <img src=\"<?php echo esc_url( $header_logo ); ?>\"\n"
        "                         alt=\"<?php bloginfo( 'name' ); ?>\"\n"
        "                         class=\"tnv-header-logo\">\n"
        "                </a>\n"
        "            <?php elseif ( has_custom_logo() ) : ?>"
    )

    updated = current.replace(old_snippet, new_snippet, 1)
    if updated == current:
        # Fallback: probeer variant zonder leading spaces
        old_snippet2 = "<?php if ( has_custom_logo() ) : ?>"
        new_snippet2 = (
            "<?php $header_logo = get_theme_mod( 'tnv_header_logo_url', '' ); ?>\n"
            "            <?php if ( $header_logo ) : ?>\n"
            "                <a href=\"<?php echo esc_url( home_url( '/' ) ); ?>\" rel=\"home\" class=\"tnv-header-logo-link\">\n"
            "                    <img src=\"<?php echo esc_url( $header_logo ); ?>\"\n"
            "                         alt=\"<?php bloginfo( 'name' ); ?>\"\n"
            "                         class=\"tnv-header-logo\">\n"
            "                </a>\n"
            "            <?php elseif ( has_custom_logo() ) : ?>"
        )
        updated = current.replace(old_snippet2, new_snippet2, 1)

    if updated == current:
        print("  ✗ header.php: kon snippet niet vinden — handmatig bijwerken vereist")
        return

    _ssh_write(f"{THEME_PATH}/header.php", updated)
    print("  ✓ header.php bijgewerkt")


def update_style_css() -> None:
    """Voeg .tnv-header-logo CSS toe aan style.css en bump de versie.

    Idempotent: als .tnv-header-logo al aanwezig is, wordt alleen de versie gebumped.
    """
    current = _ssh(f"cat {THEME_PATH}/style.css")

    logo_css = """
/* ============================================================
   Header logo image (gegenereerd via generate_header_image.py)
   ============================================================ */
.tnv-header-logo-link {
    display: block;
    line-height: 0;
    text-decoration: none;
}

.tnv-header-logo {
    height: 48px;
    width: auto;
    display: block;
}
"""

    if ".tnv-header-logo" not in current:
        current += logo_css

    # Bump versie in de stylesheet header (Version: X.Y.Z)
    import re
    def bump_version(m: re.Match) -> str:
        parts = m.group(1).split(".")
        parts[-1] = str(int(parts[-1]) + 1)
        return f"Version: {'.'.join(parts)}"

    updated = re.sub(r"Version:\s*([\d.]+)", bump_version, current, count=1)

    _ssh_write(f"{THEME_PATH}/style.css", updated)
    print("  ✓ style.css bijgewerkt (logo CSS + versie gebumped)")


def update_functions_php() -> None:
    """Voeg customizer-registratie toe aan functions.php voor tnv_header_logo_url.

    Idempotent: als de registratie al aanwezig is, wordt niets gewijzigd.
    """
    current = _ssh(f"cat {THEME_PATH}/functions.php")

    if "tnv_header_logo_url" in current:
        print("  ✓ functions.php al bijgewerkt (skip)")
        return

    customizer_block = """

// ============================================================
// 14. HEADER LOGO CUSTOMIZER
// ============================================================
add_action( 'customize_register', 'tnv_customize_register' );
function tnv_customize_register( $wp_customize ) {
    $wp_customize->add_setting( 'tnv_header_logo_url', array(
        'default'           => '',
        'sanitize_callback' => 'esc_url_raw',
        'transport'         => 'refresh',
    ) );
    $wp_customize->add_control( new WP_Customize_Image_Control(
        $wp_customize,
        'tnv_header_logo_url',
        array(
            'label'    => __( 'Header Logo', 'tnv-news' ),
            'section'  => 'title_tagline',
            'settings' => 'tnv_header_logo_url',
        )
    ) );
}
"""

    _ssh_write(f"{THEME_PATH}/functions.php", current + customizer_block)
    print("  ✓ functions.php bijgewerkt (customizer-registratie toegevoegd)")


def set_theme_mod(logo_url: str) -> None:
    """Stel theme_mod tnv_header_logo_url in via WP-CLI."""
    safe_url = logo_url.replace("'", "\\'")
    _ssh(
        f"wp --path={WP_PATH} eval "
        f"\"set_theme_mod('tnv_header_logo_url', '{safe_url}');\""
    )
    print(f"  ✓ theme_mod tnv_header_logo_url = {logo_url}")


def flush_cache() -> None:
    """Flush WordPress rewrite- en object-cache."""
    _ssh(f"wp --path={WP_PATH} cache flush 2>/dev/null || true")
    print("  ✓ Cache geflusht")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Genereer header logo voor TechNieuwsVandaag")
    parser.add_argument(
        "--variant", type=int, choices=[1, 2, 3, 4],
        help="Sla keuze-stap over en gebruik variant N direct (1-3 = FAL.ai, 4 = Pillow)"
    )
    args = parser.parse_args()

    print("\n🎨 Stap 1 — FAL.ai varianten genereren…")
    fal_paths = generate_fal_variants()
    if not fal_paths:
        print("  (geen FAL.ai varianten — alleen Pillow fallback beschikbaar)")

    print("\n🖼️  Stap 2 — Pillow fallback genereren…")
    pillow_path = generate_pillow_logo()

    if not fal_paths and args.variant and args.variant in (1, 2, 3):
        print(f"  ✗ FAL.ai variant {args.variant} gevraagd maar geen FAL.ai varianten beschikbaar")
        return 1

    print("\n📋 Stap 3 — Variant selecteren…")
    selected = select_variant(fal_paths, pillow_path, args.variant)
    print(f"  Geselecteerd: {selected}")

    print("\n⬆️  Stap 4 — Uploaden naar WordPress…")
    try:
        logo_url = upload_to_wordpress(selected)
        print(f"  ✓ URL: {logo_url}")
    except Exception as exc:
        print(f"  ✗ Upload mislukt: {exc}")
        return 1

    print("\n🔧 Stap 5 — Theme bestanden bijwerken op server…")
    try:
        update_header_php()
        update_style_css()
        update_functions_php()
    except Exception as exc:
        print(f"  ✗ Theme update mislukt: {exc}")
        return 1

    print("\n⚙️  Stap 6 — theme_mod instellen via WP-CLI…")
    try:
        set_theme_mod(logo_url)
        flush_cache()
    except Exception as exc:
        print(f"  ✗ theme_mod instellen mislukt: {exc}")
        return 1

    print(
        f"\n✅ Klaar! Controleer het resultaat op:\n"
        f"   {WP_URL}\n"
        f"\n   Logo URL: {logo_url}\n"
        f"   Om de keuze ongedaan te maken:\n"
        f"   ssh -i ~/.ssh/ssh-key-oracle-web.key {SERVER} \\\n"
        f"     \"wp --path={WP_PATH} eval \\\"delete_theme_mod('tnv_header_logo_url');\\\"\"\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
