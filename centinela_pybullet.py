"""
PROYECTO CENTINELA — Motor de Física Real con PyBullet
Drone CNTL-SIM-01 con física newtoniana → Dashboard Rich en tiempo real

Física implementada:
  - Gravedad: 9.81 m/s²
  - Empuje: 4 rotores independientes (thrust vectoring simplificado)
  - Arrastre aerodinámico: F_drag = -k_drag * v
  - Viento estocástico: perturbación gaussiana por tick
  - Batería: consumo proporcional al empuje total
  - GPS: conversión XYZ (metros) → Lat/Lon (Lima, Perú)
"""

import math, time, random
from collections import deque
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional
import asyncio

import numpy as np
import pybullet as pb
import pybullet_data

from rich import box as rbox
from rich.align import Align
from rich.columns import Columns
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTES
# ─────────────────────────────────────────────────────────────────────────────

REF_LAT              = -12.0464          # Lima, Perú
REF_LON              = -77.0428
M_PER_DEG_LAT        = 111_320.0
M_PER_DEG_LON        = 111_320.0 * math.cos(math.radians(REF_LAT))

DRONE_MASS_KG        = 1.2              # kg (quadrotor estándar)
GRAVITY              = 9.81             # m/s²
HOVER_THRUST         = DRONE_MASS_KG * GRAVITY
K_DRAG               = 0.15             # coeficiente de arrastre aerodinámico
WIND_SIGMA           = 0.08             # σ viento gaussiano (m/s²)
BATTERY_CAPACITY     = 100.0            # %
BATTERY_DRAIN_HOVER  = 0.015           # %/tick en hover
TICK_DT              = 0.05             # paso de física (s)
RENDER_EVERY         = 10              # ticks entre renders del dashboard

AMBER  = "bright_yellow"
BLUE   = "bright_cyan"
RED    = "bright_red"
GREEN  = "bright_green"
DIM    = "dim white"
WHITE  = "white"
MAG    = "magenta"

# ─────────────────────────────────────────────────────────────────────────────
# A. MOTOR DE FÍSICA — PyBulletDrone
# ─────────────────────────────────────────────────────────────────────────────

