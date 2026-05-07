"""
PROYECTO CENTINELA — ALERTAS TELEGRAM v1.0
Notificaciones automáticas al celular cuando Claude detecta riesgo ALTO/CRÍTICO
Stack: python-telegram-bot + PyBullet + Claude Vision + Rich terminal

Alertas que disparan notificación:
  - Nivel de riesgo ALTO o CRÍTICO en visión IA
  - Batería de drone < 15% (EMERGENCY)
  - Pérdida de señal GPS (RSSI < -90dBm)
  - Anomalía de motor detectada
  - Intrusión de perímetro
"""

import os, sys, time, math, random, base64, json, re
from datetime import datetime
from collections import deque
from io import BytesIO
from typing import Optional
import urllib.request
import urllib.parse
import threading

import pybullet as pb
import pybullet_data
import numpy as np

try:
    from PIL import Image, ImageDraw
    PIL_OK = True
except ImportError:
    PIL_OK = False

try:
    import cv2
    CV2_OK = True
except ImportError:
    CV2_OK = False

import anthropic

from rich import box as rbox
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich.align import Align

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN — TUS CREDENCIALES
# ─────────────────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN  = "8676501832:AAEYlMd3GogjL-tubZzOFUSfudhx_loh8lk"
TELEGRAM_CHAT_ID = "1638287560"
ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")

# Cooldown entre alertas del mismo tipo (segundos)
COOLDOWN_BATERIA  = 120
COOLDOWN_VISION   = 60
COOLDOWN_MOTOR    = 180
COOLDOWN_GPS      = 90

# Física
NUM_DRONES    = 6
REF_LAT       = -12.0464
REF_LON       = -77.0428
M_PER_DEG_LAT = 111_320.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(REF_LAT))
DRONE_MASS_KG = 1.2
GRAVITY       = 9.81
HOVER_THRUST  = DRONE_MASS_KG * GRAVITY
BATTERY_DRAIN = 0.008
TICK_DT       = 0.05
VISION_CADA   = 8   # segundos entre análisis de visión

SECTORES = [
    {"nombre":"Miraflores",  "radio":150,"angulo_base":0.0,   "alt":80},
    {"nombre":"San Isidro",  "radio":200,"angulo_base":1.047, "alt":90},
    {"nombre":"Barranco",    "radio":130,"angulo_base":2.094, "alt":70},
    {"nombre":"Surquillo",   "radio":180,"angulo_base":3.141, "alt":85},
    {"nombre":"La Victoria", "radio":160,"angulo_base":4.189, "alt":75},
    {"nombre":"Lince",       "radio":140,"angulo_base":5.236, "alt":80},
]

AMBER = "bright_yellow"
BLUE  = "bright_cyan"
RED   = "bright_red"
GREEN = "bright_green"
DIM   = "dim white"
MAG   = "magenta"
WHITE = "white"

console = Console()

# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM API — Envío de mensajes y fotos
# ─────────────────────────────────────────────────────────────────────────────

class TelegramBot:
    """
    Cliente Telegram sin dependencias externas.
    Usa urllib directamente para máxima compatibilidad.
    """
    BASE = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

    def enviar_mensaje(self, texto: str, parse_mode: str = "HTML") -> bool:
        try:
            data = urllib.parse.urlencode({
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       texto,
                "parse_mode": parse_mode,
            }).encode()
            req = urllib.request.Request(
                f"{self.BASE}/sendMessage",
                data=data,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read()).get("ok", False)
        except Exception as e:
            return False

    def enviar_foto(self, imagen_bytes: bytes, caption: str = "") -> bool:
        """Envía una foto con caption al chat de Telegram."""
        try:
            import io
            boundary = b"----WebKitFormBoundary7MA4YWxkTrZu0gW"
            body = (
                b"--" + boundary + b"\r\n"
                b'Content-Disposition: form-data; name="chat_id"\r\n\r\n' +
                TELEGRAM_CHAT_ID.encode() + b"\r\n"
                b"--" + boundary + b"\r\n"
                b'Content-Disposition: form-data; name="caption"\r\n\r\n' +
                caption.encode()[:200] + b"\r\n"
                b"--" + boundary + b"\r\n"
                b'Content-Disposition: form-data; name="photo"; filename="alerta.jpg"\r\n'
                b"Content-Type: image/jpeg\r\n\r\n" +
                imagen_bytes + b"\r\n"
                b"--" + boundary + b"--\r\n"
            )
            req = urllib.request.Request(
                f"{self.BASE}/sendPhoto",
                data=body,
                method="POST",
                headers={"Content-Type": f"multipart/form-data; boundary={boundary.decode()}"},
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read()).get("ok", False)
        except Exception:
            return self.enviar_mensaje(caption)

    def test_conexion(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.BASE}/getMe")
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())
                return data.get("ok", False)
        except:
            return False


