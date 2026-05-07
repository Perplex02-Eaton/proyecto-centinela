"""
PROYECTO CENTINELA — SISTEMA MULTI-AGENTE v1.0
6 Drones con IA independiente (Sonnet 4.6) + Coordinador de Flota (Opus 4.7)
Arquitectura: Agentes distribuidos con estado compartido de flota

Diseño de agentes:
  - DroneAgent (x6): Sonnet 4.6 — decisiones individuales rápidas
  - FleetCoordinator (x1): Opus 4.7 — estrategia de flota compleja
  - FleetState: estado compartido en memoria (O(1) lectura/escritura)
  - Staggered API calls: agentes escalonados cada 10 ticks (evita rate limit)
"""

import math, time, random, os, json, re
from collections import deque
from datetime import datetime
from dataclasses import dataclass, field
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
    raise ValueError("ANTHROPIC_API_KEY no configurada.")

client_ai = anthropic.Anthropic(api_key=API_KEY)

NUM_DRONES       = 6
REF_LAT          = -12.0464
REF_LON          = -77.0428
M_PER_DEG_LAT    = 111_320.0
M_PER_DEG_LON    = 111_320.0 * math.cos(math.radians(REF_LAT))
DRONE_MASS_KG    = 1.2
GRAVITY          = 9.81
HOVER_THRUST     = DRONE_MASS_KG * GRAVITY
K_DRAG           = 0.15
BATTERY_DRAIN    = 0.010
TICK_DT          = 0.05
AGENT_INTERVAL   = 80     # ticks entre llamadas por agente
COORD_INTERVAL   = 200    # ticks entre llamadas al coordinador
STAGGER          = 12     # ticks de diferencia entre agentes

AMBER = "bright_yellow"
BLUE  = "bright_cyan"
RED   = "bright_red"
GREEN = "bright_green"
DIM   = "dim white"
MAG   = "magenta"
WHITE = "white"

console = Console()

# ─────────────────────────────────────────────────────────────────────────────
# MISIONES DE PATRULLA POR SECTOR (Lima, Perú)
# ─────────────────────────────────────────────────────────────────────────────

SECTORES = [
    {"nombre": "Miraflores",   "radio": 150, "angulo_base": 0.0,    "alt": 80},
    {"nombre": "San Isidro",   "radio": 200, "angulo_base": 1.047,  "alt": 90},
    {"nombre": "Barranco",     "radio": 130, "angulo_base": 2.094,  "alt": 70},
    {"nombre": "Surquillo",    "radio": 180, "angulo_base": 3.141,  "alt": 85},
    {"nombre": "La Victoria",  "radio": 160, "angulo_base": 4.189,  "alt": 75},
    {"nombre": "Lince",        "radio": 140, "angulo_base": 5.236,  "alt": 80},
]

# ─────────────────────────────────────────────────────────────────────────────
# ESTADO COMPARTIDO DE FLOTA (memoria compartida entre agentes)
# ─────────────────────────────────────────────────────────────────────────────

class FleetState:
    """
    Estado global de la flota. Acceso O(1).
    Cada agente lee el estado de todos los demás antes de decidir.
    """
    def __init__(self):
        self._states  : dict = {}    # drone_id -> telem dict
        self._decisions: dict = {}   # drone_id -> última decisión IA
        self._coord_order: str = "Inicializando flota..."
        self._coord_estado: str = "INICIANDO"

    def update_telem(self, drone_id: str, telem: dict):
        self._states[drone_id] = telem

    def update_decision(self, drone_id: str, decision: dict):
        self._decisions[drone_id] = decision

    def update_coordinator(self, orden: str, estado: str):
        self._coord_order  = orden
        self._coord_estado = estado

    def get_fleet_summary(self) -> str:
        """Resumen de la flota para contexto de agentes."""
        lines = []
        for did, t in self._states.items():
            dec = self._decisions.get(did, {})
            lines.append(
                f"  {did}: BAT={t.get('battery_pct',0):.0f}% "
                f"ALT={t.get('altitude_m',0):.0f}m "
                f"SECTOR={t.get('sector','?')} "
                f"ESTADO={dec.get('estado','?')}"
            )
        return "\n".join(lines) if lines else "Sin datos"

    @property
    def all_telems(self) -> dict:
        return dict(self._states)

    @property
    def all_decisions(self) -> dict:
        return dict(self._decisions)

    @property
    def coord_order(self) -> str:
        return self._coord_order

    @property
    def coord_estado(self) -> str:
        return self._coord_estado


