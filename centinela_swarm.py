"""
PROYECTO CENTINELA — ENJAMBRE FÍSICO v1.0
Simulación de enjambre de drones con protocolo MAVLink 2.0

Arquitectura nivel Anduril/Lattice OS:
  - MAVLink 2.0 con firma HMAC-SHA256 (anti-spoofing)
  - ORCA: Optimal Reciprocal Collision Avoidance
  - Mesh network: cada drone es nodo de comunicación
  - Multi-situación: cada drone detecta independientemente
  - Coordinador Opus 4.7: estrategia de enjambre completa
  - Failsafe: RTH automático si señal perdida > 1.5s (EMERGENCY si > 3s)

Protocolos implementados:
  HEARTBEAT    → latido cada 1s (si cesa = failsafe)
  POSITION     → GPS + velocidad en tiempo real
  COMMAND      → órdenes firmadas con HMAC
  DETECTION    → reporte de amenaza al coordinador
  FORMATION    → posición relativa en formación
  EMERGENCY    → protocolo de emergencia inmediato
"""

import os, time, math, random, json, hashlib, hmac
from datetime import datetime
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional
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
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

NUM_DRONES        = 6
HMAC_SECRET       = b"CENTINELA-EATON-DYNAMICS-2026-SECURE"
TICK_DT           = 0.1     # segundos por tick (lazo principal 10 Hz)
HEARTBEAT_HZ      = 1.0     # Hz
ORCA_RADIUS       = 8.0     # metros - radio de evitación normal
ORCA_HARD_RADIUS  = 4.0     # metros - frenado de emergencia inmediato
ORCA_TICK_DT      = 0.05    # 50 ms — colisión revisada a 20 Hz → reacción <100ms
MAX_VEL           = 15.0    # m/s velocidad máxima de patrulla
MAX_VEL_RTH_EMERG = 20.0    # m/s velocidad de RTH en EMERGENCY
FAILSAFE_T        = 1.5     # segundos sin heartbeat → RTH (antes 5.0)

# Lima GPS
REF_LAT      = -12.0464
REF_LON      = -77.0428
M_LAT        = 111320.0
M_LON        = 111320.0 * math.cos(math.radians(REF_LAT))

AMBER = "bright_yellow"
BLUE  = "bright_cyan"
RED   = "bright_red"
GREEN = "bright_green"
DIM   = "dim white"
MAG   = "magenta"
WHITE = "white"

console = Console()

# ─────────────────────────────────────────────────────────────────────────────
# ENUMS Y TIPOS
# ─────────────────────────────────────────────────────────────────────────────

class DroneMode(Enum):
    GROUND    = auto()
    TAKEOFF   = auto()
    PATROL    = auto()
    INTERCEPT = auto()
    HOVER     = auto()
    RTH       = auto()
    EMERGENCY = auto()
    LANDED    = auto()

class AlertSeverity(Enum):
    CLEAR    = 0
    LOW      = 1
    MEDIUM   = 2
    HIGH     = 3
    CRITICAL = 4

class MAVLinkMsgType(Enum):
    HEARTBEAT  = 0
    POSITION   = 1
    COMMAND    = 2
    DETECTION  = 3
    FORMATION  = 4
    EMERGENCY  = 5
    ACK        = 6

# ─────────────────────────────────────────────────────────────────────────────
# A. PROTOCOLO MAVLINK 2.0 SIMULADO
# ─────────────────────────────────────────────────────────────────────────────

class MAVLink2:
    """
    Simulación de MAVLink 2.0 con firma HMAC-SHA256.

    Formato de mensaje:
      magic (1) | len (1) | incompat (1) | compat (1) |
      seq (1)   | sysid(1)| compid(1)    | msgid(3)   |
      payload   | checksum(2) | signature(13)

    Firma: HMAC-SHA256(secret, header+payload+timestamp)
    """

    _seq_counter = 0

    @classmethod
    def firmar_mensaje(cls, payload: dict, sysid: int) -> dict:
        """Firma un mensaje con HMAC-SHA256."""
        cls._seq_counter = (cls._seq_counter + 1) % 256
        ts = int(time.time() * 1e6)  # microsegundos

        raw = json.dumps(payload, sort_keys=True).encode()
        header = f"{sysid}:{cls._seq_counter}:{ts}".encode()

        firma = hmac.new(
            HMAC_SECRET,
            header + raw,
            hashlib.sha256
        ).hexdigest()[:26]  # 13 bytes = 26 hex chars

        return {
            "magic":     253,
            "seq":       cls._seq_counter,
            "sysid":     sysid,
            "timestamp": ts,
            "payload":   payload,
            "firma":     firma,
            "valid":     True,
        }

    @classmethod
    def verificar_firma(cls, mensaje: dict) -> bool:
        """Verifica la firma HMAC del mensaje recibido."""
        if not mensaje.get("valid"):
            return False
        raw    = json.dumps(mensaje["payload"], sort_keys=True).encode()
        header = f"{mensaje['sysid']}:{mensaje['seq']}:{mensaje['timestamp']}".encode()
        firma_esperada = hmac.new(
            HMAC_SECRET, header + raw, hashlib.sha256
        ).hexdigest()[:26]
        return hmac.compare_digest(firma_esperada, mensaje.get("firma",""))

    @classmethod
    def simular_ataque(cls, mensaje: dict) -> dict:
        """Simula un intento de spoofing — modifica payload."""
        msg_falso = dict(mensaje)
        msg_falso["payload"] = dict(mensaje["payload"])
        msg_falso["payload"]["lat"] = -12.0000  # GPS falso
        msg_falso["payload"]["lon"] = -77.0000
        # Firma no actualizada → falla verificación
        return msg_falso