class PyBulletDrone:
    """
    Drone cuadricóptero simulado con física real PyBullet.

    Modelo dinámico simplificado:
      ΣF = T_total * ẑ - m*g*ẑ - k_drag*v  (en world frame)
      T_total = Σ T_i   (i = 1..4 rotores)

    Control:
      - Hover estable: T_total ≈ m*g + perturbación
      - Misión patrol: variación sinusoidal de altitud
      - Viento: ruido gaussiano en XY
    """

    ROTOR_OFFSETS = [
        [ 0.15,  0.15, 0.02],
        [-0.15,  0.15, 0.02],
        [ 0.15, -0.15, 0.02],
        [-0.15, -0.15, 0.02],
    ]

    def __init__(self, drone_id: str, start_pos: list, physics_client: int):
        self.drone_id      = drone_id
        self.physics_client = physics_client
        self.battery       = BATTERY_CAPACITY
        self.mission_tick  = 0
        self._rpm_history  = deque(maxlen=20)

        # Crear cuerpo del drone (caja + esfera colisionador)
        col_shape = pb.createCollisionShape(
            pb.GEOM_BOX,
            halfExtents=[0.15, 0.15, 0.04],
            physicsClientId=physics_client
        )
        vis_shape = pb.createVisualShape(
            pb.GEOM_BOX,
            halfExtents=[0.15, 0.15, 0.04],
            rgbaColor=[0.1, 0.1, 0.1, 1.0],
            physicsClientId=physics_client
        )
        self.body_id = pb.createMultiBody(
            baseMass=DRONE_MASS_KG,
            baseCollisionShapeIndex=col_shape,
            baseVisualShapeIndex=vis_shape,
            basePosition=start_pos,
            physicsClientId=physics_client
        )
        # Amortiguación lineal y angular para estabilidad
        pb.changeDynamics(
            self.body_id, -1,
            linearDamping=K_DRAG,
            angularDamping=0.9,
            physicsClientId=physics_client
        )

    def step(self) -> dict:
        """
        Avanza un tick de física.
        Retorna telemetría completa del drone.
        """
        self.mission_tick += 1

        # ── Control de empuje (hover + misión sinusoidal) ──
        target_alt = 80.0 + 15.0 * math.sin(self.mission_tick * 0.02)
        pos, orn   = pb.getBasePositionAndOrientation(
            self.body_id, physicsClientId=self.physics_client)
        vel_lin, vel_ang = pb.getBaseVelocity(
            self.body_id, physicsClientId=self.physics_client)

        alt_error  = target_alt - pos[2]
        kp         = 2.5
        thrust_cmd = HOVER_THRUST + kp * alt_error
        thrust_cmd = max(0.0, min(thrust_cmd, HOVER_THRUST * 2.5))

        # Distribuir empuje entre 4 rotores
        rotor_thrusts = []
        for i in range(4):
            t_i = thrust_cmd / 4.0 + random.gauss(0, 0.05)
            t_i = max(0.0, t_i)
            rotor_thrusts.append(t_i)

            # Aplicar fuerza en posición del rotor
            r_pos_local = self.ROTOR_OFFSETS[i]
            pb.applyExternalForce(
                self.body_id, -1,
                forceObj=[0, 0, t_i],
                posObj=r_pos_local,
                flags=pb.LINK_FRAME,
                physicsClientId=self.physics_client
            )

        # ── Viento estocástico ──
        wind = [random.gauss(0, WIND_SIGMA),
                random.gauss(0, WIND_SIGMA),
                0.0]
        pb.applyExternalForce(
            self.body_id, -1,
            forceObj=wind,
            posObj=[0, 0, 0],
            flags=pb.LINK_FRAME,
            physicsClientId=self.physics_client
        )

        # ── Movimiento de patrulla (XY) ──
        patrol_force = [
            0.3 * math.cos(self.mission_tick * 0.015),
            0.3 * math.sin(self.mission_tick * 0.015),
            0.0
        ]
        pb.applyExternalForce(
            self.body_id, -1,
            forceObj=patrol_force,
            posObj=[0, 0, 0],
            flags=pb.WORLD_FRAME,
            physicsClientId=self.physics_client
        )

        # ── Paso de física ──
        pb.stepSimulation(physicsClientId=self.physics_client)

        # ── Leer estado post-step ──
        pos, orn   = pb.getBasePositionAndOrientation(
            self.body_id, physicsClientId=self.physics_client)
        vel_lin, _ = pb.getBaseVelocity(
            self.body_id, physicsClientId=self.physics_client)

        velocity   = math.sqrt(sum(v**2 for v in vel_lin))
        altitude   = max(0.0, pos[2])

        # RPM estimado de cada rotor (proporcional al empuje)
        rpms = [min(8000, max(0, t / HOVER_THRUST * 5000 * 4))
                for t in rotor_thrusts]
        self._rpm_history.append(rpms)

        # Motor health = 1 - varianza normalizada de RPMs
        rpm_var = float(np.std(rpms)) / 5000.0
        motor_health = max(0.0, min(1.0, 1.0 - rpm_var * 3))

        # Consumo de batería proporcional al empuje
        thrust_ratio = thrust_cmd / HOVER_THRUST
        self.battery = max(0.0,
            self.battery - BATTERY_DRAIN_HOVER * thrust_ratio)

        # RSSI simulado (degradación con distancia al origen)
        dist_m = math.sqrt(pos[0]**2 + pos[1]**2)
        rssi   = int(-55 - dist_m * 0.3 + random.gauss(0, 2))
        rssi   = max(-110, min(-40, rssi))

        # Conversión XYZ → GPS
        lat = REF_LAT + (pos[1] / M_PER_DEG_LAT)
        lon = REF_LON + (pos[0] / M_PER_DEG_LON)

        return {
            "drone_id":    self.drone_id,
            "x_m":         pos[0],
            "y_m":         pos[1],
            "lat":         lat,
            "lon":         lon,
            "altitude_m":  altitude,
            "vel_ms":      velocity,
            "battery_pct": self.battery,
            "rssi_dbm":    rssi,
            "motor_rpm":   rpms,
            "motor_health": motor_health,
            "thrust_N":    thrust_cmd,
            "tick":        self.mission_tick,
        }


# ─────────────────────────────────────────────────────────────────────────────
# B. ANALIZADOR TÁCTICO
# ─────────────────────────────────────────────────────────────────────────────

class AlertLevel:
    NOMINAL   = ("● NOMINAL",   "bright_green")
    WARNING   = ("▲ WARNING",   "bright_yellow")
    CRITICAL  = ("■ CRITICAL",  "bright_red")
    EMERGENCY = ("✗ EMERGENCY", "bright_red")

def analyze(telem: dict) -> tuple:
    bat = telem["battery_pct"]
    mh  = telem["motor_health"]
    rssi = telem["rssi_dbm"]

    if bat < 15.0:
        return AlertLevel.EMERGENCY, f"Batería {bat:.1f}% — RETORNO INMEDIATO"
    if bat < 30.0:
        return AlertLevel.WARNING, f"Batería {bat:.1f}% — Planificar retorno"
    if rssi < -90:
        return AlertLevel.CRITICAL, f"RSSI {rssi}dBm — Señal GPS degradada"
    if mh < 0.70:
        return AlertLevel.WARNING, f"Motor health {mh:.3f} — Anomalía detectada"
    return AlertLevel.NOMINAL, "Sistema nominal"


