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

import os, time, math, random, json, re
from datetime import datetime
from collections import deque
from typing import Optional
import urllib.request
import urllib.parse
import anthropic

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

def ejecutar_cadena(inc: Incidente, live, build_fn, historial, stats):
    """
    Ejecuta la cadena completa de respuesta al incidente.
    Cada paso actualiza el dashboard en tiempo real.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY) if ANTHROPIC_KEY else None

    # ── PASO 1: VISIÓN IA ──
    t0 = time.time()
    time.sleep(0.3)
    if client:
        try:
            r = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=150,
                messages=[{"role":"user","content":
                    f"Incidente en Lima: {inc.tipo} en {inc.sector}. "
                    f"Descripción: {inc.descripcion}. "
                    f"Como sistema de visión de drone, describe en 1 oración lo que la cámara captaría. "
                    f"Sé específico y táctico."
                }],
            )
            vision_txt = r.content[0].text.strip()[:100]
        except:
            vision_txt = f"Cámara detecta {inc.descripcion[:60]}"
    else:
        vision_txt = f"Cámara detecta {inc.descripcion[:60]}"

    inc.cadena["vision"] = {
        "estado": "✅",
        "texto":  vision_txt,
        "tiempo_ms": int((time.time()-t0)*1000),
    }
    live.update(build_fn(inc, historial, stats))
    time.sleep(0.2)

    # ── PASO 2: AGENTE TÁCTICO ──
    t0 = time.time()
    if client:
        try:
            r = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=100,
                messages=[{"role":"user","content":
                    f"Incidente {inc.tipo} severidad {inc.severidad} en {inc.sector} Lima. "
                    f"Como agente táctico, en exactamente 1 oración: evalúa la amenaza y recomienda nivel de respuesta."
                }],
            )
            agente_txt = r.content[0].text.strip()[:100]
        except:
            agente_txt = f"Amenaza {inc.severidad} confirmada. Respuesta inmediata requerida."
    else:
        agente_txt = f"Amenaza {inc.severidad} confirmada. Respuesta inmediata requerida."

    inc.cadena["agente"] = {
        "estado": "✅",
        "texto":  agente_txt,
        "tiempo_ms": int((time.time()-t0)*1000),
    }
    live.update(build_fn(inc, historial, stats))
    time.sleep(0.2)

    # ── PASO 3: COORDINADOR OPUS 4.7 ──
    t0 = time.time()
    if client:
        try:
            r = client.messages.create(
                model="claude-opus-4-7",
                max_tokens=120,
                messages=[{"role":"user","content":
                    f"Coordinador flota Lima. Incidente: {inc.tipo} en {inc.sector}. "
                    f"Severidad: {inc.severidad}. Drones requeridos: {inc.drones_req}. "
                    f"En 1 oración: orden táctica de coordinación de flota."
                }],
            )
            coord_txt = r.content[0].text.strip()[:100]
        except:
            coord_txt = f"Activar protocolo {inc.severidad}. Despachar {inc.drones_req} drone(s) a {inc.sector}."
    else:
        coord_txt = f"Activar protocolo {inc.severidad}. Despachar {inc.drones_req} drone(s) a {inc.sector}."

    inc.cadena["coordinador"] = {
        "estado": "✅",
        "texto":  coord_txt,
        "tiempo_ms": int((time.time()-t0)*1000),
    }
    live.update(build_fn(inc, historial, stats))
    time.sleep(0.2)

    # ── PASO 4: DESPACHO ROI ──
    t0 = time.time()
    drone_id, roi, eta = flota.despachar(inc)
    inc.drone_asignado  = drone_id or "SIN DISPONIBLE"
    inc.roi_score       = roi
    inc.eta_min         = eta

    dispatch_txt = (
        f"{drone_id} → {inc.sector} | ROI={roi:.3f} | ETA={eta:.1f}min"
        if drone_id else "Sin drones disponibles"
    )
    inc.cadena["despacho"] = {
        "estado": "✅" if drone_id else "⚠️",
        "texto":  dispatch_txt,
        "tiempo_ms": int((time.time()-t0)*1000),
    }
    live.update(build_fn(inc, historial, stats))
    time.sleep(0.2)

    # ── PASO 5: TELEGRAM ──
    t0 = time.time()
    sev_emoji = {"CRITICO":"🔴","ALTO":"🟠","MEDIO":"🟡","BAJO":"🟢"}.get(inc.severidad,"⚪")
    msg = (
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
    ok = enviar_telegram(msg)
    inc.cadena["telegram"] = {
        "estado": "✅" if ok else "⚠️",
        "texto":  f"{'Enviado a Eaton Palacin' if ok else 'Sin conexión'} ({inc.id})",
        "tiempo_ms": int((time.time()-t0)*1000),
    }
    live.update(build_fn(inc, historial, stats))
    time.sleep(0.2)

    # ── PASO 6: DOCUMENTACIÓN ──
    t0 = time.time()
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
    log_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "reportes", "incidentes_log.json"
    )
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    try:
        try:
            with open(log_path, "r") as f:
                logs = json.load(f)
        except:
            logs = []
        logs.append(registro)
        with open(log_path, "w") as f:
            json.dump(logs[-100:], f, indent=2)
        doc_txt = f"Guardado en incidentes_log.json ({len(logs)} registros)"
    except Exception as e:
        doc_txt = f"Log en memoria ({inc.id})"

    inc.cadena["documentacion"] = {
        "estado": "✅",
        "texto":  doc_txt,
        "tiempo_ms": int((time.time()-t0)*1000),
    }
    inc.resuelto = True
    inc.tiempo_total_s = (datetime.now() - inc.timestamp).total_seconds()

    # Actualizar stats
    stats["total"]      += 1
    stats["criticos"]   += 1 if inc.severidad == "CRITICO" else 0
    stats["altos"]      += 1 if inc.severidad == "ALTO" else 0
    stats["despachados"]+= 1 if drone_id else 0
    stats["telegram"]   += 1 if ok else 0
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

def main():
    console.clear()
    console.print(Panel.fit(
        f"[bold {AMBER}]Inicializando CENTINELA INCIDENTES...[/]\n"
        f"[{BLUE}]Cargando catálogo: {len(CATALOGO_INCIDENTES)} tipos de incidente[/]\n"
        f"[{DIM}]Cadena: Visión → Agente → Coordinador → Despacho → Telegram → Doc[/]",
        title="[bold white]EATON DYNAMICS — SIMULACIÓN DE INCIDENTES[/]",
        border_style=AMBER,
    ))
    time.sleep(1.5)

    historial: deque = deque(maxlen=20)
    stats = {
        "total":0,"criticos":0,"altos":0,
        "despachados":0,"telegram":0,
        "tiempo_prom":0.0,"prox":0.0,
    }

    # Mensaje inicial Telegram
    enviar_telegram(
        f"🎯 <b>CENTINELA INCIDENTES ACTIVADO</b>\n\n"
        f"Sistema de simulación en vivo iniciado.\n"
        f"📋 Catálogo: {len(CATALOGO_INCIDENTES)} tipos de incidente\n"
        f"🤖 IA: Sonnet 4.6 + Opus 4.7\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}\n\n"
        f"<i>Recibirás alertas por cada incidente detectado.</i>"
    )

    # Primer incidente inmediato
    inc_actual = generar_incidente()

    try:
        with Live(console=console, refresh_per_second=4, screen=True) as live:
            live.update(build_dashboard(inc_actual, historial, stats))

            while True:
                # Procesar incidente actual
                ejecutar_cadena(inc_actual, live,
                    lambda i,h,s: build_dashboard(i,h,s),
                    historial, stats)

                # Pausa entre incidentes (15-35 segundos)
                espera = random.uniform(15, 35)
                fin_espera = time.time() + espera

                while time.time() < fin_espera:
                    stats["prox"] = fin_espera - time.time()
                    live.update(build_dashboard(inc_actual, historial, stats))
                    time.sleep(0.5)

                # Nuevo incidente
                inc_actual = generar_incidente()
                stats["prox"] = 0.0
                live.update(build_dashboard(inc_actual, historial, stats))

    except KeyboardInterrupt:
        enviar_telegram(
            f"🔴 <b>CENTINELA INCIDENTES DETENIDO</b>\n\n"
            f"📊 Incidentes procesados: {stats['total']}\n"
            f"🔴 Críticos: {stats['criticos']}\n"
            f"🚁 Drones despachados: {stats['despachados']}\n"
            f"⏱ Tiempo promedio cadena: {stats['tiempo_prom']:.1f}s"
        )
        console.print(f"\n[bold {AMBER}]◈ SIMULACIÓN DETENIDA.[/]")
        console.print(f"  Incidentes procesados: {stats['total']}")
        console.print(f"  Tiempo promedio de cadena: {stats['tiempo_prom']:.1f}s")


if __name__ == "__main__":
    main()