bot = TelegramBot()

# ─────────────────────────────────────────────────────────────────────────────
# GESTOR DE ALERTAS TELEGRAM — Con cooldown anti-spam
# ─────────────────────────────────────────────────────────────────────────────

class AlertaManager:
    """
    Gestiona el envío de alertas a Telegram con cooldown por tipo.
    Evita spam de mensajes repetidos.
    """
    def __init__(self):
        self._ultimo_envio: dict = {}
        self._total_enviadas: int = 0
        self._log: deque = deque(maxlen=20)
        self._lock = threading.Lock()

    def _puede_enviar(self, key: str, cooldown: int) -> bool:
        now = time.time()
        ultimo = self._ultimo_envio.get(key, 0)
        return now - ultimo >= cooldown

    def _registrar_envio(self, key: str):
        self._ultimo_envio[key] = time.time()
        self._total_enviadas += 1

    def alerta_bateria(self, drone_id: str, bat: float, sector: str) -> bool:
        key = f"bat_{drone_id}"
        if not self._puede_enviar(key, COOLDOWN_BATERIA):
            return False

        ts  = datetime.now().strftime("%H:%M:%S")
        msg = (
            f"🚨 <b>ALERTA CENTINELA — BATERÍA CRÍTICA</b>\n\n"
            f"🤖 <b>Drone:</b> {drone_id}\n"
            f"📍 <b>Sector:</b> {sector}\n"
            f"🔋 <b>Batería:</b> {bat:.1f}%\n"
            f"⚠️ <b>Estado:</b> RETORNO INMEDIATO REQUERIDO\n"
            f"🕐 <b>Hora:</b> {ts}\n\n"
            f"<i>Sistema CENTINELA — EATON DYNAMICS · Lima, Perú</i>"
        )
        ok = bot.enviar_mensaje(msg)
        if ok:
            self._registrar_envio(key)
            with self._lock:
                self._log.appendleft({
                    "hora": ts, "tipo": "BATERÍA CRÍTICA",
                    "drone": drone_id, "enviada": True,
                })
        return ok

    def alerta_gps(self, drone_id: str, rssi: int, sector: str) -> bool:
        key = f"gps_{drone_id}"
        if not self._puede_enviar(key, COOLDOWN_GPS):
            return False

        ts  = datetime.now().strftime("%H:%M:%S")
        msg = (
            f"📡 <b>ALERTA CENTINELA — SEÑAL GPS DEGRADADA</b>\n\n"
            f"🤖 <b>Drone:</b> {drone_id}\n"
            f"📍 <b>Sector:</b> {sector}\n"
            f"📶 <b>RSSI:</b> {rssi} dBm\n"
            f"⚠️ <b>Estado:</b> MODO AUTÓNOMO ACTIVADO\n"
            f"🕐 <b>Hora:</b> {ts}\n\n"
            f"<i>Sistema CENTINELA — EATON DYNAMICS · Lima, Perú</i>"
        )
        ok = bot.enviar_mensaje(msg)
        if ok:
            self._registrar_envio(key)
            with self._lock:
                self._log.appendleft({
                    "hora": ts, "tipo": "GPS DEGRADADO",
                    "drone": drone_id, "enviada": True,
                })
        return ok

    def alerta_motor(self, drone_id: str, health: float, sector: str) -> bool:
        key = f"motor_{drone_id}"
        if not self._puede_enviar(key, COOLDOWN_MOTOR):
            return False

        ts  = datetime.now().strftime("%H:%M:%S")
        msg = (
            f"⚙️ <b>ALERTA CENTINELA — ANOMALÍA DE MOTOR</b>\n\n"
            f"🤖 <b>Drone:</b> {drone_id}\n"
            f"📍 <b>Sector:</b> {sector}\n"
            f"🔧 <b>Motor Health:</b> {health:.3f}\n"
            f"⚠️ <b>Estado:</b> INSPECCIÓN REQUERIDA\n"
            f"🕐 <b>Hora:</b> {ts}\n\n"
            f"<i>Sistema CENTINELA — EATON DYNAMICS · Lima, Perú</i>"
        )
        ok = bot.enviar_mensaje(msg)
        if ok:
            self._registrar_envio(key)
            with self._lock:
                self._log.appendleft({
                    "hora": ts, "tipo": "MOTOR ANOMALÍA",
                    "drone": drone_id, "enviada": True,
                })
        return ok

    def alerta_vision(self, sector: str, nivel: str, descripcion: str,
                      anomalia: Optional[str], accion: Optional[str],
                      imagen_bytes: Optional[bytes] = None) -> bool:
        key = f"vision_{sector}"
        if not self._puede_enviar(key, COOLDOWN_VISION):
            return False

        emoji = {"ALTO":"🟠","CRITICO":"🔴"}.get(nivel, "⚪")
        ts    = datetime.now().strftime("%H:%M:%S")
        msg   = (
            f"{emoji} <b>ALERTA CENTINELA — VISIÓN IA</b>\n\n"
            f"📍 <b>Sector:</b> {sector}\n"
            f"⚠️ <b>Nivel de riesgo:</b> {nivel}\n"
            f"👁 <b>Análisis:</b> {descripcion[:200]}\n"
        )
        if anomalia:
            msg += f"🚨 <b>Anomalía:</b> {anomalia[:150]}\n"
        if accion:
            msg += f"→ <b>Acción:</b> {accion[:150]}\n"
        msg += f"🕐 <b>Hora:</b> {ts}\n\n"
        msg += f"<i>Claude Sonnet 4.6 Vision · EATON DYNAMICS · Lima, Perú</i>"

        if imagen_bytes:
            ok = bot.enviar_foto(imagen_bytes, caption=f"🚨 CENTINELA | {sector} | RIESGO {nivel}")
            if ok:
                bot.enviar_mensaje(msg)
        else:
            ok = bot.enviar_mensaje(msg)

        if ok:
            self._registrar_envio(key)
            with self._lock:
                self._log.appendleft({
                    "hora": ts, "tipo": f"VISIÓN {nivel}",
                    "drone": sector, "enviada": True,
                })
        return ok

    @property
    def total(self) -> int:
        return self._total_enviadas

    @property
    def log(self) -> list:
        with self._lock:
            return list(self._log)


