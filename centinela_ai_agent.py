"""
PROYECTO CENTINELA — AGENTE IA TÁCTICO v1.0
Claude como cerebro de decisiones tácticas para la flota de drones
Stack: Anthropic API + PyBullet + Rich terminal
"""

import math, time, random, os
from collections import deque
from datetime import datetime
from typing import Optional
import anthropic
import pybullet as pb
import pybullet_data
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

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
if not API_KEY:
    raise ValueError("ANTHROPIC_API_KEY no configurada. Ejecuta: $env:ANTHROPIC_API_KEY='sk-ant-...'")

client_ai = anthropic.Anthropic(api_key=API_KEY)

REF_LAT          = -12.0464
REF_LON          = -77.0428
M_PER_DEG_LAT    = 111_320.0
M_PER_DEG_LON    = 111_320.0 * math.cos(math.radians(REF_LAT))
DRONE_MASS_KG    = 1.2
GRAVITY          = 9.81
HOVER_THRUST     = DRONE_MASS_KG * GRAVITY
K_DRAG           = 0.15
BATTERY_DRAIN    = 0.012
TICK_DT          = 0.05
AI_EVERY_TICKS   = 60       # Consultar al agente cada N ticks (~3 segundos)

AMBER = "bright_yellow"
BLUE  = "bright_cyan"
RED   = "bright_red"
GREEN = "bright_green"
DIM   = "dim white"
MAG   = "magenta"
WHITE = "white"

console = Console()

# ─────────────────────────────────────────────────────────────────────────────
# MOTOR DE FÍSICA PyBullet
# ─────────────────────────────────────────────────────────────────────────────

def init_physics():
    c = pb.connect(pb.DIRECT)
    pb.setGravity(0, 0, -GRAVITY, physicsClientId=c)
    pb.setTimeStep(TICK_DT, physicsClientId=c)
    pb.setAdditionalSearchPath(pybullet_data.getDataPath())
    pb.loadURDF("plane.urdf", physicsClientId=c)
    col = pb.createCollisionShape(pb.GEOM_BOX,
          halfExtents=[0.15,0.15,0.04], physicsClientId=c)
    vis = pb.createVisualShape(pb.GEOM_BOX,
          halfExtents=[0.15,0.15,0.04],
          rgbaColor=[0.1,0.5,1.0,1.0], physicsClientId=c)
    body = pb.createMultiBody(baseMass=DRONE_MASS_KG,
           baseCollisionShapeIndex=col, baseVisualShapeIndex=vis,
           basePosition=[0.0,0.0,80.0], physicsClientId=c)
    pb.changeDynamics(body, -1, linearDamping=K_DRAG,
                      angularDamping=0.9, physicsClientId=c)
    return c, body


def physics_step(c, body, tick, battery):
    target_alt = 80.0 + 20.0 * math.sin(tick * 0.018)
    pos, _     = pb.getBasePositionAndOrientation(body, physicsClientId=c)
    vel, _     = pb.getBaseVelocity(body, physicsClientId=c)

    thrust_cmd = HOVER_THRUST + 2.8 * (target_alt - pos[2])
    thrust_cmd = max(0.0, min(thrust_cmd, HOVER_THRUST * 2.8))

    rpms = []
    for off in [[0.15,0.15,0.02],[-0.15,0.15,0.02],
                [0.15,-0.15,0.02],[-0.15,-0.15,0.02]]:
        t_i = thrust_cmd / 4.0 + random.gauss(0, 0.04)
        t_i = max(0.0, t_i)
        pb.applyExternalForce(body,-1,[0,0,t_i],off,pb.LINK_FRAME,physicsClientId=c)
        rpms.append(min(8000, max(0, t_i/HOVER_THRUST*5000*4)))

    pb.applyExternalForce(body,-1,
        [random.gauss(0,0.08),random.gauss(0,0.08),0],
        [0,0,0],pb.LINK_FRAME,physicsClientId=c)
    pb.applyExternalForce(body,-1,
        [0.35*math.cos(tick*0.013),0.35*math.sin(tick*0.013),0],
        [0,0,0],pb.WORLD_FRAME,physicsClientId=c)

    pb.stepSimulation(physicsClientId=c)

    pos, _ = pb.getBasePositionAndOrientation(body, physicsClientId=c)
    vel, _ = pb.getBaseVelocity(body, physicsClientId=c)
    speed  = math.sqrt(sum(v**2 for v in vel))

    rpm_var      = float(np.std(rpms)) / 5000.0
    motor_health = max(0.0, min(1.0, 1.0 - rpm_var * 3))
    new_battery  = max(0.0, battery - BATTERY_DRAIN*(thrust_cmd/HOVER_THRUST))
    dist_m       = math.sqrt(pos[0]**2 + pos[1]**2)
    rssi         = int(max(-110,min(-40,-55-dist_m*0.3+random.gauss(0,2))))
    lat          = REF_LAT + (pos[1]/M_PER_DEG_LAT)
    lon          = REF_LON + (pos[0]/M_PER_DEG_LON)

    return {
        "x_m":pos[0],"y_m":pos[1],"altitude_m":max(0,pos[2]),
        "lat":lat,"lon":lon,"vel_ms":speed,
        "battery_pct":new_battery,"rssi_dbm":rssi,
        "motor_rpm":rpms,"motor_health":motor_health,
        "thrust_N":thrust_cmd,
    }, new_battery


