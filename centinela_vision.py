"""
PROYECTO CENTINELA — MÓDULO DE VISIÓN IA v1.0
Claude Vision API + OpenCV para detección en tiempo real
Stack: OpenCV + Pillow + Anthropic Vision + Rich terminal

Modos de operación:
  1. WEBCAM    — Captura desde cámara del drone/PC
  2. VIDEO     — Analiza archivo MP4 existente
  3. SIMULADO  — Escenas sintéticas si no hay cámara

Claude analiza cada frame y detecta:
  - Personas y grupos
  - Vehículos (normales y sospechosos)
  - Comportamientos anómalos
  - Objetos de interés táctico
"""

import os, sys, time, base64, random, math
from datetime import datetime
from collections import deque
from io import BytesIO
from typing import Optional
import anthropic

try:
    import cv2
    CV2_OK = True
except ImportError:
    CV2_OK = False

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_OK = True
except ImportError:
    PIL_OK = False

import numpy as np

from rich import box as rbox
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich.align import Align

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

API_KEY        = os.environ.get("ANTHROPIC_API_KEY", "")
ANALISIS_CADA  = 4       # segundos entre análisis de frames
MAX_FRAME_SIZE = (640, 480)
JPEG_QUALITY   = 75      # calidad JPEG para reducir tokens
HISTORIAL_MAX  = 15

AMBER = "bright_yellow"
BLUE  = "bright_cyan"
RED   = "bright_red"
GREEN = "bright_green"
DIM   = "dim white"
MAG   = "magenta"
WHITE = "white"

console = Console()

# ─────────────────────────────────────────────────────────────────────────────
# SISTEMA DE PROMPT — Especializado en seguridad ciudadana Lima
# ─────────────────────────────────────────────────────────────────────────────

VISION_SYSTEM = """Eres el sistema de visión táctico CENTINELA-EYE, operando sobre Lima, Perú.
Analizas imágenes de cámaras de drones de seguridad ciudadana.

Detecta y reporta en español:
1. PERSONAS: cantidad, comportamiento, grupos, actividad sospechosa
2. VEHÍCULOS: tipo, cantidad, comportamiento (estacionado, en movimiento, sospechoso)
3. ANOMALÍAS: peleas, aglomeraciones, intrusiones, objetos abandonados
4. RIESGO: nivel general de la escena (BAJO/MEDIO/ALTO/CRÍTICO)

RESPONDE SIEMPRE en este formato JSON exacto (sin markdown):
{
  "personas": <número entero o 0>,
  "vehiculos": <número entero o 0>,
  "nivel_riesgo": "BAJO|MEDIO|ALTO|CRITICO",
  "descripcion": "<descripción táctica en 1-2 oraciones>",
  "anomalia": "<anomalía específica o null>",
  "accion_recomendada": "<acción táctica o null>",
  "confianza": <0.0-1.0>
}"""


# ─────────────────────────────────────────────────────────────────────────────
# GENERADOR DE ESCENAS SINTÉTICAS (modo sin cámara)
# ─────────────────────────────────────────────────────────────────────────────

ESCENAS_LIMA = [
    {
        "nombre": "Parque Kennedy — Miraflores",
        "descripcion": "Vista aérea de parque con vegetación, bancas y personas caminando",
        "personas": random.randint(8, 25),
        "vehiculos": random.randint(0, 3),
        "riesgo": "BAJO",
        "hora": "diurna",
    },
    {
        "nombre": "Av. Larco — Miraflores",
        "descripcion": "Avenida comercial con flujo vehicular moderado y peatones en veredas",
        "personas": random.randint(15, 40),
        "vehiculos": random.randint(10, 30),
        "riesgo": "BAJO",
        "hora": "diurna",
    },
    {
        "nombre": "Mercado — La Victoria",
        "descripcion": "Zona de mercado con alta densidad peatonal y comercio informal",
        "personas": random.randint(30, 80),
        "vehiculos": random.randint(5, 15),
        "riesgo": "MEDIO",
        "hora": "diurna",
    },
    {
        "nombre": "Zona residencial — Surquillo",
        "descripcion": "Calle residencial nocturna con iluminación escasa",
        "personas": random.randint(0, 5),
        "vehiculos": random.randint(2, 8),
        "riesgo": "MEDIO",
        "hora": "nocturna",
    },
    {
        "nombre": "Esquina sospechosa — Barranco",
        "descripcion": "Grupo de personas en esquina con comportamiento inusual",
        "personas": random.randint(4, 8),
        "vehiculos": random.randint(1, 3),
        "riesgo": "ALTO",
        "hora": "nocturna",
    },
]