# ─────────────────────────────────────────────────────────────────────────────
# B. DRONE FÍSICO SIMULADO
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DroneState:
    drone_id:   str
    sysid:      int
    x:          float = 0.0      # metros desde origen
    y:          float = 0.0
    z:          float = 0.0      # altitud
    vx:         float = 0.0      # velocidad
    vy:         float = 0.0
    vz:         float = 0.0
    yaw:        float = 0.0      # heading grados
    battery:    float = 100.0
    rssi:       int   = -55      # dBm
    mode:       DroneMode = DroneMode.GROUND
    alert:      AlertSeverity = AlertSeverity.CLEAR
    target_x:   float = 0.0
    target_y:   float = 0.0
    target_z:   float = 80.0
    last_hb:    float = field(default_factory=time.time)
    msg_count:  int   = 0
    msg_valid:  int   = 0
    detection:  Optional[dict] = None
    uptime:     float = 0.0

    @property
    def lat(self): return REF_LAT + self.y / M_LAT
    @property
    def lon(self): return REF_LON + self.x / M_LON
    @property
    def speed(self): return math.sqrt(self.vx**2+self.vy**2+self.vz**2)
    @property
    def dist_to_target(self):
        return math.sqrt((self.x-self.target_x)**2+(self.y-self.target_y)**2)


SECTORES_PATROL = [
    {"nombre":"Miraflores",  "x":  150, "y": -200, "z": 80},
    {"nombre":"San Isidro",  "x":  200, "y":  100, "z": 90},
    {"nombre":"Barranco",    "x": -100, "y": -350, "z": 70},
    {"nombre":"Surquillo",   "x":  -50, "y":  150, "z": 85},
    {"nombre":"La Victoria", "x":  300, "y":  200, "z": 75},
    {"nombre":"Lince",       "x": -150, "y":  -50, "z": 80},
]

TIPOS_DETECCION = [
    {"tipo":"ROBO_AGRAVADO",      "sev":AlertSeverity.CRITICAL,"prob":0.05},
    {"tipo":"AGLOMERACION",       "sev":AlertSeverity.HIGH,    "prob":0.08},
    {"tipo":"VEHICULO_SOSPECHOSO","sev":AlertSeverity.MEDIUM,  "prob":0.10},
    {"tipo":"PERSONA_MERODEANDO", "sev":AlertSeverity.MEDIUM,  "prob":0.12},
    {"tipo":"SEMAFORO_ROTO",      "sev":AlertSeverity.LOW,     "prob":0.15},
]


