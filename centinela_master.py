"""
╔══════════════════════════════════════════════════════════════════════════════╗
║          PROYECTO CENTINELA — EATON DYNAMICS COMMAND CENTER v1.0           ║
║          Arquitectura: Async Event Loop / Single-Process Orchestration     ║
║          Stack: Python 3.11+ | Rich | NumPy | AsyncIO                      ║
║          Optimizado para: Ryzen 7 + RTX 5050 (CUDA-ready placeholders)     ║
╚══════════════════════════════════════════════════════════════════════════════╝

ARQUITECTURA DE DECISIÓN:
  - Un único event loop asyncio orquesta TODOS los módulos → latencia inter-módulo ~0ms
  - DroneState como dataclass inmutable (thread-safe por diseño)
  - TacticalAnalyzer usa Z-Score vectorizado con NumPy (GPU-extensible via CuPy)
  - DispatchManager implementa ROI ponderado: score = Σ(wᵢ · fᵢ)
  - KPITracker mantiene ventana deslizante O(1) con deque
"""

import asyncio
import math
import random
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Optional

import numpy as np
from rich import box
from rich.align import Align
from rich.columns import Columns
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTES GLOBALES DEL SISTEMA
# ─────────────────────────────────────────────────────────────────────────────

AMBER   = "bright_yellow"
BLUE    = "bright_cyan"
RED     = "bright_red"
GREEN   = "bright_green"
WHITE   = "white"
DIM     = "dim white"
MAGENTA = "magenta"

PERIMETER_CENTER_LAT = -12.0464    # Lima, Perú
PERIMETER_CENTER_LON = -77.0428
PERIMETER_RADIUS_KM  = 2.0         # Radio del perímetro de seguridad

TELEMETRY_HZ       = 2             # Frecuencia de actualización (simulada)
BATTERY_CRITICAL   = 20.0          # %
BATTERY_LOW        = 35.0          # %
SIGNAL_LOSS_RSSI   = -90           # dBm
ANOMALY_Z_THRESH   = 2.5           # σ para clasificar anomalía

DISPATCH_WEIGHTS = {               # Pesos del algoritmo ROI de despacho
    "battery":      0.40,
    "distance":     0.40,
    "motor_health": 0.20,
}

NUM_DRONES = 6
TICK_INTERVAL = 0.5               # segundos entre actualizaciones


# ─────────────────────────────────────────────────────────────────────────────
# A. MÓDULO DE TELEMETRÍA — DroneState + TelemetryEngine
# ─────────────────────────────────────────────────────────────────────────────

class DroneStatus(Enum):
    STANDBY   = auto()
    PATROL    = auto()
    DEPLOYED  = auto()
    RETURNING = auto()
    CRITICAL  = auto()
    OFFLINE   = auto()

class AlertLevel(Enum):
    NOMINAL   = "nominal"
    WARNING   = "warning"
    CRITICAL  = "critical"
    EMERGENCY = "emergency"