fleet_state = FleetState()


# ─────────────────────────────────────────────────────────────────────────────
# MOTOR DE FÍSICA — PyBullet Multi-Drone
# ─────────────────────────────────────────────────────────────────────────────

COLORES_DRONE = [
    [0.2, 0.6, 1.0, 1.0],  # Azul
    [1.0, 0.6, 0.2, 1.0],  # Ámbar
    [0.2, 1.0, 0.4, 1.0],  # Verde
    [1.0, 0.2, 0.2, 1.0],  # Rojo
    [0.8, 0.2, 1.0, 1.0],  # Morado
    [0.2, 1.0, 1.0, 1.0],  # Cyan
]

def init_physics(num_drones: int):
    c = pb.connect(pb.DIRECT)
    pb.setGravity(0, 0, -GRAVITY, physicsClientId=c)
    pb.setTimeStep(TICK_DT, physicsClientId=c)
    pb.setAdditionalSearchPath(pybullet_data.getDataPath())
    pb.loadURDF("plane.urdf", physicsClientId=c)

    bodies = []
    for i in range(num_drones):
        sector = SECTORES[i]
        angle  = sector["angulo_base"]
        r      = sector["radio"] * 0.3
        x0     = r * math.cos(angle)
        y0     = r * math.sin(angle)
        z0     = float(sector["alt"])

        col = pb.createCollisionShape(pb.GEOM_BOX,
              halfExtents=[0.15,0.15,0.04], physicsClientId=c)
        vis = pb.createVisualShape(pb.GEOM_BOX,
              halfExtents=[0.15,0.15,0.04],
              rgbaColor=COLORES_DRONE[i], physicsClientId=c)
        body = pb.createMultiBody(baseMass=DRONE_MASS_KG,
               baseCollisionShapeIndex=col, baseVisualShapeIndex=vis,
               basePosition=[x0, y0, z0], physicsClientId=c)
        pb.changeDynamics(body, -1, linearDamping=K_DRAG,
                          angularDamping=0.9, physicsClientId=c)
        bodies.append(body)
    return c, bodies