alertas = AlertaManager()

# ─────────────────────────────────────────────────────────────────────────────
# FÍSICA PyBullet — Flota de 6 drones
# ─────────────────────────────────────────────────────────────────────────────

def init_physics():
    c = pb.connect(pb.DIRECT)
    pb.setGravity(0, 0, -GRAVITY, physicsClientId=c)
    pb.setTimeStep(TICK_DT, physicsClientId=c)
    pb.setAdditionalSearchPath(pybullet_data.getDataPath())
    pb.loadURDF("plane.urdf", physicsClientId=c)
    bodies = []
    for i, s in enumerate(SECTORES):
        r = s["radio"] * 0.3
        col = pb.createCollisionShape(pb.GEOM_BOX,
              halfExtents=[0.15,0.15,0.04], physicsClientId=c)
        vis = pb.createVisualShape(pb.GEOM_BOX,
              halfExtents=[0.15,0.15,0.04],
              rgbaColor=[0.2,0.6,1.0,1.0], physicsClientId=c)
        body = pb.createMultiBody(baseMass=DRONE_MASS_KG,
               baseCollisionShapeIndex=col, baseVisualShapeIndex=vis,
               basePosition=[r*math.cos(s["angulo_base"]),
                              r*math.sin(s["angulo_base"]),
                              float(s["alt"])], physicsClientId=c)
        pb.changeDynamics(body,-1,linearDamping=0.15,
                          angularDamping=0.9,physicsClientId=c)
        bodies.append(body)
    return c, bodies


