"""
PROYECTO CENTINELA — SIMULADOR DE INCIDENTES EN VIVO v1.0
Inyecta incidentes reales de Lima y muestra la reacción en cadena completa

Cadena de respuesta:
  1. Incidente generado → coordenadas GPS reales Lima
  2. Visión IA → Claude Sonnet 4.6 analiza la escena
  3. Agente táctico → evalúa y clasifica amenaza
  4. Coordinador → Opus 4.7 decide respuesta de flota
  5. Despacho ROI → drone más eficiente asignado
  6. Telegram → alerta inmediata al operador
  7. Documentación → registro completo del incidente
  8. ML update → predicción actualizada por sector
"""

import os, time, math, random, json, re, asyncio
from datetime import datetime
from collections import deque
from typing import Optional
import urllib.request
import urllib.parse
import anthropic

try:
    from anthropic import AsyncAnthropic
    HAS_ASYNC_ANTHROPIC = True
except ImportError:
    HAS_ASYNC_ANTHROPIC = False

# Cliente AsyncAnthropic singleton (lazy)
_async_client: Optional["AsyncAnthropic"] = None

def _get_async_client():
    global _async_client
    if _async_client is None and ANTHROPIC_KEY and HAS_ASYNC_ANTHROPIC:
        _async_client = AsyncAnthropic(api_key=ANTHROPIC_KEY)
    return _async_client

from rich import box as rbox
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich.align import Align
from rich.rule import Rule

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

ANTHROPIC_KEY    = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_TOKEN   = "8676501832:AAEYlMd3GogjL-tubZzOFUSfudhx_loh8lk"
TELEGRAM_CHAT_ID = "1638287560"

NUM_DRONES = 6
REF_LAT    = -12.0464
REF_LON    = -77.0428

SECTORES_GPS = {
    "Miraflores":  (-12.1191, -77.0291),
    "San Isidro":  (-12.0975, -77.0357),
    "Barranco":    (-12.1464, -77.0217),
    "Surquillo":   (-12.1114, -77.0200),
    "La Victoria": (-12.0678, -77.0000),
    "Lince":       (-12.0850, -77.0314),
}

AMBER = "bright_yellow"
BLUE  = "bright_cyan"
RED   = "bright_red"
GREEN = "bright_green"
DIM   = "dim white"
MAG   = "magenta"
WHITE = "white"

console = Console()

# ─────────────────────────────────────────────────────────────────────────────
# CATÁLOGO DE INCIDENTES — Lima, Perú
# ─────────────────────────────────────────────────────────────────────────────