# ─────────────────────────────────────────────────────────────────────────────
# C. KPI TRACKER
# ─────────────────────────────────────────────────────────────────────────────

class KPITracker:
    def __init__(self):
        self.start_time      = time.time()
        self.max_altitude    = 0.0
        self.min_battery     = 100.0
        self.total_distance  = 0.0
        self.alert_count     = 0
        self._last_pos       = None
        self._alt_history    = deque(maxlen=50)

    def update(self, telem: dict, alert_level):
        alt = telem["altitude_m"]
        self.max_altitude = max(self.max_altitude, alt)
        self.min_battery  = min(self.min_battery, telem["battery_pct"])
        self._alt_history.append(alt)

        pos = (telem["x_m"], telem["y_m"])
        if self._last_pos:
            dx = pos[0] - self._last_pos[0]
            dy = pos[1] - self._last_pos[1]
            self.total_distance += math.sqrt(dx**2 + dy**2)
        self._last_pos = pos

        if alert_level != AlertLevel.NOMINAL:
            self.alert_count += 1

    @property
    def uptime_s(self):
        return time.time() - self.start_time

    @property
    def avg_altitude(self):
        if not self._alt_history:
            return 0.0
        return float(np.mean(self._alt_history))

    @property
    def capital_savings_usd(self):
        h = self.uptime_s / 3600
        return max(0.0, (2 * 25.0 - 4.0) * h)


# ─────────────────────────────────────────────────────────────────────────────
# D. DASHBOARD RICH
# ─────────────────────────────────────────────────────────────────────────────

console = Console()

def build_physics_panel(telem: dict) -> Panel:
    t = Table(box=None, show_header=False, expand=True, padding=(0, 1))
    t.add_column("", style=DIM, width=22)
    t.add_column("", style=WHITE)
    t.add_column("", style=DIM, width=22)
    t.add_column("", style=WHITE)

    alt_c = RED if telem["altitude_m"] < 5 else GREEN
    bat_c = RED if telem["battery_pct"] < 20 else \
            AMBER if telem["battery_pct"] < 35 else GREEN
    vel_c = AMBER if telem["vel_ms"] > 12 else GREEN

    t.add_row(
        "Altitud (física)",    f"[{alt_c}]{telem['altitude_m']:8.2f} m[/]",
        "Velocidad",           f"[{vel_c}]{telem['vel_ms']:8.2f} m/s[/]",
    )
    t.add_row(
        "Posición X",          f"{telem['x_m']:8.2f} m",
        "Posición Y",          f"{telem['y_m']:8.2f} m",
    )
    t.add_row(
        "Batería",             f"[{bat_c}]{telem['battery_pct']:8.1f} %[/]",
        "Empuje total",        f"{telem['thrust_N']:8.2f} N",
    )
    t.add_row(
        "RSSI",                f"{telem['rssi_dbm']:8d} dBm",
        "Motor Health",        f"{telem['motor_health']:8.3f}",
    )
    t.add_row(
        "GPS Lat",             f"{telem['lat']:.6f}°",
        "GPS Lon",             f"{telem['lon']:.6f}°",
    )
    t.add_row(
        "RPM Motor 1",         f"{telem['motor_rpm'][0]:8.0f}",
        "RPM Motor 2",         f"{telem['motor_rpm'][1]:8.0f}",
    )
    t.add_row(
        "RPM Motor 3",         f"{telem['motor_rpm'][2]:8.0f}",
        "RPM Motor 4",         f"{telem['motor_rpm'][3]:8.0f}",
    )
    return Panel(
        t,
        title=f"[bold {AMBER}]▸ TELEMETRÍA FÍSICA REAL — PyBullet[/]",
        border_style=BLUE,
        padding=(0, 1),
    )


def build_kpi_panel(kpi: KPITracker) -> Panel:
    def row(label, value, color=WHITE):
        t = Text()
        t.append(f"  {label:<28}", style=DIM)
        t.append(value, style=f"bold {color}")
        return t

    lines = [
        row("Altitud máxima alcanzada", f"{kpi.max_altitude:.1f} m",         BLUE),
        row("Altitud promedio",         f"{kpi.avg_altitude:.1f} m",          WHITE),
        row("Batería mínima",           f"{kpi.min_battery:.1f} %",           AMBER),
        row("Distancia recorrida",      f"{kpi.total_distance:.1f} m",        GREEN),
        row("Alertas generadas",        str(kpi.alert_count),                 RED),
        row("Ahorro capital",           f"${kpi.capital_savings_usd:.4f} USD",GREEN),
        row("Uptime",                   f"{kpi.uptime_s:.0f} s",              MAG),
    ]
    content = Text("\n").join(lines)
    return Panel(
        content,
        title=f"[bold {AMBER}]▸ KPIs OPERATIVOS[/]",
        border_style=AMBER,
        padding=(1, 1),
    )