def step_drone(c, body, tick, battery, si):
    s = SECTORES[si]
    a = s["angulo_base"] + tick * (0.008 + si*0.001)
    tx, ty = s["radio"]*math.cos(a), s["radio"]*math.sin(a)
    talt   = float(s["alt"]) + 10*math.sin(tick*0.02)

    pos,_ = pb.getBasePositionAndOrientation(body, physicsClientId=c)
    thrust = HOVER_THRUST + 2.8*(talt-pos[2])
    thrust = max(0.0, min(thrust, HOVER_THRUST*2.8))

    rpms = []
    for off in [[.15,.15,.02],[-.15,.15,.02],[.15,-.15,.02],[-.15,-.15,.02]]:
        t_i = thrust/4+random.gauss(0,.03)
        t_i = max(0.0, t_i)
        pb.applyExternalForce(body,-1,[0,0,t_i],off,pb.LINK_FRAME,physicsClientId=c)
        rpms.append(min(8000,max(0,t_i/HOVER_THRUST*5000*4)))

    pb.applyExternalForce(body,-1,
        [0.8*(tx-pos[0]),0.8*(ty-pos[1]),0],
        [0,0,0],pb.WORLD_FRAME,physicsClientId=c)
    pb.applyExternalForce(body,-1,
        [random.gauss(0,.06),random.gauss(0,.06),0],
        [0,0,0],pb.LINK_FRAME,physicsClientId=c)
    pb.stepSimulation(physicsClientId=c)

    pos,_ = pb.getBasePositionAndOrientation(body,physicsClientId=c)
    vel,_ = pb.getBaseVelocity(body,physicsClientId=c)
    spd   = math.sqrt(sum(v**2 for v in vel))
    mh    = max(0.0,min(1.0,1.0-float(np.std(rpms))/5000*3))
    nbat  = max(0.0,battery-BATTERY_DRAIN*(thrust/HOVER_THRUST))
    dist  = math.sqrt(pos[0]**2+pos[1]**2)
    rssi  = int(max(-110,min(-40,-52-dist*.25+random.gauss(0,2))))

    return {
        "drone_id":    f"CNTL-{si+1:02d}",
        "sector":      s["nombre"],
        "lat":         round(REF_LAT+pos[1]/M_PER_DEG_LAT, 6),
        "lon":         round(REF_LON+pos[0]/M_PER_DEG_LON, 6),
        "altitude_m":  round(max(0,pos[2]), 1),
        "vel_ms":      round(spd, 2),
        "battery_pct": round(nbat, 1),
        "rssi_dbm":    rssi,
        "motor_health":round(mh, 3),
        "thrust_n":    round(thrust, 2),
    }, nbat


# ─────────────────────────────────────────────────────────────────────────────
# VISIÓN IA — Frame sintético + Claude Vision
# ─────────────────────────────────────────────────────────────────────────────

ESCENAS = [
    {"nombre":"Parque Kennedy","personas":random.randint(8,20),"vehiculos":1,"riesgo":"BAJO"},
    {"nombre":"Av. Larco",     "personas":random.randint(15,35),"vehiculos":12,"riesgo":"BAJO"},
    {"nombre":"Mercado LaVic", "personas":random.randint(30,70),"vehiculos":8,"riesgo":"MEDIO"},
    {"nombre":"Calle Barranco","personas":random.randint(2,6),  "vehiculos":3,"riesgo":"ALTO"},
    {"nombre":"Zona Industrial","personas":random.randint(0,3), "vehiculos":5,"riesgo":"MEDIO"},
    {"nombre":"Esquina Noche", "personas":random.randint(4,8),  "vehiculos":2,"riesgo":"ALTO"},
]