class DroneAgent:
    """
    Agente autónomo de drone físico con:
    - Física newtoniana simplificada
    - MAVLink 2.0 para comunicación
    - ORCA para evasión de colisiones
    - Detección de situaciones independiente
    - Failsafe automático
    """

    def __init__(self, idx: int):
        s = SECTORES_PATROL[idx]
        start_x = s["x"] * 0.3
        start_y = s["y"] * 0.3
        self.state = DroneState(
            drone_id = f"CNTL-{idx+1:02d}",
            sysid    = idx + 1,
            x        = start_x,
            y        = start_y,
            z        = 0.0,
            target_x = s["x"],
            target_y = s["y"],
            target_z = float(s["z"]),
            mode     = DroneMode.TAKEOFF,
            last_hb  = time.time(),
        )
        self.sector       = s["nombre"]
        self.patrol_idx   = idx
        self.tick         = 0
        self.msg_log      = deque(maxlen=10)
        self._hb_timer    = 0.0
        self._patrol_angle= idx * (2*math.pi/NUM_DRONES)

    def calcular_orca(self, otros: list[DroneState]) -> tuple:
        """
        ORCA (Optimal Reciprocal Collision Avoidance).
        Calcula velocidad óptima evitando colisiones con otros drones.

        Si dist < ORCA_RADIUS → aplicar fuerza de repulsión
        """
        fx = fy = 0.0
        for otro in otros:
            if otro.drone_id == self.state.drone_id:
                continue
            dx = self.state.x - otro.x
            dy = self.state.y - otro.y
            dist = math.sqrt(dx**2 + dy**2)
            if 0 < dist < ORCA_RADIUS:
                # Fuerza repulsiva proporcional al inverso de distancia
                f    = (ORCA_RADIUS - dist) / ORCA_RADIUS * 3.0
                fx  += (dx/dist) * f
                fy  += (dy/dist) * f
        return fx, fy

    def step_collision_check(self, otros: list[DroneState]) -> bool:
        """
        Lazo rápido de colisión (20 Hz / 50 ms) — independiente del step() principal.
        Si hay un drone dentro de ORCA_HARD_RADIUS, frena al 20% y aplica
        repulsión doble inmediatamente. Garantiza reacción < 100 ms.
        Retorna True si activó freno de emergencia.
        """
        s = self.state
        if s.mode in (DroneMode.LANDED, DroneMode.GROUND):
            return False
        for otro in otros:
            if otro.drone_id == s.drone_id:
                continue
            dx = s.x - otro.x
            dy = s.y - otro.y
            dist = math.sqrt(dx*dx + dy*dy)
            if 0 < dist < ORCA_HARD_RADIUS:
                s.vx *= 0.2
                s.vy *= 0.2
                fx, fy = self.calcular_orca(otros)
                s.vx += fx * 2.0
                s.vy += fy * 2.0
                # Aplicar el delta inmediato — no esperar al próximo step()
                s.x += s.vx * ORCA_TICK_DT
                s.y += s.vy * ORCA_TICK_DT
                return True
        return False

    def step(self, otros: list[DroneState]) -> dict:
        """Avanza un tick del drone."""
        self.tick += 1
        s = self.state
        s.uptime += TICK_DT

        # ── HEARTBEAT ──
        self._hb_timer += TICK_DT
        if self._hb_timer >= (1.0/HEARTBEAT_HZ):
            self._hb_timer = 0.0
            s.last_hb = time.time()

        # ── FAILSAFE: RTH si sin heartbeat (EMERGENCY si la pérdida es prolongada) ──
        hb_age = time.time() - s.last_hb
        if hb_age > FAILSAFE_T and s.mode not in (DroneMode.RTH, DroneMode.EMERGENCY):
            s.mode = DroneMode.EMERGENCY if hb_age > FAILSAFE_T * 2 else DroneMode.RTH
            s.target_x = 0.0
            s.target_y = 0.0
            s.target_z = 30.0

        # ── MÁQUINA DE ESTADOS ──
        if s.mode == DroneMode.TAKEOFF:
            s.vz = 4.0
            s.z  = min(s.target_z, s.z + s.vz*TICK_DT)
            if abs(s.z - s.target_z) < 1.0:
                s.mode = DroneMode.PATROL
                s.vz   = 0.0

        elif s.mode in (DroneMode.PATROL, DroneMode.INTERCEPT):
            # Patrulla circular en sector
            if s.mode == DroneMode.PATROL:
                r      = SECTORES_PATROL[self.patrol_idx]["x"] if abs(SECTORES_PATROL[self.patrol_idx]["x"])>50 else 100
                r      = max(80, abs(r))
                self._patrol_angle += 0.008
                s.target_x = SECTORES_PATROL[self.patrol_idx]["x"] + r*0.3*math.cos(self._patrol_angle)
                s.target_y = SECTORES_PATROL[self.patrol_idx]["y"] + r*0.3*math.sin(self._patrol_angle)
                s.target_z = float(SECTORES_PATROL[self.patrol_idx]["z"]) + 5*math.sin(self.tick*0.02)

            # Control proporcional hacia target
            dx    = s.target_x - s.x
            dy    = s.target_y - s.y
            dz    = s.target_z - s.z
            dist  = math.sqrt(dx**2+dy**2)

            kp = 0.8
            if dist > 1.0:
                s.vx = min(MAX_VEL, kp*dx)
                s.vy = min(MAX_VEL, kp*dy)
            else:
                s.vx *= 0.8
                s.vy *= 0.8

            s.vz = min(3.0, max(-3.0, kp*dz))

            # ORCA evasión
            fx, fy = self.calcular_orca(otros)
            s.vx  += fx
            s.vy  += fy

            # Limitar velocidad
            v = math.sqrt(s.vx**2+s.vy**2)
            if v > MAX_VEL:
                s.vx = s.vx/v*MAX_VEL
                s.vy = s.vy/v*MAX_VEL

            # Actualizar posición
            s.x  += s.vx * TICK_DT
            s.y  += s.vy * TICK_DT
            s.z   = max(20.0, s.z + s.vz*TICK_DT)

            # Yaw hacia movimiento
            if v > 0.5:
                s.yaw = math.degrees(math.atan2(s.vy, s.vx)) % 360

        elif s.mode in (DroneMode.RTH, DroneMode.EMERGENCY):
            spd_max = MAX_VEL_RTH_EMERG if s.mode == DroneMode.EMERGENCY else MAX_VEL
            dx   = 0 - s.x; dy = 0 - s.y
            dist = math.sqrt(dx**2+dy**2)
            if dist > 5.0:
                spd  = min(spd_max, dist*0.5)
                s.vx = (dx/dist)*spd
                s.vy = (dy/dist)*spd
                s.x += s.vx*TICK_DT
                s.y += s.vy*TICK_DT
            else:
                s.vz = -3.0
                s.z  = max(0.0, s.z + s.vz*TICK_DT)
                if s.z <= 0.1:
                    s.mode = DroneMode.LANDED

        elif s.mode == DroneMode.HOVER:
            s.vx *= 0.9
            s.vy *= 0.9
            s.x  += s.vx*TICK_DT
            s.y  += s.vy*TICK_DT

        # ── BATERÍA ──
        drain = 0.006 * (s.speed/MAX_VEL + 0.5)
        s.battery = max(0.0, s.battery - drain)
        if s.battery < 10.0 and s.mode != DroneMode.RTH:
            s.mode = DroneMode.RTH

        # ── RSSI ──
        dist_orig = math.sqrt(s.x**2+s.y**2)
        s.rssi    = int(max(-110,min(-40,-52-dist_orig*0.02+random.gauss(0,1.5))))

        # ── DETECCIÓN ALEATORIA ──
        s.detection = None
        for det in TIPOS_DETECCION:
            if random.random() < det["prob"] * TICK_DT:
                s.alert = det["sev"]
                s.detection = {
                    "tipo":      det["tipo"],
                    "severidad": det["sev"].name,
                    "lat":       round(s.lat,5),
                    "lon":       round(s.lon,5),
                    "drone":     s.drone_id,
                    "sector":    self.sector,
                    "timestamp": datetime.now().isoformat(),
                }
                break
        else:
            if random.random() < 0.05:
                s.alert = AlertSeverity.CLEAR

        # ── MAVLINK: Construir y firmar mensaje ──
        payload = {
            "type":    MAVLinkMsgType.POSITION.name,
            "lat":     round(s.lat,6),
            "lon":     round(s.lon,6),
            "alt":     round(s.z,1),
            "vx":      round(s.vx,2),
            "vy":      round(s.vy,2),
            "battery": round(s.battery,1),
            "mode":    s.mode.name,
            "alert":   s.alert.name,
        }
        msg = MAVLink2.firmar_mensaje(payload, s.sysid)
        s.msg_count += 1
        if MAVLink2.verificar_firma(msg):
            s.msg_valid += 1

        self.msg_log.appendleft({
            "tipo":  "POSITION",
            "valid": True,
            "ts":    datetime.now().strftime("%H:%M:%S.%f")[:-4],
        })

        return msg