# ─────────────────────────────────────────────────────────────────────────────
# AGENTE IA TÁCTICO — Claude como cerebro
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Eres CENTINELA-AI, el cerebro táctico de un sistema de drones de seguridad 
desplegado en Lima, Perú. Tu misión es analizar telemetría en tiempo real y emitir 
decisiones tácticas precisas y concisas.

Recibirás datos de telemetría de un drone cuadricóptero. Debes:
1. Evaluar el estado operativo (NOMINAL / WARNING / CRITICAL / EMERGENCY)
2. Identificar la anomalía más crítica si existe
3. Emitir UNA orden táctica clara y ejecutable
4. Calcular el ROI de la decisión (beneficio vs costo operativo)

FORMATO DE RESPUESTA (siempre exactamente este formato JSON):
{
  "estado": "NOMINAL|WARNING|CRITICAL|EMERGENCY",
  "anomalia": "descripción breve o null",
  "orden": "orden táctica específica",
  "razon": "justificación en una línea",
  "roi_score": 0.0-1.0
}

Sé directo, técnico y sin redundancias. Máximo 15 palabras por campo."""


def consultar_agente(telem: dict, historial: list) -> dict:
    """
    Envía telemetría a Claude y obtiene decisión táctica.
    Retorna dict con la respuesta parseada.
    """
    contexto = f"""TELEMETRÍA ACTUAL — {datetime.now().strftime('%H:%M:%S')}
Drone: CNTL-SIM-01
Batería: {telem['battery_pct']:.1f}%
Altitud: {telem['altitude_m']:.1f}m
Velocidad: {telem['vel_ms']:.2f} m/s
RSSI: {telem['rssi_dbm']} dBm
Motor Health: {telem['motor_health']:.3f}
Empuje: {telem['thrust_N']:.1f}N
GPS: ({telem['lat']:.5f}, {telem['lon']:.5f})
RPMs: {[round(r) for r in telem['motor_rpm']]}

HISTORIAL RECIENTE (últimas 3 anomalías):
{chr(10).join(historial[-3:]) if historial else 'Sin anomalías previas'}