def build_alert_panel(alert_level: tuple, description: str,
                      history: deque) -> Panel:
    label, color = alert_level
    t = Table(box=None, show_header=False, expand=True)
    t.add_column("TIME", style=DIM, width=10)
    t.add_column("ESTADO", width=14)
    t.add_column("DESCRIPCIÓN", style=WHITE)

    for ts, lvl, desc in list(history)[:6]:
        lbl, col = lvl
        t.add_row(ts, f"[{col}]{lbl}[/]", desc)

    border = color if color != "bright_green" else "bright_green"
    return Panel(
        t,
        title=f"[bold {AMBER}]▸ LOG TÁCTICO — CNTL-SIM-01[/]",
        border_style=border,
        padding=(0, 1),
    )


def build_dashboard(telem: dict, alert_level: tuple,
                    description: str, kpi: KPITracker,
                    history: deque, tick: int) -> Table:
    ts  = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    hdr = Text()
    hdr.append("◈ PROYECTO CENTINELA", style=f"bold {AMBER}")
    hdr.append("  │  ", style=DIM)
    hdr.append("PYBULLET PHYSICS ENGINE", style=f"bold {BLUE}")
    hdr.append("  │  ", style=DIM)
    hdr.append(ts, style=WHITE)
    hdr.append(f"  │  TICK #{tick:05d}", style=MAG)

    root = Table.grid(expand=True)
    root.add_row(Panel(Align.center(hdr), style=AMBER, padding=(0, 2)))
    root.add_row(build_physics_panel(telem))
    root.add_row(
        Columns([
            build_alert_panel(alert_level, description, history),
            build_kpi_panel(kpi),
        ], expand=True)
    )
    lbl, col = alert_level
    footer = Text(justify="center")
    footer.append("CENTINELA ACTIVE", style=f"bold {AMBER}")
    footer.append("  ·  ", style=DIM)
    footer.append(f"ESTADO: {lbl}", style=f"bold {col}")
    footer.append("  ·  ", style=DIM)
    footer.append("PyBullet 3.2.7 + RTX 5050", style=f"bold {BLUE}")
    footer.append("  ·  Ctrl+C para detener", style=DIM)
    root.add_row(Panel(Align.center(footer), style=DIM, padding=(0, 1)))
    return root


# ─────────────────────────────────────────────────────────────────────────────
# ORQUESTADOR PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def main():
    console.clear()
    console.print(Panel.fit(
        f"[bold {AMBER}]Inicializando motor de física PyBullet...[/]\n"
        f"[{BLUE}]Drone CNTL-SIM-01 cargando...[/]\n"
        f"[{DIM}]Lima, Perú — Física newtoniana real[/]",
        title="[bold white]EATON DYNAMICS — VIERNES[/]",
        border_style=AMBER,
    ))
    time.sleep(1.5)

    # Inicializar PyBullet en modo DIRECTO (sin GUI, sin crash RTX)
    client = pb.connect(pb.DIRECT)
    pb.setGravity(0, 0, -GRAVITY, physicsClientId=client)
    pb.setTimeStep(TICK_DT, physicsClientId=client)

    # Cargar plano de suelo
    pb.setAdditionalSearchPath(pybullet_data.getDataPath())
    pb.loadURDF("plane.urdf", physicsClientId=client)

    # Crear drone en posición inicial (80m de altitud sobre Lima)
    drone = PyBulletDrone("CNTL-SIM-01", [0.0, 0.0, 80.0], client)
    kpi   = KPITracker()
    history: deque = deque(maxlen=20)
    tick  = 0

    try:
        with Live(console=console, refresh_per_second=8) as live:
            while True:
                tick += 1
                telem      = drone.step()
                alert, desc = analyze(telem)
                kpi.update(telem, alert)

                ts = datetime.now().strftime("%H:%M:%S")
                history.appendleft((ts, alert, desc[:50]))

                if tick % RENDER_EVERY == 0:
                    dashboard = build_dashboard(
                        telem, alert, desc, kpi, history, tick)
                    live.update(dashboard)

                time.sleep(TICK_DT * 0.5)

    except KeyboardInterrupt:
        pb.disconnect(physicsClientId=client)
        console.print(f"\n[bold {AMBER}]◈ CENTINELA DETENIDO.[/]")


if __name__ == "__main__":
    main()