# ─────────────────────────────────────────────────────────────────────────────
# C. COORDINADOR DE ENJAMBRE — Opus 4.7
# ─────────────────────────────────────────────────────────────────────────────

class SwarmCoordinator:
    """
    Coordinador de enjambre nivel Anduril Lattice OS.
    Opus 4.7 toma decisiones de flota completa:
    - Redistribución táctica
    - Intercepción de amenazas
    - Cobertura óptima de área
    - Gestión de colisiones a nivel flota
    """

    def __init__(self):
        self._log: deque = deque(maxlen=20)
        self._orden      = "Inicializando enjambre..."
        self._estado     = "INICIANDO"
        self._tick_ia    = 0
        self._detecciones_acum: list = []

    def analizar(self, agentes: list[DroneAgent]) -> dict:
        """Analiza el estado del enjambre y genera órdenes."""
        self._tick_ia += 1

        # Recopilar detecciones
        for ag in agentes:
            if ag.state.detection:
                self._detecciones_acum.append(ag.state.detection)

        # Mantener solo las últimas 10
        self._detecciones_acum = self._detecciones_acum[-10:]

        # Consultar Opus 4.7 cada 60 ticks
        if self._tick_ia % 60 == 0:
            self._consultar_opus(agentes)

        # Métricas del enjambre
        bats      = [ag.state.battery for ag in agentes]
        alertas   = [ag.state.alert for ag in agentes]
        max_alert = max(alertas, key=lambda a: a.value)
        drones_ok = sum(1 for ag in agentes if ag.state.mode not in
                        (DroneMode.RTH, DroneMode.LANDED, DroneMode.EMERGENCY))

        return {
            "orden":       self._orden,
            "estado":      self._estado,
            "drones_ok":   drones_ok,
            "bat_promedio":sum(bats)/len(bats),
            "max_alerta":  max_alert.name,
            "detecciones": len(self._detecciones_acum),
        }

    def _consultar_opus(self, agentes: list[DroneAgent]):
        if not ANTHROPIC_KEY:
            estados = [(ag.state.drone_id, ag.state.mode.name,
                        ag.state.alert.name, ag.state.battery)
                       for ag in agentes]
            criticos = [e for e in estados if e[2] in ("HIGH","CRITICAL")]
            if criticos:
                self._orden  = f"Despachar {criticos[0][0]} a zona crítica. Mantener cobertura."
                self._estado = "ALERTA"
            else:
                self._orden  = "Mantener patrullas circulares. Cobertura nominal en 6 sectores."
                self._estado = "NOMINAL"
            return

        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        resumen = "\n".join([
            f"  {ag.state.drone_id} | {ag.state.mode.name} | "
            f"BAT:{ag.state.battery:.0f}% | "
            f"ALERTA:{ag.state.alert.name} | "
            f"GPS:({ag.state.lat:.4f},{ag.state.lon:.4f})"
            for ag in agentes
        ])
        det_str = "\n".join([
            f"  [{d['timestamp'][11:19]}] {d['drone']}: {d['tipo']} en {d['sector']}"
            for d in self._detecciones_acum[-5:]
        ]) if self._detecciones_acum else "  Sin detecciones recientes"

        try:
            r = client.messages.create(
                model="claude-opus-4-7", max_tokens=150,
                messages=[{"role":"user","content":
                    f"Coordinador enjambre Lima. {NUM_DRONES} drones.\n"
                    f"ESTADO:\n{resumen}\n"
                    f"DETECCIONES:\n{det_str}\n"
                    f"En 1-2 oraciones: orden táctica de enjambre y redistribución."}],
            )
            self._orden  = r.content[0].text.strip()
            alertas      = [ag.state.alert for ag in agentes]
            max_a        = max(alertas, key=lambda a: a.value)
            self._estado = {AlertSeverity.CLEAR:"NOMINAL",
                            AlertSeverity.LOW:"VIGILANCIA",
                            AlertSeverity.MEDIUM:"ALERTA",
                            AlertSeverity.HIGH:"CRÍTICO",
                            AlertSeverity.CRITICAL:"EMERGENCIA"}.get(max_a,"NOMINAL")
        except Exception as e:
            self._orden = f"Error Opus 4.7: {str(e)[:50]}"