def physics_step_drone(c, body, tick, battery, sector_idx, drone_idx):
    """Física independiente por drone con misión de sector."""
    sector = SECTORES[sector_idx]
    r      = sector["radio"]
    speed  = 0.008 + drone_idx * 0.001
    angle  = sector["angulo_base"] + tick * speed
    target_x   = r * math.cos(angle)
    target_y   = r * math.sin(angle)
    target_alt = float(sector["alt"]) + 10.0 * math.sin(tick * 0.02)

    pos, _ = pb.getBasePositionAndOrientation(body, physicsClientId=c)
    vel, _ = pb.getBaseVelocity(body, physicsClientId=c)

    # Control proporcional XYZ
    kp_xy  = 0.8
    kp_alt = 2.5
    fx = kp_xy  * (target_x   - pos[0])
    fy = kp_xy  * (target_y   - pos[1])
    thrust = HOVER_THRUST + kp_alt * (target_alt - pos[2])
    thrust = max(0.0, min(thrust, HOVER_THRUST * 2.8))

    rpms = []
    for off in [[0.15,0.15,0.02],[-0.15,0.15,0.02],
                [0.15,-0.15,0.02],[-0.15,-0.15,0.02]]:
        t_i = thrust/4.0 + random.gauss(0, 0.03)
        t_i = max(0.0, t_i)
        pb.applyExternalForce(body,-1,[0,0,t_i],off,pb.LINK_FRAME,physicsClientId=c)
        rpms.append(min(8000, max(0, t_i/HOVER_THRUST*5000*4)))

    # Fuerza de navegación XY
    pb.applyExternalForce(body,-1,[fx,fy,0],[0,0,0],pb.WORLD_FRAME,physicsClientId=c)
    # Viento
    pb.applyExternalForce(body,-1,
        [random.gauss(0,0.06),random.gauss(0,0.06),0],
        [0,0,0],pb.LINK_FRAME,physicsClientId=c)

    pb.stepSimulation(physicsClientId=c)

    pos, _ = pb.getBasePositionAndOrientation(body, physicsClientId=c)
    spd_v, _ = pb.getBaseVelocity(body, physicsClientId=c)
    speed_ms = math.sqrt(sum(v**2 for v in spd_v))

    rpm_var      = float(np.std(rpms))/5000.0
    motor_health = max(0.0, min(1.0, 1.0 - rpm_var*3))
    new_bat      = max(0.0, battery - BATTERY_DRAIN*(thrust/HOVER_THRUST))
    dist_m       = math.sqrt(pos[0]**2 + pos[1]**2)
    rssi         = int(max(-110, min(-40, -52 - dist_m*0.25 + random.gauss(0,2))))
    lat          = REF_LAT + (pos[1]/M_PER_DEG_LAT)
    lon          = REF_LON + (pos[0]/M_PER_DEG_LON)

    return {
        "x_m": pos[0], "y_m": pos[1],
        "lat": lat, "lon": lon,
        "altitude_m": max(0.0, pos[2]),
        "vel_ms": speed_ms,
        "battery_pct": new_bat,
        "rssi_dbm": rssi,
        "motor_rpm": rpms,
        "motor_health": motor_health,
        "thrust_N": thrust,
        "sector": sector["nombre"],
    }, new_bat


# ─────────────────────────────────────────────────────────────────────────────
# AGENTE IA INDIVIDUAL — Sonnet 4.6 (velocidad táctica)
# ─────────────────────────────────────────────────────────────────────────────

PROMPT_AGENTE = """Eres un agente IA táctico autónomo controlando un drone de seguridad en Lima, Perú.
Tu ID de drone y sector están en el mensaje. Tienes acceso al estado de toda la flota.

Analiza tu telemetría y el estado de la flota. Toma UNA decisión táctica precisa.

RESPONDE SOLO con este JSON (sin markdown, sin texto adicional):
{"estado":"NOMINAL|WARNING|CRITICAL|EMERGENCY","orden":"acción específica en <10 palabras","coordina_con":"ID_drone_o_null","roi_score":0.0-1.0,"razon":"justificación en <12 palabras"}"""