@dataclass
class DroneState:
    """Estado completo e inmutable (por convención) de un drone en un tick."""
    drone_id:      str
    lat:           float
    lon:           float
    altitude_m:    float
    battery_pct:   float
    rssi_dbm:      int
    motor_rpm:     list[float]        # 4 motores
    motor_health:  float              # 0.0 – 1.0
    velocity_ms:   float
    status:        DroneStatus
    alert:         AlertLevel
    mission_id:    Optional[str]
    uptime_s:      float
    timestamp:     float = field(default_factory=time.time)

    @property
    def gps_ok(self) -> bool:
        return self.rssi_dbm > SIGNAL_LOSS_RSSI

    @property
    def battery_ok(self) -> bool:
        return self.battery_pct > BATTERY_CRITICAL

    @property
    def distance_to_center_km(self) -> float:
        """Distancia haversine al centro del perímetro."""
        R = 6371.0
        φ1, φ2 = math.radians(PERIMETER_CENTER_LAT), math.radians(self.lat)
        Δφ = math.radians(self.lat - PERIMETER_CENTER_LAT)
        Δλ = math.radians(self.lon - PERIMETER_CENTER_LON)
        a = math.sin(Δφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(Δλ/2)**2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class TelemetryEngine:
    """
    Generador de telemetría sintética con deriva estocástica realista.
    Cada drone tiene una trayectoria independiente simulada por Random Walk.
    """

    def __init__(self, num_drones: int):
        self.num_drones = num_drones
        self._states: dict[str, DroneState] = {}
        self._tick: int = 0
        self._initialize_fleet()

    def _initialize_fleet(self):
        for i in range(self.num_drones):
            did = f"CNTL-{i+1:02d}"
            angle = (2 * math.pi / self.num_drones) * i
            r = random.uniform(0.5, 1.8)
            lat = PERIMETER_CENTER_LAT + r * 0.009 * math.sin(angle)
            lon = PERIMETER_CENTER_LON + r * 0.009 * math.cos(angle)
            self._states[did] = DroneState(
                drone_id     = did,
                lat          = lat,
                lon          = lon,
                altitude_m   = random.uniform(50, 120),
                battery_pct  = random.uniform(60, 100),
                rssi_dbm     = random.randint(-70, -50),
                motor_rpm    = [random.uniform(4800, 5200) for _ in range(4)],
                motor_health = random.uniform(0.85, 1.0),
                velocity_ms  = random.uniform(5, 15),
                status       = random.choice([DroneStatus.PATROL, DroneStatus.STANDBY]),
                alert        = AlertLevel.NOMINAL,
                mission_id   = None,
                uptime_s     = random.uniform(0, 3600),
            )

    def tick(self) -> dict[str, DroneState]:
        """Avanza un tick de telemetría. O(n_drones). Thread-safe read."""
        self._tick += 1
        updated = {}

        for did, s in self._states.items():
            # Deriva de posición (Random Walk gaussiano)
            dlat = random.gauss(0, 0.0002)
            dlon = random.gauss(0, 0.0002)

            # Consumo de batería: función de velocidad y tiempo
            drain = (s.velocity_ms / 20.0) * 0.08 + random.gauss(0, 0.02)
            battery = max(0.0, s.battery_pct - drain)

            # Degradación de señal estocástica
            rssi = s.rssi_dbm + random.randint(-3, 3)
            rssi = max(-110, min(-40, rssi))

            # Vibración de motores (fallo simulado ocasional)
            base_rpm = 5000
            motor_rpm = [
                base_rpm + random.gauss(0, 50) + (random.gauss(0, 400) if random.random() < 0.03 else 0)
                for _ in range(4)
            ]
            rpm_variance = np.std(motor_rpm) / base_rpm
            motor_health = max(0.0, min(1.0, s.motor_health - rpm_variance * 0.01))

            # Altitud con oscilación controlada
            altitude = s.altitude_m + random.gauss(0, 1.5)
            altitude = max(10.0, min(200.0, altitude))

            # Velocidad
            velocity = max(0, s.velocity_ms + random.gauss(0, 0.5))

            # Determinar status y alert
            status = s.status
            alert  = AlertLevel.NOMINAL

            if battery < BATTERY_CRITICAL:
                status = DroneStatus.CRITICAL
                alert  = AlertLevel.EMERGENCY
            elif battery < BATTERY_LOW:
                alert  = AlertLevel.WARNING
            elif rssi < SIGNAL_LOSS_RSSI:
                alert  = AlertLevel.CRITICAL
            elif motor_health < 0.70:
                alert  = AlertLevel.WARNING

            if battery < 10.0:
                status = DroneStatus.OFFLINE

            new_state = DroneState(
                drone_id     = did,
                lat          = s.lat + dlat,
                lon          = s.lon + dlon,
                altitude_m   = altitude,
                battery_pct  = battery,
                rssi_dbm     = rssi,
                motor_rpm    = motor_rpm,
                motor_health = motor_health,
                velocity_ms  = velocity,
                status       = status,
                alert        = alert,
                mission_id   = s.mission_id,
                uptime_s     = s.uptime_s + TICK_INTERVAL,
            )
            updated[did] = new_state

        self._states = updated
        return dict(updated)

    def inject_anomaly(self, drone_id: str, anomaly_type: str):
        """Inyecta una anomalía controlada para testing táctico."""
        if drone_id not in self._states:
            return
        s = self._states[drone_id]
        if anomaly_type == "battery_drop":
            self._states[drone_id] = DroneState(**{**s.__dict__, "battery_pct": 15.0})
        elif anomaly_type == "signal_loss":
            self._states[drone_id] = DroneState(**{**s.__dict__, "rssi_dbm": -95})
        elif anomaly_type == "motor_fail":
            bad_rpm = s.motor_rpm[:]
            bad_rpm[random.randint(0,3)] = random.uniform(1000, 2000)
            self._states[drone_id] = DroneState(**{**s.__dict__, "motor_rpm": bad_rpm, "motor_health": 0.45})


# ─────────────────────────────────────────────────────────────────────────────
# B. MOTOR DE ANÁLISIS TÁCTICO — TacticalAnalyzer
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TacticalAlert:
    drone_id:    str
    alert_type:  str
    severity:    AlertLevel
    description: str
    timestamp:   float = field(default_factory=time.time)

class TacticalAnalyzer:
    """
    Motor de análisis táctico con detección de anomalías por Z-Score (NumPy).
    Mantiene historial deslizante para calcular μ y σ por métrica.

    Z-Score: z = (x - μ) / σ  →  |z| > ANOMALY_Z_THRESH → anomalía
    """

    WINDOW = 20    # Ticks en la ventana deslizante

    def __init__(self):
        self._history: dict[str, dict[str, deque]] = {}
        self._active_alerts: list[TacticalAlert] = []
        self._alert_log: deque = deque(maxlen=50)

    def _ensure_drone(self, drone_id: str):
        if drone_id not in self._history:
            self._history[drone_id] = {
                "battery": deque(maxlen=self.WINDOW),
                "rssi":    deque(maxlen=self.WINDOW),
                "motor":   deque(maxlen=self.WINDOW),
                "alt":     deque(maxlen=self.WINDOW),
            }

    def _z_score_anomaly(self, series: deque, current: float) -> float:
        """Retorna el Z-Score del valor actual vs la ventana histórica."""
        if len(series) < 5:
            return 0.0
        arr = np.array(series)
        mu, sigma = arr.mean(), arr.std()
        if sigma < 1e-6:
            return 0.0
        return abs((current - mu) / sigma)

    def _perimeter_check(self, state: DroneState) -> Optional[TacticalAlert]:
        """Detección de intrusión: drone fuera del perímetro asignado."""
        dist = state.distance_to_center_km
        if dist > PERIMETER_RADIUS_KM * 1.2:
            return TacticalAlert(
                drone_id    = state.drone_id,
                alert_type  = "PERIMETER_BREACH",
                severity    = AlertLevel.CRITICAL,
                description = f"Fuera de perímetro: {dist:.2f}km > {PERIMETER_RADIUS_KM}km"
            )
        return None

    def analyze(self, states: dict[str, DroneState]) -> list[TacticalAlert]:
        """
        Analiza el tick actual de telemetría.
        Retorna lista de alertas activas.
        Complejidad: O(n_drones · WINDOW) → O(1) amortizado con deque.
        """
        self._active_alerts = []

        for did, s in states.items():
            self._ensure_drone(did)
            h = self._history[did]

            # 1. Z-Score sobre batería
            z_bat = self._z_score_anomaly(h["battery"], s.battery_pct)
            h["battery"].append(s.battery_pct)

            # 2. Z-Score sobre RSSI
            z_rssi = self._z_score_anomaly(h["rssi"], s.rssi_dbm)
            h["rssi"].append(s.rssi_dbm)

            # 3. Z-Score sobre motor health
            z_motor = self._z_score_anomaly(h["motor"], s.motor_health)
            h["motor"].append(s.motor_health)

            # 4. Z-Score sobre altitud
            z_alt = self._z_score_anomaly(h["alt"], s.altitude_m)
            h["alt"].append(s.altitude_m)

            # Umbral fijo de batería crítica
            if s.battery_pct < BATTERY_CRITICAL:
                alert = TacticalAlert(
                    drone_id    = did,
                    alert_type  = "BATTERY_CRITICAL",
                    severity    = AlertLevel.EMERGENCY,
                    description = f"Batería {s.battery_pct:.1f}% — Retorno inmediato requerido"
                )
                self._active_alerts.append(alert)
                self._alert_log.appendleft(alert)

            # Pérdida de señal
            elif not s.gps_ok:
                alert = TacticalAlert(
                    drone_id    = did,
                    alert_type  = "SIGNAL_LOSS",
                    severity    = AlertLevel.CRITICAL,
                    description = f"RSSI {s.rssi_dbm}dBm — Señal GPS degradada"
                )
                self._active_alerts.append(alert)
                self._alert_log.appendleft(alert)

            # Fallo de motor (Z-Score)
            elif z_motor > ANOMALY_Z_THRESH and s.motor_health < 0.75:
                alert = TacticalAlert(
                    drone_id    = did,
                    alert_type  = "MOTOR_ANOMALY",
                    severity    = AlertLevel.WARNING,
                    description = f"Motor health {s.motor_health:.2f} — Z={z_motor:.1f}σ"
                )
                self._active_alerts.append(alert)
                self._alert_log.appendleft(alert)

            # Batería baja (no crítica)
            elif s.battery_pct < BATTERY_LOW:
                alert = TacticalAlert(
                    drone_id    = did,
                    alert_type  = "BATTERY_LOW",
                    severity    = AlertLevel.WARNING,
                    description = f"Batería {s.battery_pct:.1f}% — Planificar retorno"
                )
                self._active_alerts.append(alert)
                self._alert_log.appendleft(alert)

            # Intrusión de perímetro
            perimeter_alert = self._perimeter_check(s)
            if perimeter_alert:
                self._active_alerts.append(perimeter_alert)
                self._alert_log.appendleft(perimeter_alert)

        # Deduplicar por drone_id (mantener la más severa)
        seen: dict[str, TacticalAlert] = {}
        severity_rank = {
            AlertLevel.EMERGENCY: 4,
            AlertLevel.CRITICAL:  3,
            AlertLevel.WARNING:   2,
            AlertLevel.NOMINAL:   1,
        }
        for a in self._active_alerts:
            if a.drone_id not in seen or \
               severity_rank[a.severity] > severity_rank[seen[a.drone_id].severity]:
                seen[a.drone_id] = a

        self._active_alerts = list(seen.values())
        return self._active_alerts

    @property
    def alert_log(self) -> list[TacticalAlert]:
        return list(self._alert_log)[:8]


# ─────────────────────────────────────────────────────────────────────────────
# C. GESTOR DE RESPUESTA — DispatchManager
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DispatchDecision:
    """Resultado del algoritmo de despacho con trazabilidad de ROI."""
    incident_id:   str
    assigned_to:   str
    roi_score:     float
    eta_min:       float
    battery_cost:  float
    reason:        str
    timestamp:     float = field(default_factory=time.time)

class DispatchManager:
    """
    Algoritmo de despacho basado en ROI operativo ponderado.

    ROI_score(dᵢ) = w_bat  · f_bat(bᵢ)
                  + w_dist · f_dist(dᵢ, target)
                  + w_moto · f_motor(mᵢ)

    donde:
      f_bat(b)   = b / 100                          (normalizado 0-1)
      f_dist(d)  = 1 / (1 + dist_km)               (decreciente con distancia)
      f_motor(m) = m                                (0-1 directo)
    """

    def __init__(self):
        self._dispatch_log: deque = deque(maxlen=20)
        self._incident_counter = 0

    def _roi_score(self, state: DroneState, target_lat: float, target_lon: float) -> float:
        """Calcula score ROI para asignar este drone al incidente."""
        if state.status in (DroneStatus.CRITICAL, DroneStatus.OFFLINE):
            return -1.0

        # f_bat: penaliza fuertemente < BATTERY_LOW
        bat_norm = state.battery_pct / 100.0
        if state.battery_pct < BATTERY_LOW:
            bat_norm *= 0.3

        # f_dist: distancia haversine al target
        R = 6371.0
        φ1 = math.radians(state.lat)
        φ2 = math.radians(target_lat)
        Δφ = math.radians(target_lat - state.lat)
        Δλ = math.radians(target_lon - state.lon)
        a = math.sin(Δφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(Δλ/2)**2
        dist_km = R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        dist_score = 1.0 / (1.0 + dist_km)

        # f_motor
        motor_score = state.motor_health

        score = (
            DISPATCH_WEIGHTS["battery"]      * bat_norm   +
            DISPATCH_WEIGHTS["distance"]     * dist_score +
            DISPATCH_WEIGHTS["motor_health"] * motor_score
        )
        return round(score, 4)

    def dispatch(
        self,
        states: dict[str, DroneState],
        target_lat: float,
        target_lon: float,
        incident_type: str = "SECURITY_EVENT"
    ) -> Optional[DispatchDecision]:
        """
        Selecciona el drone óptimo para el incidente dado.
        Retorna None si ningún drone está disponible.
        """
        self._incident_counter += 1
        incident_id = f"INC-{self._incident_counter:04d}"

        candidates = {
            did: self._roi_score(s, target_lat, target_lon)
            for did, s in states.items()
            if s.status not in (DroneStatus.OFFLINE, DroneStatus.CRITICAL)
        }

        if not candidates:
            return None

        best_id  = max(candidates, key=candidates.get)
        best_score = candidates[best_id]
        best_state = states[best_id]

        if best_score < 0:
            return None

        # ETA estimado: distancia / velocidad media (convertida a minutos)
        R = 6371.0
        Δφ = math.radians(target_lat - best_state.lat)
        Δλ = math.radians(target_lon - best_state.lon)
        φ1 = math.radians(best_state.lat)
        φ2 = math.radians(target_lat)
        a = math.sin(Δφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(Δλ/2)**2
        dist_km = R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        avg_speed_kmh = best_state.velocity_ms * 3.6
        eta_min = (dist_km / max(avg_speed_kmh, 1)) * 60

        battery_cost = dist_km * 2.5   # % batería estimada por km

        decision = DispatchDecision(
            incident_id  = incident_id,
            assigned_to  = best_id,
            roi_score    = best_score,
            eta_min      = eta_min,
            battery_cost = battery_cost,
            reason       = f"{incident_type} | Dist {dist_km:.2f}km | Score {best_score:.3f}",
        )
        self._dispatch_log.appendleft(decision)
        return decision

    @property
    def dispatch_log(self) -> list[DispatchDecision]:
        return list(self._dispatch_log)[:5]


# ─────────────────────────────────────────────────────────────────────────────
# D. REPORTE DE KPIs — KPITracker
# ─────────────────────────────────────────────────────────────────────────────

class KPITracker:
    """
    Mantiene métricas operativas en tiempo real con ventana deslizante.

    KPIs principales:
      - Tiempo de Respuesta Promedio (min)
      - Flota Operativa (%)
      - Tasa de Alertas (alertas/min)
      - Ahorro de Capital Operativo vs despacho manual (USD estimado)
      - MTBF simulado (Mean Time Between Failures)
    """

    COST_PER_HUMAN_PATROL_USD = 25.0    # USD/hora por patrulla humana
    DRONE_COST_PER_HOUR_USD   = 4.0     # USD/hora por drone

    def __init__(self):
        self._response_times: deque  = deque(maxlen=100)
        self._alert_counts:   deque  = deque(maxlen=60)
        self._start_time:     float  = time.time()
        self._total_dispatches:  int = 0
        self._total_incidents:   int = 0
        self._ticks:             int = 0

    def record_dispatch(self, decision: DispatchDecision):
        self._response_times.append(decision.eta_min)
        self._total_dispatches += 1

    def record_alerts(self, alerts: list[TacticalAlert]):
        self._alert_counts.append(len(alerts))
        self._total_incidents += len(alerts)

    def tick(self):
        self._ticks += 1

    @property
    def avg_response_time_min(self) -> float:
        if not self._response_times:
            return 0.0
        return float(np.mean(self._response_times))

    @property
    def alert_rate_per_min(self) -> float:
        if not self._alert_counts:
            return 0.0
        return float(np.mean(self._alert_counts)) * (60 / TICK_INTERVAL)

    @property
    def uptime_hours(self) -> float:
        return (time.time() - self._start_time) / 3600

    @property
    def capital_savings_usd(self) -> float:
        """
        Δ Costo = (Costo patrullas humanas equivalentes) - (Costo flota drones)
        Equivalente humano: 1 drone ≈ 2 patrullas en cobertura de área.
        """
        equiv_patrols = NUM_DRONES * 2
        human_cost  = equiv_patrols * self.COST_PER_HUMAN_PATROL_USD * self.uptime_hours
        drone_cost  = NUM_DRONES   * self.DRONE_COST_PER_HOUR_USD    * self.uptime_hours
        return max(0.0, human_cost - drone_cost)

    def summary(self, states: dict[str, DroneState]) -> dict:
        operational = sum(
            1 for s in states.values()
            if s.status not in (DroneStatus.OFFLINE, DroneStatus.CRITICAL)
        )
        return {
            "fleet_operational_pct": (operational / NUM_DRONES) * 100,
            "avg_response_min":      self.avg_response_time_min,
            "alert_rate_per_min":    self.alert_rate_per_min,
            "capital_savings_usd":   self.capital_savings_usd,
            "total_incidents":       self._total_incidents,
            "total_dispatches":      self._total_dispatches,
            "uptime_hours":          self.uptime_hours,
        }


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD — Rich Terminal UI (Bloomberg Amber/Blue aesthetic)
# ─────────────────────────────────────────────────────────────────────────────

console = Console()

def build_header(tick: int) -> Panel:
    ts = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    title = Text()
    title.append("◈ PROYECTO CENTINELA", style=f"bold {AMBER}")
    title.append("  │  ", style=DIM)
    title.append("EATON DYNAMICS CMD v1.0", style=f"bold {BLUE}")
    title.append("  │  ", style=DIM)
    title.append(ts, style=WHITE)
    title.append("  │  TICK ", style=DIM)
    title.append(f"#{tick:04d}", style=MAGENTA)
    return Panel(Align.center(title), style=AMBER, padding=(0, 2))


def build_fleet_table(states: dict[str, DroneState], alerts: list[TacticalAlert]) -> Table:
    alert_map = {a.drone_id: a for a in alerts}

    t = Table(
        title="[bold bright_yellow]▸ TELEMETRÍA DE FLOTA EN TIEMPO REAL[/]",
        box=box.SIMPLE_HEAD,
        style="dim white",
        header_style=f"bold {AMBER}",
        show_lines=True,
        expand=True,
    )
    t.add_column("DRONE",    style=f"bold {BLUE}", width=10)
    t.add_column("STATUS",   width=11)
    t.add_column("BAT %",    justify="right", width=8)
    t.add_column("ALT m",    justify="right", width=8)
    t.add_column("VEL m/s",  justify="right", width=9)
    t.add_column("RSSI dBm", justify="right", width=10)
    t.add_column("MOTOR HS", justify="right", width=10)
    t.add_column("LAT",      justify="right", width=12)
    t.add_column("LON",      justify="right", width=12)
    t.add_column("ALERTA",   width=20)

    STATUS_COLOR = {
        DroneStatus.PATROL:    f"[{GREEN}]PATROL[/]",
        DroneStatus.STANDBY:   f"[{BLUE}]STANDBY[/]",
        DroneStatus.DEPLOYED:  f"[{AMBER}]DEPLOYED[/]",
        DroneStatus.RETURNING: f"[{MAGENTA}]RETURN[/]",
        DroneStatus.CRITICAL:  f"[bold {RED}]CRITICAL[/]",
        DroneStatus.OFFLINE:   f"[{DIM}]OFFLINE[/]",
    }
    ALERT_COLOR = {
        AlertLevel.NOMINAL:   f"[{GREEN}]● NOMINAL[/]",
        AlertLevel.WARNING:   f"[{AMBER}]▲ WARNING[/]",
        AlertLevel.CRITICAL:  f"[bold {RED}]■ CRITICAL[/]",
        AlertLevel.EMERGENCY: f"[bold {RED}]✗ EMERGENCY[/]",
    }

    for did, s in sorted(states.items()):
        bat_color = RED if s.battery_pct < BATTERY_CRITICAL else \
                    AMBER if s.battery_pct < BATTERY_LOW else GREEN
        rssi_color = RED if not s.gps_ok else GREEN
        motor_color = RED if s.motor_health < 0.70 else \
                      AMBER if s.motor_health < 0.85 else GREEN
        alert = alert_map.get(did)
        alert_cell = ALERT_COLOR.get(s.alert, ALERT_COLOR[AlertLevel.NOMINAL])

        t.add_row(
            did,
            STATUS_COLOR.get(s.status, "UNKNOWN"),
            f"[{bat_color}]{s.battery_pct:6.1f}[/]",
            f"{s.altitude_m:7.1f}",
            f"{s.velocity_ms:6.1f}",
            f"[{rssi_color}]{s.rssi_dbm:5d}[/]",
            f"[{motor_color}]{s.motor_health:.3f}[/]",
            f"{s.lat:.5f}",
            f"{s.lon:.5f}",
            alert_cell,
        )
    return t


def build_alerts_panel(analyzer: TacticalAnalyzer) -> Panel:
    log = analyzer.alert_log
    t = Table(box=None, show_header=False, expand=True, padding=(0,1))
    t.add_column("TIME",  style=DIM,           width=9)
    t.add_column("DRONE", style=f"bold {BLUE}", width=10)
    t.add_column("TYPE",  style=f"bold {AMBER}", width=18)
    t.add_column("DESC",  style=WHITE)

    SCOLOR = {
        AlertLevel.EMERGENCY: RED,
        AlertLevel.CRITICAL:  RED,
        AlertLevel.WARNING:   AMBER,
        AlertLevel.NOMINAL:   GREEN,
    }

    for a in log:
        ts = datetime.fromtimestamp(a.timestamp).strftime("%H:%M:%S")
        sc = SCOLOR.get(a.severity, WHITE)
        t.add_row(
            ts,
            f"[{sc}]{a.drone_id}[/]",
            f"[{sc}]{a.alert_type}[/]",
            a.description[:50],
        )

    if not log:
        t.add_row("—", "—", "—", f"[{GREEN}]Sistema nominal. Sin alertas activas.[/]")

    return Panel(
        t,
        title=f"[bold {AMBER}]▸ LOG DE ALERTAS TÁCTICAS (últimas 8)[/]",
        border_style=RED if log else GREEN,
        padding=(0, 1),
    )


def build_dispatch_panel(manager: DispatchManager) -> Panel:
    log = manager.dispatch_log
    t = Table(box=None, show_header=True, expand=True, padding=(0,1))
    t.add_column("INCIDENTE", style=f"bold {AMBER}", width=10)
    t.add_column("DRONE",     style=f"bold {BLUE}",  width=10)
    t.add_column("ROI",       justify="right",        width=6)
    t.add_column("ETA min",   justify="right",        width=8)
    t.add_column("BAT costo", justify="right",        width=10)
    t.add_column("RAZÓN",     style=DIM)

    for d in log:
        roi_color = GREEN if d.roi_score > 0.6 else AMBER if d.roi_score > 0.3 else RED
        t.add_row(
            d.incident_id,
            d.assigned_to,
            f"[{roi_color}]{d.roi_score:.3f}[/]",
            f"{d.eta_min:.1f}",
            f"{d.battery_cost:.1f}%",
            d.reason[:45],
        )

    if not log:
        t.add_row("—", "—", "—", "—", "—", "[dim]Sin despachos registrados[/]")

    return Panel(
        t,
        title=f"[bold {AMBER}]▸ GESTOR DE DESPACHO — ROI TÁCTICO[/]",
        border_style=BLUE,
        padding=(0, 1),
    )


def build_kpi_panel(kpis: dict) -> Panel:
    def kpi_row(label: str, value: str, color: str = WHITE) -> Text:
        t = Text()
        t.append(f"  {label:<32}", style=DIM)
        t.append(value, style=f"bold {color}")
        return t

    fleet_pct = kpis["fleet_operational_pct"]
    fleet_color = GREEN if fleet_pct >= 80 else AMBER if fleet_pct >= 50 else RED

    lines = [
        kpi_row("Flota Operativa",            f"{fleet_pct:.1f}%",     fleet_color),
        kpi_row("Tiempo de Respuesta (avg)",  f"{kpis['avg_response_min']:.2f} min", BLUE),
        kpi_row("Tasa de Alertas",            f"{kpis['alert_rate_per_min']:.2f}/min", AMBER),
        kpi_row("Incidentes Totales",         str(kpis["total_incidents"]),            WHITE),
        kpi_row("Despachos Totales",          str(kpis["total_dispatches"]),           WHITE),
        kpi_row("Ahorro Capital Operativo",   f"${kpis['capital_savings_usd']:.2f} USD", GREEN),
        kpi_row("Uptime Sistema",             f"{kpis['uptime_hours']*3600:.0f}s",     MAGENTA),
    ]

    content = Text("\n").join(lines)
    return Panel(
        content,
        title=f"[bold {AMBER}]▸ KPIs OPERATIVOS — GERENCIA[/]",
        border_style=AMBER,
        padding=(1, 1),
    )


def build_footer(active_alerts: int) -> Text:
    t = Text(justify="center")
    t.append("CENTINELA ACTIVE", style=f"bold {AMBER}")
    t.append("  ·  ", style=DIM)
    t.append(f"ALERTAS ACTIVAS: {active_alerts}", style=f"bold {RED}" if active_alerts > 0 else f"bold {GREEN}")
    t.append("  ·  ", style=DIM)
    t.append("RYZEN 7 + RTX 5050", style=f"bold {BLUE}")
    t.append("  ·  ", style=DIM)
    t.append("Ctrl+C para detener", style=DIM)
    return t


def build_layout(
    tick:      int,
    states:    dict[str, DroneState],
    alerts:    list[TacticalAlert],
    analyzer:  TacticalAnalyzer,
    manager:   DispatchManager,
    kpis:      dict,
) -> Table:
    """Ensambla el dashboard completo en un Table raíz para Rich Live."""
    root = Table.grid(expand=True)
    root.add_row(build_header(tick))
    root.add_row(build_fleet_table(states, alerts))
    root.add_row(
        Columns([
            build_alerts_panel(analyzer),
            build_kpi_panel(kpis),
        ], expand=True)
    )
    root.add_row(build_dispatch_panel(manager))
    root.add_row(Panel(build_footer(len(alerts)), style=DIM, padding=(0,2)))
    return root


# ─────────────────────────────────────────────────────────────────────────────
# ORQUESTADOR PRINCIPAL — centinela_master()
# ─────────────────────────────────────────────────────────────────────────────

async def simulate_incidents(
    manager: DispatchManager,
    kpi:     KPITracker,
    states_ref: list,   # [dict] mutable reference via list wrapper
):
    """Coroutine que genera incidentes sintéticos periódicos para testing del despacho."""
    await asyncio.sleep(3)
    while True:
        await asyncio.sleep(random.uniform(4, 10))
        if states_ref:
            states = states_ref[0]
            # Incidente en ubicación aleatoria dentro del área de Lima
            inc_lat = PERIMETER_CENTER_LAT + random.uniform(-0.018, 0.018)
            inc_lon = PERIMETER_CENTER_LON + random.uniform(-0.018, 0.018)
            incident_types = [
                "INTRUSION_DETECTED",
                "SUSPICIOUS_VEHICLE",
                "PERIMETER_ALERT",
                "CROWD_ANOMALY",
                "LOGISTICS_REQUEST",
            ]
            itype = random.choice(incident_types)
            decision = manager.dispatch(states, inc_lat, inc_lon, itype)
            if decision:
                kpi.record_dispatch(decision)


async def centinela_master():
    """
    Orquestador principal del Proyecto Centinela.

    Loop de control:
      1. TelemetryEngine.tick()     → nuevos estados de flota
      2. TacticalAnalyzer.analyze() → alertas activas
      3. KPITracker.record_alerts() → métricas
      4. Dashboard.render()         → Rich Live update
      5. Sleep(TICK_INTERVAL)       → cadencia de actualización
    """
    console.clear()
    console.print(
        Panel.fit(
            f"[bold {AMBER}]Inicializando PROYECTO CENTINELA...[/]\n"
            f"[{BLUE}]Cargando {NUM_DRONES} drones en flota...[/]\n"
            f"[{DIM}]Lima, Perú — Perímetro {PERIMETER_RADIUS_KM}km radio[/]",
            title="[bold white]EATON DYNAMICS[/]",
            border_style=AMBER,
        )
    )
    await asyncio.sleep(1.5)

    telemetry = TelemetryEngine(NUM_DRONES)
    analyzer  = TacticalAnalyzer()
    dispatch  = DispatchManager()
    kpi       = KPITracker()

    states_ref: list = []
    tick = 0

    # Lanzar coroutine de incidentes en paralelo
    incident_task = asyncio.create_task(
        simulate_incidents(dispatch, kpi, states_ref)
    )

    try:
        with Live(console=console, refresh_per_second=4) as live:
            while True:
                tick += 1
                kpi.tick()

                # Inyectar anomalías aleatorias con baja probabilidad (testing)
                if random.random() < 0.04:
                    target = random.choice(list(telemetry._states.keys()))
                    anomaly = random.choice(["battery_drop", "signal_loss", "motor_fail"])
                    telemetry.inject_anomaly(target, anomaly)

                # Módulo A — Telemetría
                states = telemetry.tick()
                states_ref.clear()
                states_ref.append(states)

                # Módulo B — Análisis Táctico
                alerts = analyzer.analyze(states)
                kpi.record_alerts(alerts)

                # Módulo D — KPIs
                kpi_summary = kpi.summary(states)

                # Render dashboard
                dashboard = build_layout(tick, states, alerts, analyzer, dispatch, kpi_summary)
                live.update(dashboard)

                await asyncio.sleep(TICK_INTERVAL)

    except KeyboardInterrupt:
        incident_task.cancel()
        console.print(f"\n[bold {AMBER}]◈ CENTINELA DETENIDO. Sesión terminada.[/]")
    except Exception as e:
        incident_task.cancel()
        console.print_exception()
        raise


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        asyncio.run(centinela_master())
    except KeyboardInterrupt:
        pass