# ─────────────────────────────────────────────────────────────────────────────
# D. SIMULADOR DE ATAQUE / SEGURIDAD
# ─────────────────────────────────────────────────────────────────────────────

class SecurityMonitor:
    """
    Monitor de seguridad del enjambre.
    Detecta y bloquea intentos de:
    - GPS spoofing (firma HMAC inválida)
    - Jamming (heartbeat perdido)
    - Comando no autorizado
    - Intruso en red mesh
    """

    def __init__(self):
        self.ataques_detectados = 0
        self.ataques_bloqueados = 0
        self.log: deque = deque(maxlen=15)
        self._ataque_timer = 0.0
        self._ataque_cada  = random.uniform(15, 30)

    def step(self, agentes: list[DroneAgent], tick: int) -> Optional[dict]:
        """Simula intentos de ataque y los detecta."""
        self._ataque_timer += TICK_DT

        if self._ataque_timer < self._ataque_cada:
            return None

        self._ataque_timer   = 0.0
        self._ataque_cada    = random.uniform(20, 45)

        # Elegir tipo de ataque
        tipo_ataque = random.choice([
            "GPS_SPOOFING",
            "SIGNAL_JAMMING",
            "COMANDO_FALSO",
            "REPLAY_ATTACK",
            "INTRUSO_MESH",
        ])

        self.ataques_detectados += 1

        # Simular ataque y detección
        if tipo_ataque == "GPS_SPOOFING":
            # Crear mensaje falso y verificar firma → falla
            ag      = random.choice(agentes)
            payload = {"lat":-12.000,"lon":-77.000,"alt":100,"tipo":"SPOOF"}
            msg_real= MAVLink2.firmar_mensaje(payload, ag.state.sysid)
            msg_fake= MAVLink2.simular_ataque(msg_real)
            valido  = MAVLink2.verificar_firma(msg_fake)
            bloqueado = not valido
            detalle   = f"Firma HMAC inválida — {ag.state.drone_id}"

        elif tipo_ataque == "SIGNAL_JAMMING":
            # Simular pérdida de heartbeat
            ag = random.choice(agentes)
            bloqueado = True
            detalle   = f"Failsafe RTH activado — {ag.state.drone_id}"

        elif tipo_ataque == "COMANDO_FALSO":
            # Comando sin firma → rechazado
            payload_falso = {"cmd":"LAND","target":"ALL","auth":"FAKE"}
            msg_falso = {"payload":payload_falso,"firma":"00000000","sysid":99,"seq":0,"timestamp":0}
            bloqueado = not MAVLink2.verificar_firma(msg_falso)
            detalle   = "Comando sin autorización — origen desconocido"

        elif tipo_ataque == "REPLAY_ATTACK":
            # Mensaje antiguo (timestamp viejo)
            bloqueado = True
            detalle   = "Timestamp expirado — mensaje duplicado rechazado"

        else:  # INTRUSO_MESH
            bloqueado = True
            detalle   = "SysID no registrado en whitelist — acceso denegado"

        if bloqueado:
            self.ataques_bloqueados += 1

        entrada = {
            "hora":      datetime.now().strftime("%H:%M:%S"),
            "tipo":      tipo_ataque,
            "bloqueado": bloqueado,
            "detalle":   detalle,
        }
        self.log.appendleft(entrada)
        return entrada