def llamar_agente_drone(drone_id: str, telem: dict, fleet_summary: str) -> dict:
    prompt = f"""DRONE: {drone_id} | SECTOR: {telem.get('sector','?')}
TELEMETRÍA:
  Batería: {telem['battery_pct']:.1f}%
  Altitud: {telem['altitude_m']:.1f}m
  Velocidad: {telem['vel_ms']:.2f}m/s
  RSSI: {telem['rssi_dbm']}dBm
  Motor Health: {telem['motor_health']:.3f}
  GPS: ({telem['lat']:.5f}, {telem['lon']:.5f})

ESTADO DE FLOTA:
{fleet_summary}

Emite tu decisión táctica."""

    try:
        r = client_ai.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            system=PROMPT_AGENTE,
            messages=[{"role":"user","content":prompt}]
        )
        text = r.content[0].text.strip()
        match = re.search(r'\{.*?\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        pass
    return {"estado":"ERROR","orden":"Reconectando...","coordina_con":None,
            "roi_score":0.0,"razon":"Error API"}


# ─────────────────────────────────────────────────────────────────────────────
# COORDINADOR DE FLOTA — Opus 4.7 (estrategia compleja)
# ─────────────────────────────────────────────────────────────────────────────

PROMPT_COORDINADOR = """Eres el Coordinador de Flota CENTINELA-AI de Lima, Perú.
Supervisas 6 drones autónomos de seguridad. Recibes el estado completo de la flota.

Tu rol: optimizar la cobertura total, detectar conflictos entre agentes,
reasignar recursos cuando hay emergencias, y emitir órdenes de flota.

RESPONDE SOLO con este JSON:
{"estado_flota":"NOMINAL|DEGRADADO|CRITICO","orden_flota":"orden para toda la flota en <15 palabras","reasignacion":"ID_drone->sector_o_null","alerta_critica":"descripcion_o_null","eficiencia_flota":0.0-1.0}"""


def llamar_coordinador(fleet_summary: str, all_decisions: dict) -> dict:
    decisiones_str = "\n".join([
        f"  {did}: {dec.get('estado','?')} | {dec.get('orden','?')}"
        for did, dec in all_decisions.items()
    ])

    prompt = f"""ESTADO DE FLOTA COMPLETO:
{fleet_summary}

DECISIONES DE AGENTES INDIVIDUALES:
{decisiones_str}

Analiza la situación global y emite orden de coordinación de flota."""

    try:
        r = client_ai.messages.create(
            model="claude-opus-4-7",
            max_tokens=300,
            system=PROMPT_COORDINADOR,
            messages=[{"role":"user","content":prompt}]
        )
        text = r.content[0].text.strip()
        match = re.search(r'\{.*?\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        pass
    return {"estado_flota":"ERROR","orden_flota":"Error coordinador",
            "reasignacion":None,"alerta_critica":str(e)[:40],"eficiencia_flota":0.0}


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD RICH — MULTI-AGENTE
# ─────────────────────────────────────────────────────────────────────────────

def estado_color(e: str) -> str:
    return {"NOMINAL":GREEN,"WARNING":AMBER,"CRITICAL":RED,
            "EMERGENCY":RED,"DEGRADADO":AMBER,"CRITICO":RED,
            "ERROR":DIM,"INICIANDO":DIM}.get(e, WHITE)


def build_fleet_table(batteries: list, tick: int) -> Table:
    all_t = fleet_state.all_telems
    all_d = fleet_state.all_decisions

    t = Table(
        title=f"[bold {AMBER}]▸ TELEMETRÍA MULTI-AGENTE — {NUM_DRONES} DRONES ACTIVOS[/]",
        box=rbox.SIMPLE_HEAD, style=DIM,
        header_style=f"bold {AMBER}", show_lines=True, expand=True,
    )
    t.add_column("DRONE",    style=f"bold {BLUE}", width=10)
    t.add_column("SECTOR",   width=13)
    t.add_column("BAT %",    justify="right", width=7)
    t.add_column("ALT m",    justify="right", width=7)
    t.add_column("VEL m/s",  justify="right", width=8)
    t.add_column("RSSI",     justify="right", width=8)
    t.add_column("MOT HS",   justify="right", width=8)
    t.add_column("ESTADO IA",width=11)
    t.add_column("ORDEN IA", width=30)
    t.add_column("ROI",      justify="right", width=5)

    for i, did in enumerate([f"CNTL-{j+1:02d}" for j in range(NUM_DRONES)]):
        telem = all_t.get(did, {})
        dec   = all_d.get(did, {})

        bat   = telem.get("battery_pct", batteries[i])
        alt   = telem.get("altitude_m", 0)
        vel   = telem.get("vel_ms", 0)
        rssi  = telem.get("rssi_dbm", 0)
        mh    = telem.get("motor_health", 1.0)
        sec   = telem.get("sector", SECTORES[i]["nombre"])

        bc = RED if bat<20 else AMBER if bat<35 else GREEN
        mc = RED if mh<0.70 else AMBER if mh<0.85 else GREEN
        rc = AMBER if rssi < -85 else GREEN

        estado = dec.get("estado","—")
        ec     = estado_color(estado)
        orden  = dec.get("orden","Esperando IA...")[:28]
        roi    = dec.get("roi_score", 0.0)
        roic   = GREEN if roi>0.7 else AMBER if roi>0.4 else RED

        t.add_row(
            did,
            sec,
            f"[{bc}]{bat:5.1f}[/]",
            f"{alt:6.1f}",
            f"{vel:5.2f}",
            f"[{rc}]{rssi:4d}[/]",
            f"[{mc}]{mh:.3f}[/]",
            f"[{ec}]{estado}[/]",
            f"[{AMBER}]{orden}[/]",
            f"[{roic}]{roi:.2f}[/]",
        )
    return t


def build_coordinator_panel() -> Panel:
    orden  = fleet_state.coord_order
    estado = fleet_state.coord_estado
    ec     = estado_color(estado)

    content = Text()
    content.append(f"  ESTADO FLOTA: ", style=DIM)
    content.append(f"{estado}\n", style=f"bold {ec}")
    content.append(f"  ORDEN:        ", style=DIM)
    content.append(f"{orden}\n", style=f"bold {AMBER}")
    content.append(f"\n  Motor: ", style=DIM)
    content.append("Claude Opus 4.7", style=f"bold {BLUE}")
    content.append(" — Estrategia de flota completa", style=DIM)

    return Panel(content,
        title=f"[bold {AMBER}]▸ COORDINADOR DE FLOTA — OPUS 4.7[/]",
        border_style=ec if ec != DIM else AMBER,
        padding=(0,1))


def build_kpi_panel(kpi: dict) -> Panel:
    def r(l,v,c=WHITE):
        t2 = Text()
        t2.append(f"  {l:<24}", style=DIM)
        t2.append(v, style=f"bold {c}")
        return t2

    content = Text("\n").join([
        r("Consultas agentes",  str(kpi["consultas"]),           BLUE),
        r("Consultas coord.",   str(kpi["coord_calls"]),         MAG),
        r("Nominales",          str(kpi["nominales"]),           GREEN),
        r("Warnings",           str(kpi["warnings"]),            AMBER),
        r("Críticos",           str(kpi["criticals"]),           RED),
        r("ROI flota prom.",    f"{kpi['roi_avg']:.3f}",         AMBER),
        r("Uptime",             f"{kpi['uptime']:.0f}s",         MAG),
        r("Ahorro capital",     f"${kpi['savings']:.4f}",        GREEN),
    ])
    return Panel(content,
        title=f"[bold {AMBER}]▸ KPIs MULTI-AGENTE[/]",
        border_style=AMBER, padding=(1,1))


def build_dashboard(tick: int, kpi: dict, thinking_id: Optional[str]) -> Table:
    ts = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    hdr = Text()
    hdr.append("◈ CENTINELA MULTI-AGENT", style=f"bold {AMBER}")
    hdr.append("  │  ", style=DIM)
    hdr.append("6 DRONES · SONNET 4.6 + OPUS 4.7", style=f"bold {BLUE}")
    hdr.append("  │  ", style=DIM)
    hdr.append(ts, style=WHITE)
    hdr.append(f"  │  TICK #{tick:06d}", style=MAG)
    if thinking_id:
        hdr.append(f"  │  ⟳ {thinking_id}", style=f"italic {DIM}")

    coord_estado = fleet_state.coord_estado
    ec = estado_color(coord_estado)
    footer = Text(justify="center")
    footer.append("CENTINELA MULTI-AGENT ACTIVE", style=f"bold {AMBER}")
    footer.append("  ·  ", style=DIM)
    footer.append(f"FLOTA: {coord_estado}", style=f"bold {ec}")
    footer.append("  ·  ", style=DIM)
    footer.append("Sonnet 4.6 × 6 + Opus 4.7 × 1", style=f"bold {BLUE}")
    footer.append("  ·  Ctrl+C para detener", style=DIM)

    batteries = [kpi.get(f"bat_{i}", 100.0) for i in range(NUM_DRONES)]

    root = Table.grid(expand=True)
    root.add_row(Panel(Align.center(hdr), style=AMBER, padding=(0,2)))
    root.add_row(build_fleet_table(batteries, tick))
    root.add_row(Columns([
        build_coordinator_panel(),
        build_kpi_panel(kpi),
    ], expand=True))
    root.add_row(Panel(Align.center(footer), style=DIM, padding=(0,1)))
    return root


# ─────────────────────────────────────────────────────────────────────────────
# ORQUESTADOR PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def main():
    console.clear()
    console.print(Panel.fit(
        f"[bold {AMBER}]Inicializando CENTINELA MULTI-AGENTE...[/]\n"
        f"[{BLUE}]Creando {NUM_DRONES} drones en física PyBullet...[/]\n"
        f"[{DIM}]Sonnet 4.6 × 6 agentes + Opus 4.7 × 1 coordinador[/]",
        title="[bold white]EATON DYNAMICS — MULTI-AGENT v1.0[/]",
        border_style=AMBER,
    ))
    time.sleep(1.5)

    pb_client, pb_bodies = init_physics(NUM_DRONES)
    drone_ids  = [f"CNTL-{i+1:02d}" for i in range(NUM_DRONES)]
    batteries  = [100.0] * NUM_DRONES
    tick       = 0
    start_time = time.time()
    thinking_id: Optional[str] = None

    kpi = {
        "consultas": 0, "coord_calls": 0,
        "nominales": 0, "warnings": 0, "criticals": 0,
        "roi_sum": 0.0, "roi_avg": 0.0,
        "uptime": 0.0, "savings": 0.0,
    }
    for i in range(NUM_DRONES):
        kpi[f"bat_{i}"] = 100.0

    try:
        with Live(console=console, refresh_per_second=6, screen=True) as live:
            while True:
                tick += 1
                kpi["uptime"]  = time.time() - start_time
                kpi["savings"] = max(0.0, (NUM_DRONES*2*25.0 - NUM_DRONES*4.0) * kpi["uptime"]/3600)

                # ── Física de los 6 drones ──
                for i, (did, body) in enumerate(zip(drone_ids, pb_bodies)):
                    telem, new_bat = physics_step_drone(
                        pb_client, body, tick, batteries[i], i, i)
                    batteries[i]    = new_bat
                    kpi[f"bat_{i}"] = new_bat
                    fleet_state.update_telem(did, telem)

                # ── Llamadas a agentes individuales (escalonadas) ──
                for i, did in enumerate(drone_ids):
                    offset = i * STAGGER
                    if (tick - offset) > 0 and (tick - offset) % AGENT_INTERVAL == 0:
                        thinking_id = did
                        live.update(build_dashboard(tick, kpi, thinking_id))

                        telem    = fleet_state.all_telems.get(did, {})
                        summary  = fleet_state.get_fleet_summary()
                        decision = llamar_agente_drone(did, telem, summary)

                        fleet_state.update_decision(did, decision)
                        kpi["consultas"] += 1
                        estado = decision.get("estado","ERROR")
                        if estado=="NOMINAL":    kpi["nominales"] += 1
                        elif estado=="WARNING":  kpi["warnings"]  += 1
                        elif estado in ("CRITICAL","EMERGENCY"): kpi["criticals"] += 1
                        roi = decision.get("roi_score",0.0)
                        kpi["roi_sum"] += roi
                        kpi["roi_avg"] = kpi["roi_sum"] / max(1, kpi["consultas"])
                        thinking_id = None

                # ── Coordinador Opus 4.7 (menos frecuente) ──
                if tick % COORD_INTERVAL == 0:
                    thinking_id = "COORDINADOR (Opus 4.7)"
                    live.update(build_dashboard(tick, kpi, thinking_id))

                    coord = llamar_coordinador(
                        fleet_state.get_fleet_summary(),
                        fleet_state.all_decisions
                    )
                    fleet_state.update_coordinator(
                        coord.get("orden_flota","Sin orden"),
                        coord.get("estado_flota","NOMINAL")
                    )
                    kpi["coord_calls"] += 1
                    thinking_id = None

                live.update(build_dashboard(tick, kpi, thinking_id))
                time.sleep(TICK_DT)

    except KeyboardInterrupt:
        pb.disconnect(physicsClientId=pb_client)
        console.print(f"\n[bold {AMBER}]◈ CENTINELA MULTI-AGENTE DETENIDO.[/]")


if __name__ == "__main__":
    main()