Analiza y emite tu decisión táctica en el formato JSON especificado."""

    try:
        response = client_ai.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": contexto}]
        )
        import json, re
        text = response.content[0].text
        # Extraer JSON de la respuesta
        match = re.search(r'\{.*?\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
        return {"estado":"ERROR","anomalia":"Parse error","orden":"Revisar conexión IA",
                "razon":"Respuesta no parseable","roi_score":0.0}
    except Exception as e:
        return {"estado":"ERROR","anomalia":str(e)[:40],
                "orden":"Verificar API key","razon":"Error de conexión",
                "roi_score":0.0}


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD RICH CON IA
# ─────────────────────────────────────────────────────────────────────────────

def estado_color(estado: str) -> str:
    return {
        "NOMINAL":   GREEN,
        "WARNING":   AMBER,
        "CRITICAL":  RED,
        "EMERGENCY": RED,
        "ERROR":     DIM,
    }.get(estado, WHITE)


def build_dashboard(telem, tick, ai_resp, ai_log, kpi, thinking):
    ts = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")

    # Header
    hdr = Text()
    hdr.append("◈ CENTINELA-AI", style=f"bold {AMBER}")
    hdr.append("  │  ", style=DIM)
    hdr.append("CLAUDE TACTICAL AGENT", style=f"bold {BLUE}")
    hdr.append("  │  ", style=DIM)
    hdr.append(ts, style=WHITE)
    hdr.append(f"  │  TICK #{tick:06d}", style=MAG)

    # Panel telemetría
    t_table = Table(box=None, show_header=False, expand=True, padding=(0,1))
    t_table.add_column("", style=DIM, width=20)
    t_table.add_column("", style=WHITE)
    t_table.add_column("", style=DIM, width=20)
    t_table.add_column("", style=WHITE)

    bc = RED if telem["battery_pct"]<20 else AMBER if telem["battery_pct"]<35 else GREEN
    mc = RED if telem["motor_health"]<0.70 else AMBER if telem["motor_health"]<0.85 else GREEN

    t_table.add_row("Batería",   f"[{bc}]{telem['battery_pct']:.1f}%[/]",
                    "Altitud",   f"{telem['altitude_m']:.1f}m")
    t_table.add_row("Velocidad", f"{telem['vel_ms']:.2f}m/s",
                    "Empuje",    f"{telem['thrust_N']:.1f}N")
    t_table.add_row("RSSI",      f"{telem['rssi_dbm']}dBm",
                    "Motor HS",  f"[{mc}]{telem['motor_health']:.3f}[/]")
    t_table.add_row("GPS Lat",   f"{telem['lat']:.5f}°",
                    "GPS Lon",   f"{telem['lon']:.5f}°")

    telem_panel = Panel(t_table,
        title=f"[bold {AMBER}]▸ TELEMETRÍA PyBullet — FÍSICA REAL[/]",
        border_style=BLUE, padding=(0,1))

    # Panel IA
    if ai_resp:
        ec    = estado_color(ai_resp.get("estado","ERROR"))
        roi_v = ai_resp.get("roi_score", 0.0)
        roi_c = GREEN if roi_v > 0.7 else AMBER if roi_v > 0.4 else RED

        ai_content = Text()
        ai_content.append(f"  ESTADO:   ", style=DIM)
        ai_content.append(f"{ai_resp.get('estado','?')}\n", style=f"bold {ec}")
        ai_content.append(f"  ANOMALÍA: ", style=DIM)
        ai_content.append(f"{ai_resp.get('anomalia','Ninguna') or 'Ninguna'}\n", style=WHITE)
        ai_content.append(f"  ORDEN:    ", style=DIM)
        ai_content.append(f"{ai_resp.get('orden','—')}\n", style=f"bold {AMBER}")
        ai_content.append(f"  RAZÓN:    ", style=DIM)
        ai_content.append(f"{ai_resp.get('razon','—')}\n", style=DIM)
        ai_content.append(f"  ROI:      ", style=DIM)
        ai_content.append(f"{roi_v:.2f}", style=f"bold {roi_c}")
    elif thinking:
        ai_content = Text()
        ai_content.append("\n  ⟳ Claude analizando telemetría...\n", style=f"italic {DIM}")
    else:
        ai_content = Text()
        ai_content.append("\n  Esperando primer análisis...\n", style=DIM)

    ai_panel = Panel(ai_content,
        title=f"[bold {AMBER}]▸ CEREBRO TÁCTICO — CLAUDE AI[/]",
        border_style=AMBER if not thinking else DIM,
        padding=(0,1))

    # Panel log IA
    log_table = Table(box=None, show_header=False, expand=True)
    log_table.add_column("T", style=DIM, width=9)
    log_table.add_column("E", width=11)
    log_table.add_column("ORDEN", style=WHITE)

    for entry in list(ai_log)[:6]:
        ec = estado_color(entry["estado"])
        log_table.add_row(
            entry["ts"],
            f"[{ec}]{entry['estado']}[/]",
            entry["orden"][:45],
        )
    if not ai_log:
        log_table.add_row("—","—","Esperando decisiones del agente...")

    log_panel = Panel(log_table,
        title=f"[bold {AMBER}]▸ HISTORIAL DECISIONES IA[/]",
        border_style=BLUE, padding=(0,1))

    # Panel KPIs
    def krow(l, v, c=WHITE):
        t2 = Text()
        t2.append(f"  {l:<26}", style=DIM)
        t2.append(v, style=f"bold {c}")
        return t2

    kpi_content = Text("\n").join([
        krow("Consultas al agente", str(kpi["consultas"]),     BLUE),
        krow("Decisiones NOMINAL",  str(kpi["nominales"]),     GREEN),
        krow("Decisiones WARNING",  str(kpi["warnings"]),      AMBER),
        krow("Decisiones CRITICAL", str(kpi["criticals"]),     RED),
        krow("ROI promedio",        f"{kpi['roi_avg']:.3f}",   AMBER),
        krow("Uptime",              f"{kpi['uptime']:.0f}s",   MAG),
        krow("Ahorro capital",      f"${kpi['savings']:.4f}",  GREEN),
    ])
    kpi_panel = Panel(kpi_content,
        title=f"[bold {AMBER}]▸ KPIs AGENTE IA[/]",
        border_style=AMBER, padding=(1,1))

    # Footer
    estado_actual = ai_resp.get("estado","—") if ai_resp else "INICIANDO"
    ec = estado_color(estado_actual)
    footer_t = Text(justify="center")
    footer_t.append("CENTINELA-AI ACTIVE", style=f"bold {AMBER}")
    footer_t.append("  ·  ", style=DIM)
    footer_t.append(f"IA: {estado_actual}", style=f"bold {ec}")
    footer_t.append("  ·  ", style=DIM)
    footer_t.append("Claude Sonnet 4.6", style=f"bold {BLUE}")
    footer_t.append("  ·  Ctrl+C para detener", style=DIM)

    # Ensamblar
    root = Table.grid(expand=True)
    root.add_row(Panel(Align.center(hdr), style=AMBER, padding=(0,2)))
    root.add_row(telem_panel)
    root.add_row(Columns([ai_panel, kpi_panel], expand=True))
    root.add_row(log_panel)
    root.add_row(Panel(Align.center(footer_t), style=DIM, padding=(0,1)))
    return root


# ─────────────────────────────────────────────────────────────────────────────
# ORQUESTADOR PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def main():
    console.clear()
    console.print(Panel.fit(
        f"[bold {AMBER}]Inicializando CENTINELA-AI...[/]\n"
        f"[{BLUE}]Conectando con Claude Sonnet 4.6...[/]\n"
        f"[{DIM}]Motor de física PyBullet activado[/]",
        title="[bold white]EATON DYNAMICS — CENTINELA-AI v1.0[/]",
        border_style=AMBER,
    ))
    time.sleep(1.5)

    # Inicializar física
    pb_client, pb_body = init_physics()
    battery    = 100.0
    tick       = 0
    ai_resp    = None
    ai_log     = deque(maxlen=20)
    anom_hist  = []
    thinking   = False
    start_time = time.time()

    kpi = {
        "consultas": 0, "nominales": 0, "warnings": 0,
        "criticals": 0, "roi_sum": 0.0, "roi_avg": 0.0,
        "uptime": 0.0, "savings": 0.0,
    }

    try:
        with Live(console=console, refresh_per_second=6, screen=True) as live:
            while True:
                tick += 1
                telem, battery = physics_step(pb_client, pb_body, tick, battery)

                # Actualizar KPIs
                kpi["uptime"]  = time.time() - start_time
                kpi["savings"] = max(0.0, (2*25.0 - 4.0) * kpi["uptime"]/3600)

                # Consultar agente IA cada AI_EVERY_TICKS ticks
                if tick % AI_EVERY_TICKS == 0:
                    thinking = True
                    live.update(build_dashboard(
                        telem, tick, ai_resp, ai_log, kpi, thinking))

                    # Construir historial de anomalías para contexto
                    bat = telem["battery_pct"]
                    if bat < 30:
                        anom_hist.append(f"[{datetime.now().strftime('%H:%M:%S')}] Batería baja: {bat:.1f}%")
                    if telem["motor_health"] < 0.75:
                        anom_hist.append(f"[{datetime.now().strftime('%H:%M:%S')}] Motor anómalo: {telem['motor_health']:.3f}")

                    # Llamar a Claude
                    ai_resp = consultar_agente(telem, anom_hist)
                    thinking = False

                    # Actualizar KPIs del agente
                    kpi["consultas"] += 1
                    estado = ai_resp.get("estado", "ERROR")
                    if estado == "NOMINAL":   kpi["nominales"] += 1
                    elif estado == "WARNING": kpi["warnings"]  += 1
                    elif estado in ("CRITICAL","EMERGENCY"): kpi["criticals"] += 1

                    roi = ai_resp.get("roi_score", 0.0)
                    kpi["roi_sum"] += roi
                    kpi["roi_avg"]  = kpi["roi_sum"] / kpi["consultas"]

                    # Guardar en log
                    ai_log.appendleft({
                        "ts":     datetime.now().strftime("%H:%M:%S"),
                        "estado": estado,
                        "orden":  ai_resp.get("orden", "—"),
                    })

                # Render dashboard
                live.update(build_dashboard(
                    telem, tick, ai_resp, ai_log, kpi, thinking))

                time.sleep(TICK_DT)

    except KeyboardInterrupt:
        pb.disconnect(physicsClientId=pb_client)
        console.print(f"\n[bold {AMBER}]◈ CENTINELA-AI DETENIDO.[/]")


if __name__ == "__main__":
    main()