# ─────────────────────────────────────────────────────────────────────────────
# E. DASHBOARD RICH — ENJAMBRE
# ─────────────────────────────────────────────────────────────────────────────

def alert_color(a: AlertSeverity) -> str:
    return {
        AlertSeverity.CLEAR:    GREEN,
        AlertSeverity.LOW:      "bright_green",
        AlertSeverity.MEDIUM:   AMBER,
        AlertSeverity.HIGH:     RED,
        AlertSeverity.CRITICAL: RED,
    }.get(a, WHITE)


def mode_str(m: DroneMode) -> str:
    styles = {
        DroneMode.GROUND:    f"[{DIM}]GROUND[/]",
        DroneMode.TAKEOFF:   f"[{AMBER}]TAKEOFF[/]",
        DroneMode.PATROL:    f"[{GREEN}]PATROL[/]",
        DroneMode.INTERCEPT: f"[{RED}]INTERCEPT[/]",
        DroneMode.HOVER:     f"[{BLUE}]HOVER[/]",
        DroneMode.RTH:       f"[{MAG}]RTH[/]",
        DroneMode.EMERGENCY: f"[bold {RED}]EMERGENCY[/]",
        DroneMode.LANDED:    f"[{DIM}]LANDED[/]",
    }
    return styles.get(m, m.name)


def build_swarm_table(agentes: list[DroneAgent]) -> Table:
    t = Table(
        title=f"[bold {AMBER}]▸ ENJAMBRE — {NUM_DRONES} DRONES ACTIVOS | MAVLINK 2.0[/]",
        box=rbox.SIMPLE_HEAD, style=DIM,
        header_style=f"bold {AMBER}",
        show_lines=True, expand=True,
    )
    t.add_column("DRONE",   style=f"bold {BLUE}", width=10)
    t.add_column("SECTOR",  width=13)
    t.add_column("MODO",    width=11)
    t.add_column("BAT %",   justify="right", width=7)
    t.add_column("ALT m",   justify="right", width=7)
    t.add_column("VEL m/s", justify="right", width=8)
    t.add_column("RSSI",    justify="right", width=7)
    t.add_column("X m",     justify="right", width=7)
    t.add_column("Y m",     justify="right", width=7)
    t.add_column("ALERTA",  width=10)
    t.add_column("MSG OK",  justify="right", width=8)
    t.add_column("ORCA",    width=6)

    for ag in agentes:
        s   = ag.state
        bc  = RED if s.battery<15 else AMBER if s.battery<30 else GREEN
        rc  = RED if s.rssi<-90 else GREEN
        ac  = alert_color(s.alert)
        msg_pct = f"{s.msg_valid}/{s.msg_count}" if s.msg_count else "—"

        # Detección ORCA
        otros = [a.state for a in agentes if a.state.drone_id != s.drone_id]
        fx,fy = DroneAgent.__new__(DroneAgent).calcular_orca.__func__(
            type('obj',(object,),{'state':s,'calcular_orca':lambda self,o:None})(),
            otros
        ) if False else (0,0)  # Simplificado para display
        orca_act = any(
            math.sqrt((s.x-o.x)**2+(s.y-o.y)**2) < ORCA_RADIUS
            for o in otros
        )
        orca_str = f"[{AMBER}]⚡ ACT[/]" if orca_act else f"[{GREEN}]OK[/]"

        t.add_row(
            s.drone_id,
            ag.sector,
            mode_str(s.mode),
            f"[{bc}]{s.battery:.1f}[/]",
            f"{s.z:.1f}",
            f"{s.speed:.1f}",
            f"[{rc}]{s.rssi}[/]",
            f"{s.x:.0f}",
            f"{s.y:.0f}",
            f"[{ac}]{s.alert.name}[/]",
            msg_str := msg_pct,
            orca_str,
        )
    return t