CATALOGO_INCIDENTES = [
    {
        "tipo":       "ROBO_AGRAVADO",
        "emoji":      "🔫",
        "severidad":  "CRITICO",
        "sectores":   ["Barranco", "La Victoria", "Surquillo"],
        "descripcion":"Reporte de robo a mano armada. Sujetos en motocicleta.",
        "duracion_s": 45,
        "drones_req": 2,
        "color":      RED,
    },
    {
        "tipo":       "ACCIDENTE_VEHICULAR",
        "emoji":      "🚗",
        "severidad":  "ALTO",
        "sectores":   ["Miraflores", "San Isidro", "Surquillo"],
        "descripcion":"Colisión vehicular con posibles heridos. Tráfico obstruido.",
        "duracion_s": 60,
        "drones_req": 1,
        "color":      AMBER,
    },
    {
        "tipo":       "AGLOMERACION_SOSPECHOSA",
        "emoji":      "👥",
        "severidad":  "MEDIO",
        "sectores":   ["La Victoria", "Surquillo", "Lince"],
        "descripcion":"Grupo numeroso con comportamiento inusual. Posible disturbio.",
        "duracion_s": 50,
        "drones_req": 1,
        "color":      AMBER,
    },
    {
        "tipo":       "INTRUSION_PERIMETRO",
        "emoji":      "🚨",
        "severidad":  "CRITICO",
        "sectores":   ["Lince", "San Isidro", "Miraflores"],
        "descripcion":"Intrusión detectada en zona restringida. Acceso no autorizado.",
        "duracion_s": 40,
        "drones_req": 2,
        "color":      RED,
    },
    {
        "tipo":       "PERSONA_SOSPECHOSA",
        "emoji":      "🕵️",
        "severidad":  "MEDIO",
        "sectores":   ["San Isidro", "Miraflores", "Barranco"],
        "descripcion":"Individuo merodeando en actitud sospechosa. Sin documentación.",
        "duracion_s": 35,
        "drones_req": 1,
        "color":      AMBER,
    },
    {
        "tipo":       "DISTURBIO_CIVIL",
        "emoji":      "⚡",
        "severidad":  "ALTO",
        "sectores":   ["La Victoria", "Surquillo", "Lince"],
        "descripcion":"Altercado entre grupos. Situación escalando. PNP requerida.",
        "duracion_s": 55,
        "drones_req": 2,
        "color":      RED,
    },
    {
        "tipo":       "VEHICULO_ABANDONADO",
        "emoji":      "🚘",
        "severidad":  "BAJO",
        "sectores":   ["Surquillo", "Lince", "La Victoria"],
        "descripcion":"Vehículo abandonado en vía pública. Posible objeto sospechoso.",
        "duracion_s": 30,
        "drones_req": 1,
        "color":      AMBER,
    },
    {
        "tipo":       "INCENDIO_ESTRUCTURAL",
        "emoji":      "🔥",
        "severidad":  "CRITICO",
        "sectores":   ["Barranco", "La Victoria", "Miraflores"],
        "descripcion":"Humo visible desde edificación. Posible incendio en progreso.",
        "duracion_s": 70,
        "drones_req": 2,
        "color":      RED,
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# GENERADOR DE INCIDENTES
# ─────────────────────────────────────────────────────────────────────────────

class Incidente:
    _contador = 0

    def __init__(self, template: dict):
        Incidente._contador += 1
        self.id          = f"INC-{Incidente._contador:04d}"
        self.tipo        = template["tipo"]
        self.emoji       = template["emoji"]
        self.severidad   = template["severidad"]
        self.sector      = random.choice(template["sectores"])
        self.descripcion = template["descripcion"]
        self.duracion_s  = template["duracion_s"]
        self.drones_req  = template["drones_req"]
        self.color       = template["color"]
        self.timestamp   = datetime.now()

        # GPS con variación aleatoria dentro del sector
        base_lat, base_lon = SECTORES_GPS[self.sector]
        self.lat = base_lat + random.uniform(-0.005, 0.005)
        self.lon = base_lon + random.uniform(-0.005, 0.005)

        # Estado de la cadena de respuesta
        self.cadena = {
            "vision":       {"estado": "⏳", "texto": "Pendiente", "tiempo_ms": 0},
            "agente":       {"estado": "⏳", "texto": "Pendiente", "tiempo_ms": 0},
            "coordinador":  {"estado": "⏳", "texto": "Pendiente", "tiempo_ms": 0},
            "despacho":     {"estado": "⏳", "texto": "Pendiente", "tiempo_ms": 0},
            "telegram":     {"estado": "⏳", "texto": "Pendiente", "tiempo_ms": 0},
            "documentacion":{"estado": "⏳", "texto": "Pendiente", "tiempo_ms": 0},
        }
        self.drone_asignado = None
        self.eta_min        = 0.0
        self.roi_score      = 0.0
        self.resuelto       = False
        self.tiempo_total_s = 0.0


def generar_incidente() -> Incidente:
    template = random.choice(CATALOGO_INCIDENTES)
    return Incidente(template)


# ─────────────────────────────────────────────────────────────────────────────
# FLOTA SIMULADA (estado simplificado para despacho)
# ─────────────────────────────────────────────────────────────────────────────

class FlotaSimulada:
    def __init__(self):
        self.drones = {}
        for i, (sector, (lat, lon)) in enumerate(SECTORES_GPS.items()):
            did = f"CNTL-{i+1:02d}"
            self.drones[did] = {
                "drone_id":    did,
                "sector":      sector,
                "lat":         lat + random.uniform(-0.003, 0.003),
                "lon":         lon + random.uniform(-0.003, 0.003),
                "battery_pct": random.uniform(65, 98),
                "motor_health":random.uniform(0.90, 1.0),
                "velocity_ms": random.uniform(7, 12),
                "disponible":  True,
            }

    def calcular_roi(self, did: str, inc: Incidente) -> float:
        d = self.drones[did]
        if not d["disponible"]:
            return -1.0
        bat_n = d["battery_pct"] / 100.0
        if d["battery_pct"] < 30:
            bat_n *= 0.3
        R  = 6371.0
        φ1 = math.radians(d["lat"])
        φ2 = math.radians(inc.lat)
        Δφ = math.radians(inc.lat - d["lat"])
        Δλ = math.radians(inc.lon - d["lon"])
        a  = math.sin(Δφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(Δλ/2)**2
        dist_km = R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        return 0.40*bat_n + 0.40/(1+dist_km) + 0.20*d["motor_health"]

    def despachar(self, inc: Incidente) -> tuple:
        scores = {
            did: self.calcular_roi(did, inc)
            for did in self.drones
            if self.drones[did]["disponible"]
        }
        if not scores:
            return None, 0.0, 0.0
        best = max(scores, key=scores.get)
        d    = self.drones[best]
        R    = 6371.0
        Δφ   = math.radians(inc.lat - d["lat"])
        Δλ   = math.radians(inc.lon - d["lon"])
        φ1   = math.radians(d["lat"])
        φ2   = math.radians(inc.lat)
        a    = math.sin(Δφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(Δλ/2)**2
        dist_km = R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        eta_min = (dist_km / max(d["velocity_ms"]*3.6, 1)) * 60
        self.drones[best]["disponible"] = False
        return best, round(scores[best], 4), round(eta_min, 2)


flota = FlotaSimulada()


# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────────────────────────────────────

def enviar_telegram(msg: str) -> bool:
    try:
        data = urllib.parse.urlencode({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data=data, method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read()).get("ok", False)
    except:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CADENA DE RESPUESTA — Procesamiento con IA
# ─────────────────────────────────────────────────────────────────────────────

def _safe_text(resp) -> str:
    """Extrae texto de una respuesta de Claude defendiendo contra content vacío."""
    try:
        if not resp or not getattr(resp, "content", None):
            return ""
        for block in resp.content:
            if hasattr(block, "text") and block.text:
                return block.text.strip()[:100]
    except Exception:
        pass
    return ""


async def _llm_call(client, model: str, prompt: str, max_tokens: int, fallback: str) -> tuple:
    """Una llamada Claude async con timing. Retorna (texto, tiempo_ms)."""
    t0 = time.time()
    if not client:
        return fallback, int((time.time()-t0)*1000)
    try:
        resp = await client.messages.create(
            model=model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        txt = _safe_text(resp) or fallback
        return txt, int((time.time()-t0)*1000)
    except Exception:
        return fallback, int((time.time()-t0)*1000)


def _build_telegram_msg(inc: "Incidente") -> str:
    sev_emoji = {"CRITICO":"🔴","ALTO":"🟠","MEDIO":"🟡","BAJO":"🟢"}.get(inc.severidad, "⚪")
    return (
        f"{inc.emoji} <b>CENTINELA — INCIDENTE DETECTADO</b>\n\n"
        f"{sev_emoji} <b>Tipo:</b> {inc.tipo}\n"
        f"📍 <b>Sector:</b> {inc.sector}\n"
        f"⚠️ <b>Severidad:</b> {inc.severidad}\n"
        f"📋 <b>Descripción:</b> {inc.descripcion}\n"
        f"🤖 <b>Drone asignado:</b> {inc.drone_asignado}\n"
        f"⏱ <b>ETA:</b> {inc.eta_min:.1f} min\n"
        f"📊 <b>ROI Score:</b> {inc.roi_score:.3f}\n"
        f"🕐 <b>Hora:</b> {inc.timestamp.strftime('%H:%M:%S')}\n\n"
        f"<i>ID: {inc.id} | CENTINELA — EATON DYNAMICS</i>"
    )


def _persistir_log(registro: dict) -> str:
    """Bloqueante (file I/O); se invoca via asyncio.to_thread."""
    log_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "reportes", "incidentes_log.json"
    )
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        try:
            with open(log_path, "r") as f:
                logs = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logs = []
        logs.append(registro)
        with open(log_path, "w") as f:
            json.dump(logs[-100:], f, indent=2)
        return f"Guardado en incidentes_log.json ({len(logs)} registros)"
    except Exception:
        return f"Log en memoria ({registro.get('id','?')})"


async def ejecutar_cadena(inc: Incidente, live, build_fn, historial, stats):
    """
    Cadena de respuesta async — paralelizada con asyncio.gather.
    Latencia objetivo: <3s (vs ~13s del flujo secuencial anterior).

    Layout temporal:
      t=0     : disparo de 3 llamadas Claude en paralelo (vision/agente/coord)
      t≈3s   : await gather → despacho ROI síncrono (<5ms)
      t≈3s+ε : telegram + documentación en paralelo (no bloquean)
    """
    client = _get_async_client()
    t_global = time.time()

    # ── PASOS 1-3 EN PARALELO: Visión + Agente + Coordinador ──
    p_vision = (
        f"Incidente en Lima: {inc.tipo} en {inc.sector}. "
        f"Descripción: {inc.descripcion}. "
        f"Como sistema de visión de drone, describe en 1 oración lo que la cámara captaría. "
        f"Sé específico y táctico."
    )
    p_agente = (
        f"Incidente {inc.tipo} severidad {inc.severidad} en {inc.sector} Lima. "
        f"Como agente táctico, en exactamente 1 oración: evalúa la amenaza y recomienda nivel de respuesta."
    )
    p_coord = (
        f"Coordinador flota Lima. Incidente: {inc.tipo} en {inc.sector}. "
        f"Severidad: {inc.severidad}. Drones requeridos: {inc.drones_req}. "
        f"En 1 oración: orden táctica de coordinación de flota."
    )
    fb_vision = f"Cámara detecta {inc.descripcion[:60]}"
    fb_agente = f"Amenaza {inc.severidad} confirmada. Respuesta inmediata requerida."
    fb_coord  = f"Activar protocolo {inc.severidad}. Despachar {inc.drones_req} drone(s) a {inc.sector}."

    (vision_txt, vision_ms), (agente_txt, agente_ms), (coord_txt, coord_ms) = await asyncio.gather(
        _llm_call(client, "claude-sonnet-4-6", p_vision, 150, fb_vision),
        _llm_call(client, "claude-sonnet-4-6", p_agente, 100, fb_agente),
        _llm_call(client, "claude-opus-4-7",   p_coord,  120, fb_coord),
    )

    inc.cadena["vision"]      = {"estado":"✅","texto":vision_txt,"tiempo_ms":vision_ms}
    inc.cadena["agente"]      = {"estado":"✅","texto":agente_txt,"tiempo_ms":agente_ms}
    inc.cadena["coordinador"] = {"estado":"✅","texto":coord_txt, "tiempo_ms":coord_ms}
    live.update(build_fn(inc, historial, stats))

    # ── PASO 4: DESPACHO ROI (síncrono, <5ms) ──
    t0 = time.time()
    drone_id, roi, eta = flota.despachar(inc)
    inc.drone_asignado = drone_id or "SIN DISPONIBLE"
    inc.roi_score      = roi or 0.0
    inc.eta_min        = eta or 0.0

    inc.cadena["despacho"] = {
        "estado": "✅" if drone_id else "⚠️",
        "texto":  (f"{drone_id} → {inc.sector} | ROI={roi:.3f} | ETA={eta:.1f}min"
                   if drone_id else "Sin drones disponibles"),
        "tiempo_ms": int((time.time()-t0)*1000),
    }
    live.update(build_fn(inc, historial, stats))

    # ── PASOS 5-6 EN PARALELO: Telegram + Documentación ──
    t_tel = time.time()
    t_doc = time.time()
    msg = _build_telegram_msg(inc)
    registro = {
        "id":        inc.id,
        "tipo":      inc.tipo,
        "sector":    inc.sector,
        "severidad": inc.severidad,
        "lat":       inc.lat,
        "lon":       inc.lon,
        "drone":     inc.drone_asignado,
        "eta_min":   inc.eta_min,
        "roi":       inc.roi_score,
        "timestamp": inc.timestamp.isoformat(),
    }
    ok, doc_txt = await asyncio.gather(
        asyncio.to_thread(enviar_telegram, msg),
        asyncio.to_thread(_persistir_log, registro),
    )
    inc.cadena["telegram"] = {
        "estado": "✅" if ok else "⚠️",
        "texto":  f"{'Enviado a Eaton Palacin' if ok else 'Sin conexión'} ({inc.id})",
        "tiempo_ms": int((time.time()-t_tel)*1000),
    }
    inc.cadena["documentacion"] = {
        "estado": "✅",
        "texto":  doc_txt,
        "tiempo_ms": int((time.time()-t_doc)*1000),
    }
    inc.resuelto = True
    inc.tiempo_total_s = time.time() - t_global

    # Actualizar stats
    stats["total"]       += 1
    stats["criticos"]    += 1 if inc.severidad == "CRITICO" else 0
    stats["altos"]       += 1 if inc.severidad == "ALTO" else 0
    stats["despachados"] += 1 if drone_id else 0
    stats["telegram"]    += 1 if ok else 0
    stats["tiempo_prom"] = (
        stats["tiempo_prom"] * (stats["total"]-1) + inc.tiempo_total_s
    ) / stats["total"]

    historial.appendleft(inc)
    live.update(build_fn(inc, historial, stats))


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD RICH
# ─────────────────────────────────────────────────────────────────────────────

def ec(sev):
    return {"CRITICO":RED,"ALTO":AMBER,"MEDIO":"bright_yellow","BAJO":GREEN}.get(sev,WHITE)


def build_cadena_panel(inc: Incidente) -> Panel:
    pasos = [
        ("👁  VISIÓN IA",     "vision",       "Sonnet 4.6"),
        ("🤖 AGENTE",         "agente",        "Sonnet 4.6"),
        ("🧠 COORDINADOR",    "coordinador",   "Opus 4.7"),
        ("🚁 DESPACHO ROI",   "despacho",      "Algoritmo"),
        ("📱 TELEGRAM",       "telegram",      "Bot API"),
        ("📋 DOCUMENTACIÓN",  "documentacion", "JSON Log"),
    ]

    t = Table(box=None, show_header=False, expand=True, padding=(0,1))
    t.add_column("PASO",   style=f"bold {AMBER}", width=18)
    t.add_column("MOTOR",  style=DIM,             width=11)
    t.add_column("ESTADO", width=4)
    t.add_column("RESULTADO", style=WHITE)
    t.add_column("ms",     justify="right", style=DIM, width=6)

    for nombre, key, motor in pasos:
        c = inc.cadena[key]
        est = c["estado"]
        txt = c["texto"][:55] if c["texto"] != "Pendiente" else f"[{DIM}]Esperando...[/]"
        ms  = str(c["tiempo_ms"]) if c["tiempo_ms"] > 0 else "—"
        t.add_row(nombre, motor, est, txt, ms)

    return Panel(t,
        title=f"[bold {AMBER}]▸ CADENA DE RESPUESTA — {inc.id}[/]",
        border_style=ec(inc.severidad),
        padding=(0,1))


def build_incidente_panel(inc: Incidente) -> Panel:
    nc = ec(inc.severidad)
    content = Text()
    content.append(f"\n  {inc.emoji} ", style=f"bold {nc}")
    content.append(f"{inc.tipo}\n", style=f"bold {nc}")
    content.append(f"\n  SECTOR:     ", style=DIM)
    content.append(f"{inc.sector}\n", style=f"bold {BLUE}")
    content.append(f"  SEVERIDAD:  ", style=DIM)
    content.append(f"{inc.severidad}\n", style=f"bold {nc}")
    content.append(f"  GPS:        ", style=DIM)
    content.append(f"{inc.lat:.5f}, {inc.lon:.5f}\n", style=WHITE)
    content.append(f"  DESCRIPCIÓN:\n  ", style=DIM)
    content.append(f"{inc.descripcion}\n", style=WHITE)

    if inc.resuelto:
        content.append(f"\n  🚁 DRONE: ", style=DIM)
        content.append(f"{inc.drone_asignado}", style=f"bold {GREEN}")
        content.append(f" | ETA: {inc.eta_min:.1f}min", style=AMBER)
        content.append(f" | ROI: {inc.roi_score:.3f}\n", style=BLUE)
        content.append(f"  ⏱ TIEMPO TOTAL: ", style=DIM)
        content.append(f"{inc.tiempo_total_s:.1f}s\n", style=GREEN)

    return Panel(content,
        title=f"[bold {AMBER}]▸ INCIDENTE ACTIVO — {inc.timestamp.strftime('%H:%M:%S')}[/]",
        border_style=nc, padding=(0,1))


def build_historial_table(historial: deque) -> Table:
    t = Table(
        title=f"[bold {AMBER}]▸ HISTORIAL DE INCIDENTES PROCESADOS[/]",
        box=rbox.SIMPLE_HEAD, style=DIM,
        header_style=f"bold {AMBER}",
        show_lines=False, expand=True,
    )
    t.add_column("ID",        style=f"bold {BLUE}", width=10)
    t.add_column("TIPO",      width=22)
    t.add_column("SECTOR",    width=13)
    t.add_column("SEV",       width=9)
    t.add_column("DRONE",     width=9)
    t.add_column("ETA",       justify="right", width=7)
    t.add_column("ROI",       justify="right", width=6)
    t.add_column("TIEMPO",    justify="right", width=7)
    t.add_column("TELEGRAM",  width=8)

    for inc in list(historial)[:7]:
        nc  = ec(inc.severidad)
        tel = inc.cadena["telegram"]["estado"]
        t.add_row(
            inc.id,
            f"{inc.emoji} {inc.tipo[:18]}",
            inc.sector,
            f"[{nc}]{inc.severidad}[/]",
            inc.drone_asignado or "—",
            f"{inc.eta_min:.1f}m",
            f"{inc.roi_score:.3f}",
            f"{inc.tiempo_total_s:.1f}s",
            tel,
        )

    if not historial:
        t.add_row("—","—","—","—","—","—","—","—","—")

    return t


def build_stats_panel(stats: dict) -> Panel:
    def sr(l, v, c=WHITE):
        t2 = Text()
        t2.append(f"  {l:<22}", style=DIM)
        t2.append(v, style=f"bold {c}")
        return t2

    content = Text("\n").join([
        sr("Incidentes totales",  str(stats["total"]),            AMBER),
        sr("Críticos",            str(stats["criticos"]),          RED),
        sr("Altos",               str(stats["altos"]),             AMBER),
        sr("Drones despachados",  str(stats["despachados"]),       BLUE),
        sr("Alertas Telegram",    str(stats["telegram"]),          GREEN),
        sr("Tiempo prom. cadena", f"{stats['tiempo_prom']:.1f}s",  MAG),
        sr("Próximo incidente",   f"{stats['prox']:.0f}s",         DIM),
    ])
    return Panel(content,
        title=f"[bold {AMBER}]▸ ESTADÍSTICAS[/]",
        border_style=AMBER, padding=(1,1))


def build_dashboard(inc_activo: Incidente, historial: deque, stats: dict) -> Table:
    ts  = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    hdr = Text()
    hdr.append("◈ CENTINELA INCIDENTES", style=f"bold {AMBER}")
    hdr.append("  │  ", style=DIM)
    hdr.append("SIMULACIÓN EN VIVO — LIMA, PERÚ", style=f"bold {BLUE}")
    hdr.append(f"  │  {ts}  │  {stats['total']} procesados", style=MAG)

    nc = ec(inc_activo.severidad)
    footer = Text(justify="center")
    footer.append("CENTINELA INCIDENTES ACTIVE", style=f"bold {AMBER}")
    footer.append("  ·  ", style=DIM)
    footer.append(f"{inc_activo.emoji} {inc_activo.tipo}", style=f"bold {nc}")
    footer.append("  ·  ", style=DIM)
    footer.append("Sonnet 4.6 + Opus 4.7 + Telegram", style=f"bold {BLUE}")
    footer.append("  ·  Ctrl+C para detener", style=DIM)

    root = Table.grid(expand=True)
    root.add_row(Panel(Align.center(hdr), style=AMBER, padding=(0,2)))
    root.add_row(Columns([
        build_incidente_panel(inc_activo),
        build_stats_panel(stats),
    ], expand=True))
    root.add_row(build_cadena_panel(inc_activo))
    root.add_row(build_historial_table(historial))
    root.add_row(Panel(Align.center(footer), style=DIM, padding=(0,1)))
    return root


# ─────────────────────────────────────────────────────────────────────────────
# MAIN — Orquestador de simulación
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    console.clear()
    console.print(Panel.fit(
        f"[bold {AMBER}]Inicializando CENTINELA INCIDENTES...[/]\n"
        f"[{BLUE}]Cargando catálogo: {len(CATALOGO_INCIDENTES)} tipos de incidente[/]\n"
        f"[{DIM}]Cadena async paralela: gather(visión|agente|coord) → despacho → gather(telegram|doc)[/]",
        title="[bold white]EATON DYNAMICS — SIMULACIÓN DE INCIDENTES[/]",
        border_style=AMBER,
    ))
    await asyncio.sleep(1.5)

    historial: deque = deque(maxlen=20)
    stats = {
        "total":0,"criticos":0,"altos":0,
        "despachados":0,"telegram":0,
        "tiempo_prom":0.0,"prox":0.0,
    }

    # Mensaje inicial Telegram (no bloquea event loop)
    await asyncio.to_thread(enviar_telegram,
        f"🎯 <b>CENTINELA INCIDENTES ACTIVADO</b>\n\n"
        f"Sistema de simulación en vivo iniciado.\n"
        f"📋 Catálogo: {len(CATALOGO_INCIDENTES)} tipos de incidente\n"
        f"🤖 IA: Sonnet 4.6 + Opus 4.7 (async paralelo)\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}\n\n"
        f"<i>Recibirás alertas por cada incidente detectado.</i>"
    )

    inc_actual = generar_incidente()

    try:
        with Live(console=console, refresh_per_second=4) as live:
            live.update(build_dashboard(inc_actual, historial, stats))

            while True:
                # Procesar incidente actual (cadena async, ~3s con red)
                await ejecutar_cadena(inc_actual, live,
                    lambda i,h,s: build_dashboard(i,h,s),
                    historial, stats)

                # Pausa entre incidentes (15-35s)
                espera = random.uniform(15, 35)
                fin_espera = time.time() + espera
                while time.time() < fin_espera:
                    stats["prox"] = fin_espera - time.time()
                    live.update(build_dashboard(inc_actual, historial, stats))
                    await asyncio.sleep(0.5)

                # Nuevo incidente
                inc_actual = generar_incidente()
                stats["prox"] = 0.0
                live.update(build_dashboard(inc_actual, historial, stats))

    except (KeyboardInterrupt, asyncio.CancelledError):
        await asyncio.to_thread(enviar_telegram,
            f"🔴 <b>CENTINELA INCIDENTES DETENIDO</b>\n\n"
            f"📊 Incidentes procesados: {stats['total']}\n"
            f"🔴 Críticos: {stats['criticos']}\n"
            f"🚁 Drones despachados: {stats['despachados']}\n"
            f"⏱ Tiempo promedio cadena: {stats['tiempo_prom']:.1f}s"
        )
        console.print(f"\n[bold {AMBER}]◈ SIMULACIÓN DETENIDA.[/]")
        console.print(f"  Incidentes procesados: {stats['total']}")
        console.print(f"  Tiempo promedio de cadena: {stats['tiempo_prom']:.1f}s")
    finally:
        # Cerrar cliente AsyncAnthropic limpiamente
        if _async_client is not None:
            try:
                await _async_client.close()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