def generar_frame(escena_idx: int) -> tuple:
    """Genera frame sintético y retorna (numpy_array, bytes_jpeg)."""
    if not PIL_OK:
        arr = np.zeros((240,320,3), dtype=np.uint8)
        return arr, None

    e   = ESCENAS[escena_idx % len(ESCENAS)]
    W,H = 320, 240
    bg  = (15,20,30) if "Noche" in e["nombre"] else (70,110,70)
    img = Image.new("RGB",(W,H),bg)
    d   = ImageDraw.Draw(img)

    d.rectangle([0,0,W,H//3],fill=(5,10,20) if "Noche" in e["nombre"] else (120,170,210))
    d.rectangle([0,H//2,W,H],fill=(40,38,35))

    for _ in range(e["personas"]):
        x,y = random.randint(10,W-10), random.randint(H//2,H-10)
        d.ellipse([x-3,y-6,x+3,y+6],fill=(220,200,170))

    for _ in range(e["vehiculos"]):
        x,y = random.randint(10,W-50), random.randint(H//2+10,H-15)
        d.rectangle([x,y,x+35,y+16],fill=(random.randint(80,220),60,60))

    d.rectangle([0,0,W,32],fill=(0,0,0))
    d.text((4,4),  f"CENTINELA-EYE | {e['nombre']}", fill=(255,152,0))
    d.text((4,18), f"{datetime.now().strftime('%H:%M:%S')} | PERS:{e['personas']} VEH:{e['vehiculos']}",
           fill=(150,200,255))

    arr = np.array(img)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=70)
    return arr, buf.getvalue()


def analizar_vision(sector: str, imagen_bytes: Optional[bytes]) -> dict:
    if not ANTHROPIC_KEY or not imagen_bytes:
        e = random.choice(ESCENAS)
        return {
            "nivel_riesgo":       e["riesgo"],
            "descripcion":        f"Sector {sector}: {e['personas']} personas, {e['vehiculos']} vehículos.",
            "anomalia":           "Grupo sospechoso en esquina" if e["riesgo"]=="ALTO" else None,
            "accion_recomendada": "Aumentar vigilancia" if e["riesgo"]=="ALTO" else None,
            "confianza":          0.75,
        }

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    b64    = base64.b64encode(imagen_bytes).decode()
    try:
        r = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=300,
            system="""Eres CENTINELA-EYE. Analiza la imagen de drone de seguridad en Lima, Perú.
Responde SOLO JSON: {"nivel_riesgo":"BAJO|MEDIO|ALTO|CRITICO","descripcion":"<1-2 oraciones>","anomalia":"<texto o null>","accion_recomendada":"<texto o null>","confianza":0.0-1.0}""",
            messages=[{"role":"user","content":[
                {"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":b64}},
                {"type":"text","text":f"Analiza sector {sector}."},
            ]}],
        )
        text  = r.content[0].text.strip()
        match = re.search(r'\{.*?\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except:
        pass
    return {"nivel_riesgo":"BAJO","descripcion":"Sin análisis","anomalia":None,"accion_recomendada":None,"confianza":0.0}


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD RICH
# ─────────────────────────────────────────────────────────────────────────────

def ec(nivel):
    return {"BAJO":GREEN,"MEDIO":AMBER,"ALTO":RED,"CRITICO":RED,
            "EMERGENCY":RED,"WARNING":AMBER,"NOMINAL":GREEN}.get(nivel, WHITE)


def build_dashboard(telems, vision_actual, alertas_log, stats, tick):
    ts  = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    hdr = Text()
    hdr.append("◈ CENTINELA — ALERTAS TELEGRAM", style=f"bold {AMBER}")
    hdr.append("  │  ", style=DIM)
    hdr.append("NOTIFICACIONES EN TIEMPO REAL", style=f"bold {BLUE}")
    hdr.append(f"  │  {ts}  │  TICK #{tick:05d}", style=MAG)

    # Tabla de flota
    ft = Table(box=rbox.SIMPLE_HEAD, style=DIM,
               header_style=f"bold {AMBER}", show_lines=True, expand=True,
               title=f"[bold {AMBER}]▸ FLOTA — MONITOREO CON ALERTAS TELEGRAM[/]")
    ft.add_column("DRONE",  style=f"bold {BLUE}", width=10)
    ft.add_column("SECTOR", width=13)
    ft.add_column("BAT %",  justify="right", width=7)
    ft.add_column("ALT m",  justify="right", width=7)
    ft.add_column("RSSI",   justify="right", width=8)
    ft.add_column("MOT HS", justify="right", width=8)
    ft.add_column("ALERTA", width=18)

    for did, t in sorted(telems.items()):
        bat  = t["battery_pct"]
        bc   = RED if bat<15 else AMBER if bat<30 else GREEN
        mc   = RED if t["motor_health"]<0.70 else AMBER if t["motor_health"]<0.85 else GREEN
        rc   = RED if t["rssi_dbm"]<-90 else GREEN

        if bat < 15:
            estado = f"[bold {RED}]📱 TELEGRAM ENVIADO[/]"
        elif bat < 30:
            estado = f"[{AMBER}]▲ BAT BAJA[/]"
        elif t["rssi_dbm"] < -90:
            estado = f"[{RED}]📡 GPS ALERT[/]"
        elif t["motor_health"] < 0.75:
            estado = f"[{AMBER}]⚙ MOTOR[/]"
        else:
            estado = f"[{GREEN}]● NOMINAL[/]"

        ft.add_row(
            did, t["sector"],
            f"[{bc}]{bat:.1f}[/]",
            f"{t['altitude_m']:.1f}",
            f"[{rc}]{t['rssi_dbm']}[/]",
            f"[{mc}]{t['motor_health']:.3f}[/]",
            estado,
        )

    # Panel visión
    if vision_actual:
        nivel = vision_actual.get("nivel_riesgo","BAJO")
        nec   = ec(nivel)
        vc    = Text()
        vc.append(f"  SECTOR:  ", style=DIM)
        vc.append(f"{vision_actual.get('sector','?')}\n", style=f"bold {BLUE}")
        vc.append(f"  RIESGO:  ", style=DIM)
        vc.append(f"{nivel}\n", style=f"bold {nec}")
        vc.append(f"  ANÁLISIS: ", style=DIM)
        vc.append(f"{vision_actual.get('descripcion','')[:70]}\n", style=WHITE)
        anom = vision_actual.get("anomalia")
        if anom:
            vc.append(f"  📱 TELEGRAM ENVIADO: {anom[:50]}", style=f"bold {RED}")
    else:
        vc = Text("\n  Esperando primer análisis de visión...", style=DIM)

    vision_panel = Panel(vc,
        title=f"[bold {AMBER}]▸ VISIÓN IA — ÚLTIMO ANÁLISIS[/]",
        border_style=ec(vision_actual.get("nivel_riesgo","BAJO")) if vision_actual else DIM,
        padding=(0,1))

    # Panel stats telegram
    def sr(l,v,c=WHITE):
        t2=Text(); t2.append(f"  {l:<22}",style=DIM); t2.append(v,style=f"bold {c}"); return t2

    sc = Text("\n").join([
        sr("Alertas enviadas",    str(stats["enviadas"]),  AMBER),
        sr("Batería crítica",     str(stats["bat"]),       RED),
        sr("GPS degradado",       str(stats["gps"]),       RED),
        sr("Motor anomalía",      str(stats["motor"]),     AMBER),
        sr("Visión ALTO/CRÍTICO", str(stats["vision"]),    RED),
        sr("Bot Telegram",        "✓ ACTIVO" if stats["bot_ok"] else "✗ ERROR",
                                  GREEN if stats["bot_ok"] else RED),
        sr("Uptime",              f"{stats['uptime']:.0f}s", MAG),
    ])
    stats_panel = Panel(sc,
        title=f"[bold {AMBER}]▸ ESTADÍSTICAS TELEGRAM[/]",
        border_style=AMBER, padding=(1,1))

    # Log de alertas enviadas
    lt = Table(box=None, show_header=False, expand=True)
    lt.add_column("HORA", style=DIM, width=9)
    lt.add_column("TIPO", style=f"bold {AMBER}", width=16)
    lt.add_column("DRONE/SECTOR", style=f"bold {BLUE}", width=14)
    lt.add_column("ENVIADA", width=8)

    for entry in alertas_log[:6]:
        ok_c = GREEN if entry.get("enviada") else RED
        lt.add_row(
            entry["hora"],
            entry["tipo"][:14],
            entry["drone"][:12],
            f"[{ok_c}]{'📱 SÍ' if entry.get('enviada') else '✗ NO'}[/]",
        )
    if not alertas_log:
        lt.add_row("—","—","—","Sin alertas aún")

    log_panel = Panel(lt,
        title=f"[bold {AMBER}]▸ LOG ALERTAS TELEGRAM[/]",
        border_style=BLUE, padding=(0,1))

    # Footer
    footer = Text(justify="center")
    footer.append("CENTINELA TELEGRAM ACTIVE", style=f"bold {AMBER}")
    footer.append("  ·  ", style=DIM)
    footer.append(f"📱 {stats['enviadas']} alertas enviadas", style=f"bold {GREEN}")
    footer.append("  ·  ", style=DIM)
    footer.append("Eaton Palacin · Lima, Perú", style=f"bold {BLUE}")
    footer.append("  ·  Ctrl+C para detener", style=DIM)

    root = Table.grid(expand=True)
    root.add_row(Panel(Align.center(hdr), style=AMBER, padding=(0,2)))
    root.add_row(ft)
    root.add_row(Columns([vision_panel, stats_panel], expand=True))
    root.add_row(log_panel)
    root.add_row(Panel(Align.center(footer), style=DIM, padding=(0,1)))
    return root


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    console.clear()
    console.print(Panel.fit(
        f"[bold {AMBER}]Inicializando CENTINELA TELEGRAM...[/]\n"
        f"[{BLUE}]Verificando conexión con Telegram...[/]\n"
        f"[{DIM}]Bot: Centinela Alerta Bot | Chat: Eaton Palacin[/]",
        title="[bold white]EATON DYNAMICS — ALERTAS TELEGRAM[/]",
        border_style=AMBER,
    ))

    # Test de conexión
    if bot.test_conexion():
        console.print(f"[{GREEN}]✓ Telegram conectado[/]")
        bot.enviar_mensaje(
            "🚀 <b>CENTINELA ACTIVADO</b>\n\n"
            "Sistema de alertas táctica iniciado.\n"
            f"🤖 Drones: {NUM_DRONES} activos\n"
            f"📍 Lima, Perú — {datetime.now().strftime('%H:%M:%S')}\n\n"
            "<i>Recibirás alertas cuando se detecte riesgo ALTO o CRÍTICO.</i>"
        )
    else:
        console.print(f"[{AMBER}]⚠ Telegram sin conexión — verificar WARP[/]")

    time.sleep(1.5)

    # Inicializar física
    pb_client, pb_bodies = init_physics()
    batteries  = [100.0] * NUM_DRONES
    drone_ids  = [f"CNTL-{i+1:02d}" for i in range(NUM_DRONES)]
    telems     = {}
    tick       = 0
    escena_idx = 0
    vision_act = None
    ultimo_vis = 0.0
    start_time = time.time()

    stats = {
        "enviadas":0,"bat":0,"gps":0,"motor":0,
        "vision":0,"bot_ok":bot.test_conexion(),"uptime":0.0,
    }

    try:
        with Live(console=console, refresh_per_second=4, screen=True) as live:
            while True:
                tick += 1
                stats["uptime"] = time.time() - start_time

                # ── Física ──
                for i, (did, body) in enumerate(zip(drone_ids, pb_bodies)):
                    t, nbat = step_drone(pb_client, body, tick, batteries[i], i)
                    batteries[i] = nbat
                    telems[did]  = t

                    # ── Verificar alertas de drones ──
                    bat = t["battery_pct"]
                    if bat < 15.0:
                        if alertas.alerta_bateria(did, bat, t["sector"]):
                            stats["enviadas"] += 1
                            stats["bat"]      += 1

                    if t["rssi_dbm"] < -90:
                        if alertas.alerta_gps(did, t["rssi_dbm"], t["sector"]):
                            stats["enviadas"] += 1
                            stats["gps"]      += 1

                    if t["motor_health"] < 0.70:
                        if alertas.alerta_motor(did, t["motor_health"], t["sector"]):
                            stats["enviadas"] += 1
                            stats["motor"]    += 1

                # ── Análisis de visión periódico ──
                now = time.time()
                if now - ultimo_vis >= VISION_CADA:
                    ultimo_vis   = now
                    sector_actual = SECTORES[escena_idx % NUM_DRONES]["nombre"]
                    _, img_bytes = generar_frame(escena_idx)
                    analisis     = analizar_vision(sector_actual, img_bytes)
                    analisis["sector"] = sector_actual
                    vision_act   = analisis
                    escena_idx  += 1

                    nivel = analisis.get("nivel_riesgo","BAJO")
                    if nivel in ("ALTO","CRITICO"):
                        if alertas.alerta_vision(
                            sector_actual, nivel,
                            analisis.get("descripcion",""),
                            analisis.get("anomalia"),
                            analisis.get("accion_recomendada"),
                            img_bytes,
                        ):
                            stats["enviadas"] += 1
                            stats["vision"]   += 1

                # Render
                live.update(build_dashboard(
                    telems, vision_act, alertas.log, stats, tick))
                time.sleep(TICK_DT)

    except KeyboardInterrupt:
        pb.disconnect(physicsClientId=pb_client)
        bot.enviar_mensaje(
            f"🔴 <b>CENTINELA DETENIDO</b>\n\n"
            f"📊 Alertas enviadas: {stats['enviadas']}\n"
            f"⏱ Uptime: {stats['uptime']:.0f}s\n\n"
            f"<i>Sistema desactivado por operador.</i>"
        )
        console.print(f"\n[bold {AMBER}]◈ CENTINELA TELEGRAM DETENIDO.[/]")
        console.print(f"  📱 Alertas enviadas: {stats['enviadas']}")


if __name__ == "__main__":
    main()