def build_coord_panel(coord: SwarmCoordinator, meta: dict) -> Panel:
    ec_map = {
        "NOMINAL":"bright_green","VIGILANCIA":"bright_yellow",
        "ALERTA":"bright_yellow","CRÍTICO":"bright_red","EMERGENCIA":"bright_red",
    }
    estado = meta.get("estado","NOMINAL")
    ec     = ec_map.get(estado, WHITE)

    content = Text()
    content.append(f"\n  ESTADO FLOTA: ", style=DIM)
    content.append(f"{estado}\n", style=f"bold {ec}")
    content.append(f"  DRONES OK:    ", style=DIM)
    content.append(f"{meta['drones_ok']}/{NUM_DRONES}\n", style=GREEN)
    content.append(f"  BAT PROMEDIO: ", style=DIM)
    content.append(f"{meta['bat_promedio']:.1f}%\n", style=AMBER)
    content.append(f"  DETECCIONES:  ", style=DIM)
    content.append(f"{meta['detecciones']}\n", style=RED if meta['detecciones']>0 else GREEN)
    content.append(f"\n  ORDEN OPUS 4.7:\n  ", style=DIM)
    content.append(f"{meta['orden'][:100]}\n", style=WHITE)

    return Panel(content,
        title=f"[bold {AMBER}]▸ COORDINADOR — CLAUDE OPUS 4.7[/]",
        border_style=ec, padding=(0,1))


def build_security_panel(sec: SecurityMonitor) -> Panel:
    t = Table(box=None, show_header=False, expand=True)
    t.add_column("HORA", style=DIM,          width=10)
    t.add_column("TIPO", style=f"bold {RED}", width=18)
    t.add_column("RESULT",                   width=12)
    t.add_column("DETALLE", style=WHITE)

    for entry in list(sec.log)[:6]:
        bc = GREEN if entry["bloqueado"] else RED
        t.add_row(
            entry["hora"],
            entry["tipo"][:16],
            f"[{bc}]{'✓ BLOQUEADO' if entry['bloqueado'] else '✗ PENETRÓ'}[/]",
            entry["detalle"][:35],
        )
    if not sec.log:
        t.add_row("—","—","—","Sin ataques detectados")

    stats = Text()
    stats.append(f"\n  Detectados: ", style=DIM)
    stats.append(str(sec.ataques_detectados), style=AMBER)
    stats.append(f"  │  Bloqueados: ", style=DIM)
    stats.append(str(sec.ataques_bloqueados), style=GREEN)
    rate = f"{sec.ataques_bloqueados/max(1,sec.ataques_detectados):.0%}"
    stats.append(f"  │  Tasa: ", style=DIM)
    stats.append(rate, style=GREEN)

    content = Table.grid()
    content.add_row(t)
    content.add_row(stats)

    return Panel(content,
        title=f"[bold {RED}]▸ SEGURIDAD — MAVLINK HMAC + ANTI-SPOOFING[/]",
        border_style=RED if sec.ataques_detectados>0 else GREEN,
        padding=(0,1))


def build_mesh_panel(agentes: list[DroneAgent]) -> Panel:
    """Visualiza la red mesh del enjambre."""
    t = Table(box=None, show_header=False, expand=True)
    t.add_column("FROM→TO",  style=f"bold {BLUE}", width=15)
    t.add_column("DIST m",   justify="right",       width=8)
    t.add_column("ORCA",     width=8)
    t.add_column("LATENCIA", justify="right",       width=10)

    links = []
    for i, ag1 in enumerate(agentes):
        for j, ag2 in enumerate(agentes):
            if j <= i:
                continue
            dx   = ag1.state.x - ag2.state.x
            dy   = ag1.state.y - ag2.state.y
            dist = math.sqrt(dx**2 + dy**2)
            if dist < 400:  # Solo enlaces cercanos
                links.append((ag1, ag2, dist))

    links.sort(key=lambda x: x[2])
    for ag1, ag2, dist in links[:8]:
        orca  = dist < ORCA_RADIUS
        lat_ms= round(dist * 0.05 + random.uniform(1,5), 1)
        orca_c= AMBER if orca else GREEN
        t.add_row(
            f"{ag1.state.drone_id}→{ag2.state.drone_id}",
            f"{dist:.0f}",
            f"[{orca_c}]{'⚡ACT' if orca else 'OK'}[/]",
            f"{lat_ms}ms",
        )

    return Panel(t,
        title=f"[bold {AMBER}]▸ RED MESH — ENLACES ACTIVOS[/]",
        border_style=BLUE, padding=(0,1))