def generar_frame_sintetico(escena_idx: int, tick: int) -> np.ndarray:
    """
    Genera un frame sintético con PIL simulando vista de drone.
    Incluye texto de la escena para que Claude pueda leerlo.
    """
    if not PIL_OK:
        return np.zeros((480, 640, 3), dtype=np.uint8)

    escena = ESCENAS_LIMA[escena_idx % len(ESCENAS_LIMA)]
    W, H   = 640, 480

    # Fondo según hora
    if escena["hora"] == "nocturna":
        bg_color = (15, 20, 30)
        road_col = (30, 35, 45)
        sky_col  = (5, 10, 20)
    else:
        bg_color = (80, 120, 80)
        road_col = (100, 95, 90)
        sky_col  = (135, 180, 220)

    img = Image.new("RGB", (W, H), bg_color)
    draw = ImageDraw.Draw(img)

    # Cielo
    draw.rectangle([0, 0, W, H//3], fill=sky_col)

    # Suelo / calle
    draw.rectangle([0, H//2, W, H], fill=road_col)

    # Veredas
    draw.rectangle([0, H//2-20, W, H//2+10], fill=(120, 115, 110))

    # Vegetación (puntos verdes)
    for _ in range(20):
        x = random.randint(0, W)
        y = random.randint(H//3, H//2)
        r = random.randint(5, 15)
        g = random.randint(100, 180)
        draw.ellipse([x-r, y-r, x+r, y+r], fill=(30, g, 30))

    # Personas (puntos azules/blancos)
    num_personas = escena["personas"] + random.randint(-3, 3)
    num_personas = max(0, num_personas)
    for _ in range(num_personas):
        x = random.randint(20, W-20)
        y = random.randint(H//2-10, H-30)
        color = (200, 200, 255) if escena["hora"]=="nocturna" else (255, 220, 180)
        draw.ellipse([x-4, y-8, x+4, y+2], fill=color)
        draw.rectangle([x-3, y+2, x+3, y+12], fill=color)

    # Vehículos (rectángulos)
    num_vehiculos = escena["vehiculos"]
    for _ in range(num_vehiculos):
        x = random.randint(30, W-60)
        y = random.randint(H//2+20, H-20)
        col_v = (random.randint(100,250), random.randint(50,150), random.randint(50,150))
        draw.rectangle([x, y, x+40, y+20], fill=col_v)
        draw.rectangle([x+5, y-8, x+35, y+2], fill=(col_v[0]//2, col_v[1]//2+50, col_v[2]//2+50))

    # Overlay de info táctica
    overlay_h = 80
    overlay = Image.new("RGBA", (W, overlay_h), (0, 0, 0, 180))
    img.paste(Image.fromarray(np.array(overlay)[:,:,:3]), (0, 0))

    draw = ImageDraw.Draw(img)

    # Texto de información
    draw.text((8, 5),  f"CENTINELA-EYE | DRONE CAM",      fill=(255, 152, 0))
    draw.text((8, 20), f"SECTOR: {escena['nombre']}",       fill=(255, 255, 255))
    draw.text((8, 35), f"HORA: {datetime.now().strftime('%H:%M:%S')} | TICK: {tick:05d}", fill=(150, 200, 255))
    draw.text((8, 50), f"PERS: ~{num_personas} | VEH: ~{num_vehiculos} | RIESGO: {escena['riesgo']}", fill=(200, 200, 200))
    draw.text((8, 65), f"{escena['descripcion'][:70]}", fill=(150, 150, 150))

    # Crosshair central
    cx, cy = W//2, H//2+40
    draw.line([cx-15, cy, cx+15, cy], fill=(255, 100, 0), width=1)
    draw.line([cx, cy-15, cx, cy+15], fill=(255, 100, 0), width=1)
    draw.rectangle([cx-20, cy-20, cx+20, cy+20], outline=(255, 100, 0), width=1)

    # Indicador de riesgo (esquina superior derecha)
    riesgo_col = {"BAJO":(0,200,0),"MEDIO":(255,165,0),"ALTO":(255,60,0),"CRITICO":(255,0,0)}
    rc = riesgo_col.get(escena["riesgo"], (255,255,255))
    draw.rectangle([W-120, 5, W-5, 28], fill=(*rc, 200) if len(rc)==3 else rc)
    draw.text((W-115, 8), f"RIESGO: {escena['riesgo']}", fill=(0,0,0))

    return np.array(img)


def frame_a_base64(frame: np.ndarray) -> str:
    """Convierte frame numpy a base64 JPEG para Claude Vision API."""
    if PIL_OK:
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB) if CV2_OK else frame)
        img = img.resize(MAX_FRAME_SIZE, Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY)
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    else:
        # Fallback sin PIL
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        return base64.b64encode(buf.tobytes()).decode("utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# ANÁLISIS DE VISIÓN — Claude Vision API
# ─────────────────────────────────────────────────────────────────────────────

def analizar_frame(frame_b64: str, sector: str) -> dict:
    """
    Envía frame a Claude Vision API y obtiene análisis táctico.
    Modelo: claude-sonnet-4-6 (velocidad óptima para tiempo real)
    """
    if not API_KEY:
        return {
            "personas": random.randint(0, 20),
            "vehiculos": random.randint(0, 10),
            "nivel_riesgo": random.choice(["BAJO", "BAJO", "MEDIO", "ALTO"]),
            "descripcion": f"Análisis simulado del sector {sector}. Configure API key para visión real.",
            "anomalia": None,
            "accion_recomendada": None,
            "confianza": 0.0,
        }

    client = anthropic.Anthropic(api_key=API_KEY)
    try:
        r = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            system=VISION_SYSTEM,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": frame_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": f"Analiza esta imagen del sector {sector}. Responde en JSON.",
                    },
                ],
            }],
        )
        import json, re
        text  = r.content[0].text.strip()
        match = re.search(r'\{.*?\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        pass

    return {
        "personas": 0,
        "vehiculos": 0,
        "nivel_riesgo": "BAJO",
        "descripcion": "Error en análisis de visión.",
        "anomalia": None,
        "accion_recomendada": None,
        "confianza": 0.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD RICH — VISIÓN EN TIEMPO REAL
# ─────────────────────────────────────────────────────────────────────────────

def riesgo_color(nivel: str) -> str:
    return {"BAJO":GREEN,"MEDIO":AMBER,"ALTO":RED,"CRITICO":RED}.get(nivel, WHITE)


def build_vision_dashboard(
    sector: str,
    analisis: Optional[dict],
    historial: deque,
    stats: dict,
    tick: int,
    analizando: bool,
    modo: str,
) -> Table:
    ts  = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    hdr = Text()
    hdr.append("◈ CENTINELA-EYE", style=f"bold {AMBER}")
    hdr.append("  │  ", style=DIM)
    hdr.append("VISIÓN IA — CLAUDE SONNET 4.6", style=f"bold {BLUE}")
    hdr.append("  │  ", style=DIM)
    hdr.append(ts, style=WHITE)
    hdr.append(f"  │  TICK #{tick:05d}  │  MODO: {modo}", style=MAG)

    # Panel de detección actual
    if analisis:
        nivel    = analisis.get("nivel_riesgo", "BAJO")
        nc       = riesgo_color(nivel)
        personas = analisis.get("personas", 0)
        vehiculos= analisis.get("vehiculos", 0)
        desc     = analisis.get("descripcion", "")
        anomalia = analisis.get("anomalia")
        accion   = analisis.get("accion_recomendada")
        confianza= analisis.get("confianza", 0.0)

        det_content = Text()
        det_content.append(f"  SECTOR:    ", style=DIM)
        det_content.append(f"{sector}\n", style=f"bold {BLUE}")
        det_content.append(f"  RIESGO:    ", style=DIM)
        det_content.append(f"{nivel}\n", style=f"bold {nc}")
        det_content.append(f"  PERSONAS:  ", style=DIM)
        det_content.append(f"{personas} detectadas\n",
                           style=f"bold {AMBER if personas>10 else WHITE}")
        det_content.append(f"  VEHÍCULOS: ", style=DIM)
        det_content.append(f"{vehiculos} detectados\n",
                           style=f"bold {AMBER if vehiculos>5 else WHITE}")
        det_content.append(f"  ANÁLISIS:  ", style=DIM)
        det_content.append(f"{desc[:80]}\n", style=WHITE)
        if anomalia:
            det_content.append(f"  ⚠ ANOMALÍA: ", style=f"bold {RED}")
            det_content.append(f"{anomalia}\n", style=f"bold {RED}")
        if accion:
            det_content.append(f"  → ACCIÓN:  ", style=f"bold {AMBER}")
            det_content.append(f"{accion}\n", style=f"bold {AMBER}")
        det_content.append(f"  CONFIANZA: ", style=DIM)
        det_content.append(f"{confianza:.0%}", style=GREEN if confianza>0.7 else AMBER)

        if analizando:
            det_content.append(f"\n\n  ⟳ Claude analizando frame...", style=f"italic {DIM}")
    else:
        det_content = Text()
        if analizando:
            det_content.append("\n  ⟳ Primer análisis en progreso...", style=f"italic {DIM}")
        else:
            det_content.append("\n  Esperando primer frame...", style=DIM)

    det_panel = Panel(
        det_content,
        title=f"[bold {AMBER}]▸ DETECCIÓN EN TIEMPO REAL[/]",
        border_style=riesgo_color(analisis.get("nivel_riesgo","BAJO")) if analisis else DIM,
        padding=(0, 1),
    )

    # Panel estadísticas
    def srow(l, v, c=WHITE):
        t2 = Text()
        t2.append(f"  {l:<22}", style=DIM)
        t2.append(v, style=f"bold {c}")
        return t2

    stats_content = Text("\n").join([
        srow("Frames analizados", str(stats["frames"]),          BLUE),
        srow("Personas detectadas", str(stats["personas_total"]), AMBER),
        srow("Vehículos detectados", str(stats["vehiculos_total"]),AMBER),
        srow("Alertas generadas",   str(stats["alertas"]),        RED),
        srow("Nivel ALTO/CRÍTICO",  str(stats["alto_critico"]),   RED),
        srow("Modelo IA",           "Sonnet 4.6",                 BLUE),
        srow("Uptime visión",       f"{stats['uptime']:.0f}s",    MAG),
    ])
    stats_panel = Panel(
        stats_content,
        title=f"[bold {AMBER}]▸ ESTADÍSTICAS VISIÓN[/]",
        border_style=AMBER,
        padding=(1, 1),
    )

    # Panel historial
    h_table = Table(box=None, show_header=False, expand=True)
    h_table.add_column("HORA",   style=DIM,          width=9)
    h_table.add_column("SECTOR", style=f"bold {BLUE}", width=16)
    h_table.add_column("RIESGO", width=10)
    h_table.add_column("PERS",   justify="right",    width=5)
    h_table.add_column("VEH",    justify="right",    width=5)
    h_table.add_column("DETECCIÓN", style=WHITE)

    for entry in list(historial)[:8]:
        nc = riesgo_color(entry["nivel_riesgo"])
        h_table.add_row(
            entry["hora"],
            entry["sector"][:14],
            f"[{nc}]{entry['nivel_riesgo']}[/]",
            str(entry["personas"]),
            str(entry["vehiculos"]),
            entry["descripcion"][:40],
        )

    if not historial:
        h_table.add_row("—","—","—","—","—","Esperando análisis...")

    hist_panel = Panel(
        h_table,
        title=f"[bold {AMBER}]▸ HISTORIAL DE DETECCIONES[/]",
        border_style=BLUE,
        padding=(0, 1),
    )

    # Footer
    nivel_actual = analisis.get("nivel_riesgo","—") if analisis else "INICIANDO"
    nc = riesgo_color(nivel_actual)
    footer = Text(justify="center")
    footer.append("CENTINELA-EYE ACTIVE", style=f"bold {AMBER}")
    footer.append("  ·  ", style=DIM)
    footer.append(f"RIESGO: {nivel_actual}", style=f"bold {nc}")
    footer.append("  ·  ", style=DIM)
    footer.append("Claude Sonnet 4.6 Vision", style=f"bold {BLUE}")
    footer.append("  ·  Ctrl+C para detener", style=DIM)

    root = Table.grid(expand=True)
    root.add_row(Panel(Align.center(hdr), style=AMBER, padding=(0,2)))
    root.add_row(Columns([det_panel, stats_panel], expand=True))
    root.add_row(hist_panel)
    root.add_row(Panel(Align.center(footer), style=DIM, padding=(0,1)))
    return root


# ─────────────────────────────────────────────────────────────────────────────
# ORQUESTADOR PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def detectar_modo() -> tuple:
    """Detecta el modo de operación disponible."""
    # Intentar webcam
    if CV2_OK:
        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            cap.release()
            return "WEBCAM", None

    # Buscar video local
    videos = [f for f in os.listdir(".") if f.endswith((".mp4",".avi",".mov"))]
    if videos and CV2_OK:
        return "VIDEO", videos[0]

    # Modo simulado
    return "SIMULADO", None


def main():
    console.clear()
    console.print(Panel.fit(
        f"[bold {AMBER}]Inicializando CENTINELA-EYE...[/]\n"
        f"[{BLUE}]Detectando fuente de video...[/]\n"
        f"[{DIM}]Claude Sonnet 4.6 Vision API[/]",
        title="[bold white]EATON DYNAMICS — VISIÓN IA[/]",
        border_style=AMBER,
    ))
    time.sleep(1.0)

    modo, video_path = detectar_modo()
    console.print(f"[{AMBER}]Modo detectado: {modo}[/]")
    if not API_KEY:
        console.print(f"[{AMBER}]⚠ Sin API key — modo simulado de análisis[/]")
    time.sleep(0.8)

    # Inicializar captura
    cap = None
    if modo == "WEBCAM" and CV2_OK:
        cap = cv2.VideoCapture(0)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    elif modo == "VIDEO" and CV2_OK:
        cap = cv2.VideoCapture(video_path)

    sectores = ["Miraflores","San Isidro","Barranco",
                "Surquillo","La Victoria","Lince"]

    historial    = deque(maxlen=HISTORIAL_MAX)
    analisis     = None
    analizando   = False
    ultimo_anal  = 0.0
    tick         = 0
    escena_idx   = 0
    start_time   = time.time()

    stats = {
        "frames": 0, "personas_total": 0,
        "vehiculos_total": 0, "alertas": 0,
        "alto_critico": 0, "uptime": 0.0,
    }

    try:
        with Live(console=console, refresh_per_second=4) as live:
            while True:
                tick += 1
                stats["uptime"] = time.time() - start_time

                # ── Obtener frame ──
                sector = sectores[escena_idx % len(sectores)]
                frame  = None

                if cap and cap.isOpened():
                    ret, frame = cap.read()
                    if not ret:
                        if modo == "VIDEO":
                            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        frame = None

                if frame is None:
                    frame = generar_frame_sintetico(escena_idx, tick)

                # ── Análisis periódico con Claude Vision ──
                now = time.time()
                if now - ultimo_anal >= ANALISIS_CADA:
                    ultimo_anal = now
                    analizando  = True
                    live.update(build_vision_dashboard(
                        sector, analisis, historial, stats, tick, True, modo))

                    # Convertir frame a base64
                    frame_b64 = frame_a_base64(frame)

                    # Llamar a Claude Vision
                    analisis   = analizar_frame(frame_b64, sector)
                    analizando = False

                    # Actualizar estadísticas
                    stats["frames"]          += 1
                    stats["personas_total"]  += analisis.get("personas", 0)
                    stats["vehiculos_total"] += analisis.get("vehiculos", 0)

                    nivel = analisis.get("nivel_riesgo", "BAJO")
                    if nivel in ("ALTO", "CRITICO"):
                        stats["alto_critico"] += 1
                        stats["alertas"]      += 1

                    # Agregar al historial
                    historial.appendleft({
                        "hora":        datetime.now().strftime("%H:%M:%S"),
                        "sector":      sector,
                        "nivel_riesgo":nivel,
                        "personas":    analisis.get("personas", 0),
                        "vehiculos":   analisis.get("vehiculos", 0),
                        "descripcion": analisis.get("descripcion", ""),
                        "anomalia":    analisis.get("anomalia"),
                    })

                    # Rotar sector cada análisis
                    escena_idx += 1

                # Render dashboard
                live.update(build_vision_dashboard(
                    sector, analisis, historial, stats, tick, analizando, modo))

                time.sleep(0.25)

    except KeyboardInterrupt:
        if cap:
            cap.release()
        console.print(f"\n[bold {AMBER}]◈ CENTINELA-EYE DETENIDO.[/]")
        if stats["frames"] > 0:
            console.print(
                f"  Frames analizados:  {stats['frames']}\n"
                f"  Personas detectadas: {stats['personas_total']}\n"
                f"  Alertas generadas:  {stats['alertas']}"
            )


if __name__ == "__main__":
    main()