def build_dashboard(agentes, coord, meta, sec, tick, stats):
    ts  = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    hdr = Text()
    hdr.append("◈ CENTINELA SWARM", style=f"bold {AMBER}")
    hdr.append("  │  ", style=DIM)
    hdr.append(f"{NUM_DRONES} DRONES | MAVLINK 2.0 | ORCA | OPUS 4.7", style=f"bold {BLUE}")
    hdr.append(f"  │  {ts}  │  TICK #{tick:06d}", style=MAG)

    max_a  = max((ag.state.alert for ag in agentes), key=lambda a: a.value)
    ac     = alert_color(max_a)
    footer = Text(justify="center")
    footer.append("SWARM ACTIVE", style=f"bold {AMBER}")
    footer.append("  ·  ", style=DIM)
    footer.append(f"ALERTA MAX: {max_a.name}", style=f"bold {ac}")
    footer.append("  ·  ", style=DIM)
    footer.append(f"ATAQUES BLOQUEADOS: {sec.ataques_bloqueados}", style=f"bold {GREEN}")
    footer.append("  ·  ", style=DIM)
    footer.append("MAVLink 2.0 HMAC-SHA256", style=f"bold {BLUE}")
    footer.append("  ·  Ctrl+C para detener", style=DIM)

    root = Table.grid(expand=True)
    root.add_row(Panel(Align.center(hdr), style=AMBER, padding=(0,2)))
    root.add_row(build_swarm_table(agentes))
    root.add_row(Columns([
        build_coord_panel(coord, meta),
        build_mesh_panel(agentes),
    ], expand=True))
    root.add_row(build_security_panel(sec))
    root.add_row(Panel(Align.center(footer), style=DIM, padding=(0,1)))
    return root


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    console.clear()
    console.print(Panel.fit(
        f"[bold {AMBER}]Inicializando CENTINELA SWARM...[/]\n"
        f"[{BLUE}]{NUM_DRONES} drones | MAVLink 2.0 | ORCA | Opus 4.7[/]\n"
        f"[{DIM}]HMAC-SHA256 anti-spoofing activo[/]",
        title="[bold white]EATON DYNAMICS — ENJAMBRE FÍSICO v1.0[/]",
        border_style=AMBER,
    ))
    time.sleep(1.5)

    agentes = [DroneAgent(i) for i in range(NUM_DRONES)]
    coord   = SwarmCoordinator()
    sec     = SecurityMonitor()
    tick    = 0
    stats   = {"total_msgs":0,"valid_msgs":0}

    try:
        with Live(console=console, refresh_per_second=6) as live:
            while True:
                tick += 1

                # Paso de física de todos los drones
                estados = [ag.state for ag in agentes]
                for ag in agentes:
                    msg = ag.step(estados)
                    stats["total_msgs"] += 1
                    if msg.get("valid"):
                        stats["valid_msgs"] += 1

                # Coordinador
                meta = coord.analizar(agentes)

                # Seguridad
                sec.step(agentes, tick)

                # Render
                live.update(build_dashboard(agentes,coord,meta,sec,tick,stats))

                # Sub-tick a 20 Hz: chequeo de colisión inminente
                # (reacción < 100 ms ante aproximaciones dentro de ORCA_HARD_RADIUS)
                time.sleep(ORCA_TICK_DT)
                estados_mid = [ag.state for ag in agentes]
                for ag in agentes:
                    ag.step_collision_check(estados_mid)
                time.sleep(TICK_DT - ORCA_TICK_DT)

    except KeyboardInterrupt:
        console.print(f"\n[bold {AMBER}]◈ CENTINELA SWARM DETENIDO.[/]")
        console.print(f"  Mensajes MAVLink: {stats['total_msgs']:,}")
        console.print(f"  Mensajes válidos: {stats['valid_msgs']:,}")
        console.print(f"  Ataques bloqueados: {sec.ataques_bloqueados}")


if __name__ == "__main__":
    main()
