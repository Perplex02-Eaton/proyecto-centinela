"""
PROYECTO CENTINELA — THREAT SCORE v1.0
Índice de amenaza unificado 0-100 que combina todos los módulos

Fórmula compuesta:
  TS = w_tel * f_telemetría
     + w_vis * f_visión
     + w_ml  * f_predicción_ml
     + w_alt * f_alertas_activas
     + w_inc * f_incidentes_recientes

Niveles:
   0 - 20  → VERDE    — Situación nominal
  21 - 40  → AMARILLO — Vigilancia elevada
  41 - 60  → NARANJA  — Alerta táctica
  61 - 80  → ROJO     — Crisis activa
  81 - 100 → CRÍTICO  — Emergencia mayor

Acciones automáticas por umbral:
  > 40  → Aumentar frecuencia de patrullaje
  > 60  → Activar drones de reserva
  > 80  → Alerta Telegram CRÍTICA + reporte PDF automático
  ≥ 85  → MODO EMERGENCIA — auto-deploy de TODOS los drones disponibles
          (controlado por env var CENTINELA_AUTO_DEPLOY=1; default 0 / disabled)
"""

import os, time, math, random, json
from datetime import datetime
from collections import deque
from typing import Optional, Callable
import urllib.request, urllib.parse
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

ANTHROPIC_KEY    = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_TOKEN   = "8676501832:AAEYlMd3GogjL-tubZzOFUSfudhx_loh8lk"
TELEGRAM_CHAT_ID = "1638287560"

# Pesos del Threat Score (suman 1.0)
PESOS = {
    "telemetria": 0.25,   # Estado de la flota
    "vision":     0.25,   # Detección visual IA
    "ml_pred":    0.20,   # Predicción ML zona
    "alertas":    0.20,   # Alertas activas
    "incidentes": 0.10,   # Incidentes recientes
}

# Umbrales de acción automática
UMBRAL_VIGILANCIA      = 40
UMBRAL_CRISIS          = 60
UMBRAL_EMERGENCIA      = 80     # Telegram crítico + reporte PDF
UMBRAL_EMERGENCIA_AUTO = 85     # Auto-deploy de TODOS los drones (sin operador)
AUTO_DEPLOY_COOLDOWN   = 60     # segundos entre activaciones (anti-oscilación)
AUTO_DEPLOY_ENABLED    = os.getenv("CENTINELA_AUTO_DEPLOY", "0") == "1"

# Física
NUM_DRONES    = 6
REF_LAT       = -12.0464
REF_LON       = -77.0428
GRAVITY       = 9.81
DRONE_MASS    = 1.2
HOVER_THRUST  = DRONE_MASS * GRAVITY
BATTERY_DRAIN = 0.006

SECTORES = [
    {"nombre":"Miraflores",  "radio":150,"angulo":0.0,   "alt":80},
    {"nombre":"San Isidro",  "radio":200,"angulo":1.047, "alt":90},
    {"nombre":"Barranco",    "radio":130,"angulo":2.094, "alt":70},
    {"nombre":"Surquillo",   "radio":180,"angulo":3.141, "alt":85},
    {"nombre":"La Victoria", "radio":160,"angulo":4.189, "alt":75},
    {"nombre":"Lince",       "radio":140,"angulo":5.236, "alt":80},
]

PATRON_RIESGO = {
    "Miraflores":  [5,3,3,2,2,3,5,8,10,12,12,10,10,10,8,8,10,12,15,18,20,18,12,8],
    "San Isidro":  [4,2,2,2,2,3,5,10,15,18,18,15,15,15,12,10,12,15,18,20,18,15,10,6],
    "Barranco":    [30,35,40,38,25,10,5,4,4,5,6,6,6,6,5,5,6,8,12,18,22,28,32,35],
    "Surquillo":   [25,20,18,15,10,8,6,8,10,12,12,10,10,10,10,10,12,15,18,20,22,25,28,28],
    "La Victoria": [20,15,12,10,8,8,10,15,18,20,22,20,18,18,15,15,18,20,25,28,28,25,22,20],
    "Lince":       [15,12,10,8,6,5,5,6,8,10,10,8,8,8,6,6,8,10,12,15,18,20,18,15],
}

AMBER = "bright_yellow"
BLUE  = "bright_cyan"
RED   = "bright_red"
GREEN = "bright_green"
DIM   = "dim white"
MAG   = "magenta"
WHITE = "white"
ORANGE = "orange1"

console = Console()

# ─────────────────────────────────────────────────────────────────────────────
# SIMULADORES DE SUBSISTEMAS
# ─────────────────────────────────────────────────────────────────────────────

class SimuladorFlota:
    """Simula estado de la flota sin PyBullet (liviano para el Threat Score)."""

    def __init__(self):
        self.batteries    = [random.uniform(70, 98) for _ in range(NUM_DRONES)]
        self.motor_health = [random.uniform(0.90, 1.0) for _ in range(NUM_DRONES)]
        self.rssi         = [random.randint(-75, -50) for _ in range(NUM_DRONES)]
        self.tick         = 0

    def step(self) -> list:
        self.tick += 1
        estados = []
        for i in range(NUM_DRONES):
            self.batteries[i]    = max(0, self.batteries[i] - BATTERY_DRAIN)
            self.motor_health[i] = max(0, self.motor_health[i] - random.gauss(0, 0.001))
            self.rssi[i]         = max(-110, min(-40, self.rssi[i] + random.randint(-2, 2)))
            estados.append({
                "drone_id":    f"CNTL-{i+1:02d}",
                "sector":      SECTORES[i]["nombre"],
                "battery_pct": self.batteries[i],
                "motor_health":self.motor_health[i],
                "rssi_dbm":    self.rssi[i],
            })
        return estados

    def score_telemetria(self, estados: list) -> float:
        """
        f_tel = 100 * (1 - salud_promedio)
        salud = promedio(bat/100, motor_health, rssi_norm)
        """
        scores = []
        for e in estados:
            bat_s   = e["battery_pct"] / 100.0
            mot_s   = e["motor_health"]
            rssi_s  = (e["rssi_dbm"] - (-110)) / (-40 - (-110))
            scores.append((bat_s + mot_s + rssi_s) / 3.0)
        salud = sum(scores) / len(scores)
        return round((1.0 - salud) * 100, 2)


class SimuladorVision:
    """Simula detecciones de visión IA con variación temporal realista."""

    NIVELES = {"BAJO":10, "MEDIO":35, "ALTO":65, "CRITICO":90}

    def __init__(self):
        self.nivel_actual = "BAJO"
        self.tick = 0

    def step(self) -> dict:
        self.tick += 1
        hora = datetime.now().hour

        # Probabilidad de escalada según hora
        if hora in (22, 23, 0, 1, 2):
            prob_alto = 0.25
        elif hora in (18, 19, 20, 21):
            prob_alto = 0.15
        else:
            prob_alto = 0.05

        r = random.random()
        if r < prob_alto * 0.3:
            self.nivel_actual = "CRITICO"
        elif r < prob_alto:
            self.nivel_actual = "ALTO"
        elif r < prob_alto * 3:
            self.nivel_actual = "MEDIO"
        else:
            self.nivel_actual = "BAJO"

        sector = random.choice(SECTORES)["nombre"]
        return {
            "nivel":   self.nivel_actual,
            "sector":  sector,
            "score":   self.NIVELES[self.nivel_actual] + random.uniform(-5, 5),
        }


class SimuladorML:
    """Predicción ML simplificada (sin entrenar modelo completo)."""

    def predecir(self) -> dict:
        hora = datetime.now().hour
        dow  = datetime.now().weekday()

        predicciones = {}
        for s in SECTORES:
            base = PATRON_RIESGO[s["nombre"]][hora] / 100.0
            if dow >= 5:
                base *= 1.3
            base = max(0.0, min(1.0, base + random.gauss(0, 0.03)))
            predicciones[s["nombre"]] = round(base * 100, 1)

        max_sector = max(predicciones, key=predicciones.get)
        return {
            "predicciones": predicciones,
            "max_sector":   max_sector,
            "max_prob":     predicciones[max_sector],
            "score":        predicciones[max_sector],
        }


class GestorAlertas:
    """Simula alertas activas del sistema."""

    def __init__(self):
        self.alertas = []
        self.tick = 0

    def step(self, estados_flota: list) -> list:
        self.tick += 1
        alertas = []
        for e in estados_flota:
            if e["battery_pct"] < 15:
                alertas.append({"tipo":"BATTERY_CRITICAL","drone":e["drone_id"],"sev":4})
            elif e["battery_pct"] < 25:
                alertas.append({"tipo":"BATTERY_LOW","drone":e["drone_id"],"sev":2})
            if e["rssi_dbm"] < -92:
                alertas.append({"tipo":"GPS_LOSS","drone":e["drone_id"],"sev":3})
            if e["motor_health"] < 0.72:
                alertas.append({"tipo":"MOTOR_FAIL","drone":e["drone_id"],"sev":3})

        # Alerta aleatoria de incidente
        if random.random() < 0.08:
            alertas.append({"tipo":"INCIDENTE_DETECTADO","drone":"SISTEMA","sev":random.randint(2,4)})

        self.alertas = alertas
        return alertas

    def score_alertas(self) -> float:
        if not self.alertas:
            return 0.0
        sev_total = sum(a["sev"] for a in self.alertas)
        return min(100.0, sev_total * 8.0)


class GestorIncidentes:
    """Rastrea incidentes recientes y su impacto en el Threat Score."""

    def __init__(self):
        self.log: deque = deque(maxlen=10)
        self.tick = 0

    def step(self) -> Optional[dict]:
        self.tick += 1
        if random.random() < 0.04:
            sev = random.choice(["MEDIO","ALTO","ALTO","CRITICO"])
            inc = {
                "tipo":    random.choice(["ROBO","ACCIDENTE","AGLOMERACION","INTRUSION"]),
                "sector":  random.choice(SECTORES)["nombre"],
                "sev":     sev,
                "ts":      datetime.now(),
            }
            self.log.appendleft(inc)
            return inc
        return None

    def score_incidentes(self) -> float:
        if not self.log:
            return 0.0
        pesos_sev = {"BAJO":10,"MEDIO":30,"ALTO":60,"CRITICO":90}
        score = 0.0
        for i, inc in enumerate(self.log):
            age_min = (datetime.now() - inc["ts"]).total_seconds() / 60
            decay   = math.exp(-age_min / 15)  # decay exponencial 15min
            score  += pesos_sev.get(inc["sev"], 30) * decay
        return min(100.0, score)


# ─────────────────────────────────────────────────────────────────────────────
# MOTOR DEL THREAT SCORE
# ─────────────────────────────────────────────────────────────────────────────

class ThreatScoreEngine:
    """
    Calcula el Threat Score unificado cada tick.

    TS = Σ wᵢ · fᵢ  donde Σwᵢ = 1.0

    Componentes:
      f_tel  = score de telemetría de flota (0-100)
      f_vis  = score de visión IA (0-100)
      f_ml   = score de predicción ML (0-100)
      f_alt  = score de alertas activas (0-100)
      f_inc  = score de incidentes recientes (0-100)
    """

    def __init__(self):
        self.flota     = SimuladorFlota()
        self.vision    = SimuladorVision()
        self.ml        = SimuladorML()
        self.alertas   = GestorAlertas()
        self.incidentes= GestorIncidentes()
        self.historial : deque = deque(maxlen=60)
        self.incidentes_log: deque = deque(maxlen=10)
        self.ts_actual  = 0.0
        self.nivel_ant  = "VERDE"

    def calcular(self) -> dict:
        # Step subsistemas
        estados_flota = self.flota.step()
        vision_data   = self.vision.step()
        ml_data       = self.ml.predecir()
        alertas_act   = self.alertas.step(estados_flota)
        nuevo_inc     = self.incidentes.step()

        if nuevo_inc:
            self.incidentes_log.appendleft(nuevo_inc)

        # Scores componentes
        f_tel = self.flota.score_telemetria(estados_flota)
        f_vis = float(vision_data["score"])
        f_ml  = float(ml_data["score"])
        f_alt = self.alertas.score_alertas()
        f_inc = self.incidentes.score_incidentes()

        # Threat Score ponderado
        ts = (
            PESOS["telemetria"] * f_tel +
            PESOS["vision"]     * f_vis +
            PESOS["ml_pred"]    * f_ml  +
            PESOS["alertas"]    * f_alt +
            PESOS["incidentes"] * f_inc
        )
        ts = round(max(0.0, min(100.0, ts)), 2)
        self.ts_actual = ts

        # Nivel
        if ts >= 81:   nivel = "CRÍTICO"
        elif ts >= 61: nivel = "ROJO"
        elif ts >= 41: nivel = "NARANJA"
        elif ts >= 21: nivel = "AMARILLO"
        else:          nivel = "VERDE"

        resultado = {
            "ts":            ts,
            "nivel":         nivel,
            "componentes":   {
                "telemetria": round(f_tel, 2),
                "vision":     round(f_vis, 2),
                "ml_pred":    round(f_ml, 2),
                "alertas":    round(f_alt, 2),
                "incidentes": round(f_inc, 2),
            },
            "estados_flota": estados_flota,
            "vision_data":   vision_data,
            "ml_data":       ml_data,
            "alertas_act":   alertas_act,
            "nuevo_incidente":nuevo_inc,
            "nivel_anterior": self.nivel_ant,
        }
        self.nivel_ant = nivel
        self.historial.appendleft(round(ts, 1))
        return resultado


# ─────────────────────────────────────────────────────────────────────────────
# ACCIONES AUTOMÁTICAS POR UMBRAL
# ─────────────────────────────────────────────────────────────────────────────

class AccionesAutomaticas:
    """
    Acciones automáticas por umbral.

    Modo EMERGENCIA (TS ≥ 85): si AUTO_DEPLOY_ENABLED es True, dispara el
    callback on_emergency_deploy(ts, nivel) sin esperar confirmación del
    operador. Cada activación queda registrada en self.deploy_log y se
    persiste en reportes/auto_deploys.json (audit trail). Anti-oscilación
    garantizada por AUTO_DEPLOY_COOLDOWN.
    """

    def __init__(self, on_emergency_deploy: Optional[Callable[[float, str], None]] = None):
        self._ultimo_40   = 0.0
        self._ultimo_60   = 0.0
        self._ultimo_80   = 0.0
        self._ultimo_auto = 0.0
        self._on_emergency_deploy = on_emergency_deploy
        self.log: deque         = deque(maxlen=20)
        self.deploy_log: deque  = deque(maxlen=50)

    def _persist_deploy(self, entry: dict):
        """Guarda el evento de auto-deploy en JSON (best-effort, no bloquea)."""
        try:
            log_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "reportes", "auto_deploys.json")
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            try:
                with open(log_path, "r") as f:
                    logs = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                logs = []
            logs.append(entry)
            with open(log_path, "w") as f:
                json.dump(logs[-200:], f, indent=2)
        except Exception:
            pass

    def evaluar(self, resultado: dict):
        # NoneType hardening — defaults defensivos en lugar de KeyError
        ts    = float(resultado.get("ts", 0.0) or 0.0)
        nivel = resultado.get("nivel", "?") or "?"
        now   = time.time()

        # ── MODO EMERGENCIA: auto-deploy a TS ≥ 85 ──
        if (AUTO_DEPLOY_ENABLED
                and ts >= UMBRAL_EMERGENCIA_AUTO
                and now - self._ultimo_auto > AUTO_DEPLOY_COOLDOWN):
            self._ultimo_auto = now
            entry = {
                "iso":      datetime.now().isoformat(),
                "ts_score": round(ts, 2),
                "nivel":    nivel,
                "trigger":  "AUTO_THRESHOLD_85",
                "cooldown": AUTO_DEPLOY_COOLDOWN,
            }
            self.deploy_log.appendleft(entry)
            self._persist_deploy(entry)

            if self._on_emergency_deploy is not None:
                try:
                    self._on_emergency_deploy(ts, nivel)
                except Exception as e:
                    self.log.appendleft({
                        "ts": ts, "accion": f"DEPLOY ERROR: {type(e).__name__}",
                        "nivel": nivel,
                        "hora": datetime.now().strftime("%H:%M:%S"),
                    })

            msg = (
                f"🚨🚨🚨 <b>MODO EMERGENCIA — AUTO-DEPLOY</b> 🚨🚨🚨\n\n"
                f"📊 <b>Score:</b> {ts:.1f}/100\n"
                f"🔴 <b>Nivel:</b> {nivel}\n"
                f"⚡ <b>Acción:</b> Todos los drones disponibles desplegados\n"
                f"🤖 <b>Sin confirmación del operador</b> (umbral ≥ 85)\n"
                f"🕐 {datetime.now().strftime('%H:%M:%S')}\n\n"
                f"<i>Evento registrado en auto_deploys.json</i>"
            )
            enviar_telegram(msg)
            self.log.appendleft({
                "ts": ts, "accion": "AUTO-DEPLOY", "nivel": nivel,
                "hora": datetime.now().strftime("%H:%M:%S"),
            })
            return

        # ── Tier 80 — Telegram crítico (sin auto-deploy) ──
        if ts >= UMBRAL_EMERGENCIA and now - self._ultimo_80 > 120:
            self._ultimo_80 = now
            estado_auto = "ARMADO" if AUTO_DEPLOY_ENABLED else "DESHABILITADO (testing)"
            msg = (
                f"🚨🚨 <b>THREAT SCORE CRÍTICO</b> 🚨🚨\n\n"
                f"📊 <b>Score:</b> {ts:.1f}/100\n"
                f"🔴 <b>Nivel:</b> {nivel}\n"
                f"⚡ <b>Acción:</b> Drones activados\n"
                f"📋 <b>Reporte PDF generado automáticamente</b>\n"
                f"🤖 <b>Auto-deploy ≥85:</b> {estado_auto}\n"
                f"🕐 {datetime.now().strftime('%H:%M:%S')}\n\n"
                f"<i>CENTINELA — EATON DYNAMICS · Lima, Perú</i>"
            )
            enviar_telegram(msg)
            self.log.appendleft({
                "ts": ts, "accion": "EMERGENCIA MAYOR", "nivel": nivel,
                "hora": datetime.now().strftime("%H:%M:%S"),
            })

        elif ts >= UMBRAL_CRISIS and now - self._ultimo_60 > 90:
            self._ultimo_60 = now
            msg = (
                f"🔴 <b>THREAT SCORE — CRISIS ACTIVA</b>\n\n"
                f"📊 <b>Score:</b> {ts:.1f}/100\n"
                f"⚠️ <b>Nivel:</b> {nivel}\n"
                f"🚁 <b>Acción:</b> Activando drones de reserva\n"
                f"🕐 {datetime.now().strftime('%H:%M:%S')}"
            )
            enviar_telegram(msg)
            self.log.appendleft({
                "ts": ts, "accion": "CRISIS ACTIVA", "nivel": nivel,
                "hora": datetime.now().strftime("%H:%M:%S"),
            })

        elif ts >= UMBRAL_VIGILANCIA and now - self._ultimo_40 > 60:
            self._ultimo_40 = now
            self.log.appendleft({
                "ts": ts, "accion": "Vigilancia elevada", "nivel": nivel,
                "hora": datetime.now().strftime("%H:%M:%S"),
            })


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
# DASHBOARD RICH — THREAT SCORE
# ─────────────────────────────────────────────────────────────────────────────

def nivel_color(nivel: str) -> str:
    return {
        "VERDE":   GREEN,
        "AMARILLO":AMBER,
        "NARANJA": ORANGE,
        "ROJO":    RED,
        "CRÍTICO": RED,
    }.get(nivel, WHITE)


def build_gauge(ts: float, nivel: str) -> Text:
    """Gauge ASCII del Threat Score — el elemento central del dashboard."""
    nc     = nivel_color(nivel)
    filled = int(ts / 2.5)   # 40 chars = 100%
    empty  = 40 - filled
    barra  = "█" * filled + "░" * empty

    t = Text(justify="center")
    t.append("\n")
    t.append("╔══════════════════════════════════════════════╗\n", style=DIM)
    t.append("║          CENTINELA  THREAT  SCORE            ║\n", style=f"bold {AMBER}")
    t.append("╠══════════════════════════════════════════════╣\n", style=DIM)
    t.append("║                                              ║\n", style=DIM)
    t.append("║    ", style=DIM)
    t.append(f"{ts:6.1f}", style=f"bold {nc}")
    t.append(" / 100", style=WHITE)
    t.append("                              ║\n", style=DIM)
    t.append("║    ", style=DIM)
    t.append(barra, style=f"bold {nc}")
    t.append(" ║\n", style=DIM)
    t.append("║    0%         25%        50%        75%  100% ║\n", style=DIM)
    t.append("║                                              ║\n", style=DIM)
    t.append("║    NIVEL: ", style=DIM)
    t.append(f"{'■ ' + nivel:20}", style=f"bold {nc}")
    t.append("               ║\n", style=DIM)
    t.append("╚══════════════════════════════════════════════╝\n", style=DIM)
    return t


def build_componentes_panel(comp: dict, pesos: dict) -> Panel:
    t = Table(box=None, show_header=True, expand=True, padding=(0,1))
    t.add_column("COMPONENTE",   style=f"bold {AMBER}", width=14)
    t.add_column("PESO",         style=DIM,             width=7)
    t.add_column("SCORE RAW",    justify="right",       width=10)
    t.add_column("CONTRIBUCIÓN", justify="right",       width=12)
    t.add_column("BARRA",        width=22)

    componentes = [
        ("TELEMETRÍA",  "telemetria"),
        ("VISIÓN IA",   "vision"),
        ("ML PRED.",    "ml_pred"),
        ("ALERTAS",     "alertas"),
        ("INCIDENTES",  "incidentes"),
    ]

    for nombre, key in componentes:
        raw  = comp[key]
        peso = pesos[key]
        cont = raw * peso
        nc   = RED if raw>60 else AMBER if raw>30 else GREEN
        bar  = "█" * int(raw/5) + "░" * (20-int(raw/5))
        t.add_row(
            nombre,
            f"{peso:.0%}",
            f"[{nc}]{raw:.1f}[/]",
            f"[{nc}]{cont:.2f}[/]",
            f"[{nc}]{bar[:20]}[/]",
        )

    return Panel(t,
        title=f"[bold {AMBER}]▸ DESGLOSE DE COMPONENTES — THREAT SCORE[/]",
        border_style=BLUE, padding=(0,1))


def build_historial_chart(historial: deque) -> Panel:
    """Mini chart ASCII del historial del Threat Score."""
    vals = list(historial)[:40]
    if not vals:
        return Panel(Text("Sin datos aún...", style=DIM),
                     title=f"[bold {AMBER}]▸ HISTORIAL TS[/]", border_style=DIM)

    max_v = max(vals) if vals else 100
    min_v = min(vals) if vals else 0
    H     = 6
    lines = []

    for row in range(H, 0, -1):
        threshold = min_v + (max_v - min_v) * row / H
        line_txt  = Text()
        label_val = min_v + (max_v - min_v) * row / H
        label_col = RED if label_val > 60 else AMBER if label_val > 30 else GREEN
        line_txt.append(f"{label_val:5.0f} │", style=f"{label_col}")
        for v in reversed(vals):
            nc = RED if v > 60 else AMBER if v > 30 else GREEN
            line_txt.append("█" if v >= threshold else " ", style=nc)
        lines.append(line_txt)

    axis = Text("      └" + "─" * min(len(vals), 40), style=DIM)
    full = Text("\n").join(lines)
    full.append("\n")
    full.append(axis)

    return Panel(full,
        title=f"[bold {AMBER}]▸ HISTORIAL THREAT SCORE (últimos {len(vals)} ticks)[/]",
        border_style=AMBER, padding=(0,1))


def build_flota_mini(estados: list) -> Panel:
    t = Table(box=None, show_header=False, expand=True, padding=(0,1))
    t.add_column("DRONE", style=f"bold {BLUE}", width=9)
    t.add_column("BAT",   justify="right",      width=7)
    t.add_column("MOT",   justify="right",      width=7)
    t.add_column("RSSI",  justify="right",      width=7)
    t.add_column("SECTOR",style=DIM,            width=13)

    for e in estados:
        bc = RED if e["battery_pct"]<15 else AMBER if e["battery_pct"]<30 else GREEN
        mc = RED if e["motor_health"]<0.72 else GREEN
        rc = RED if e["rssi_dbm"]<-90 else GREEN
        t.add_row(
            e["drone_id"],
            f"[{bc}]{e['battery_pct']:.0f}%[/]",
            f"[{mc}]{e['motor_health']:.2f}[/]",
            f"[{rc}]{e['rssi_dbm']}[/]",
            e["sector"],
        )
    return Panel(t,
        title=f"[bold {AMBER}]▸ ESTADO FLOTA[/]",
        border_style=BLUE, padding=(0,1))


def build_acciones_panel(acciones_log: deque) -> Panel:
    t = Table(box=None, show_header=False, expand=True)
    t.add_column("HORA", style=DIM, width=9)
    t.add_column("TS",   justify="right", width=7)
    t.add_column("NIVEL",width=10)
    t.add_column("ACCIÓN",style=WHITE)

    for a in list(acciones_log)[:5]:
        nc = RED if a["ts"]>=80 else AMBER if a["ts"]>=60 else "bright_yellow"
        t.add_row(
            a["hora"],
            f"[{nc}]{a['ts']:.1f}[/]",
            f"[{nc}]{a['nivel']}[/]",
            a["accion"][:35],
        )
    if not acciones_log:
        t.add_row("—","—","—","Sin acciones automáticas aún")

    return Panel(t,
        title=f"[bold {AMBER}]▸ ACCIONES AUTOMÁTICAS[/]",
        border_style=RED if acciones_log else DIM,
        padding=(0,1))


def build_dashboard(resultado: dict, historial: deque,
                    acciones_log: deque, tick: int,
                    stats: dict) -> Table:
    ts    = resultado["ts"]
    nivel = resultado["nivel"]
    nc    = nivel_color(nivel)
    now   = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")

    hdr = Text()
    hdr.append("◈ CENTINELA THREAT SCORE", style=f"bold {AMBER}")
    hdr.append("  │  ", style=DIM)
    hdr.append("ÍNDICE UNIFICADO DE AMENAZA", style=f"bold {BLUE}")
    hdr.append(f"  │  {now}  │  TICK #{tick:05d}", style=MAG)

    footer = Text(justify="center")
    footer.append("THREAT SCORE ACTIVE", style=f"bold {AMBER}")
    footer.append("  ·  ", style=DIM)
    footer.append(f"TS: {ts:.1f}/100", style=f"bold {nc}")
    footer.append("  ·  ", style=DIM)
    footer.append(f"NIVEL: {nivel}", style=f"bold {nc}")
    footer.append("  ·  ", style=DIM)
    footer.append(f"ACCIONES AUTO: {stats['acciones']}", style=AMBER)
    footer.append("  ·  Ctrl+C para detener", style=DIM)

    root = Table.grid(expand=True)
    root.add_row(Panel(Align.center(hdr), style=AMBER, padding=(0,2)))

    # Gauge central
    gauge_panel = Panel(
        Align.center(build_gauge(ts, nivel)),
        border_style=nc, padding=(0,2),
    )

    # Columna derecha: ML + Vision
    vision_txt = Text()
    vision_txt.append(f"  SECTOR: ", style=DIM)
    vision_txt.append(f"{resultado['vision_data']['sector']}\n", style=f"bold {BLUE}")
    vision_txt.append(f"  NIVEL:  ", style=DIM)
    vision_txt.append(f"{resultado['vision_data']['nivel']}\n",
                      style=f"bold {nivel_color(resultado['vision_data']['nivel'])}")
    vision_txt.append(f"  SCORE:  ", style=DIM)
    vision_txt.append(f"{resultado['vision_data']['score']:.1f}\n", style=WHITE)

    ml_max = resultado["ml_data"]["max_sector"]
    ml_prob = resultado["ml_data"]["max_prob"]
    ml_txt = Text()
    ml_txt.append(f"  SECTOR CRÍTICO: ", style=DIM)
    ml_txt.append(f"{ml_max}\n", style=f"bold {RED if ml_prob>50 else AMBER}")
    ml_txt.append(f"  PROBABILIDAD:   ", style=DIM)
    ml_txt.append(f"{ml_prob:.1f}%\n", style=f"bold {RED if ml_prob>50 else AMBER}")

    info_panel = Panel(
        Text("\n[VISIÓN IA]\n", style=f"bold {AMBER}").append_text(vision_txt)
        .append_text(Text("\n[PREDICCIÓN ML]\n", style=f"bold {AMBER}"))
        .append_text(ml_txt),
        title=f"[bold {AMBER}]▸ SUBSISTEMAS[/]",
        border_style=BLUE, padding=(0,1),
    )

    root.add_row(Columns([gauge_panel, info_panel], expand=True, equal=True))
    root.add_row(build_componentes_panel(resultado["componentes"], PESOS))
    root.add_row(Columns([
        build_historial_chart(historial),
        build_flota_mini(resultado["estados_flota"]),
    ], expand=True))
    root.add_row(build_acciones_panel(acciones_log))
    root.add_row(Panel(Align.center(footer), style=DIM, padding=(0,1)))
    return root


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    console.clear()
    console.print(Panel.fit(
        f"[bold {AMBER}]Inicializando CENTINELA THREAT SCORE...[/]\n"
        f"[{BLUE}]Componentes: Telemetría + Visión + ML + Alertas + Incidentes[/]\n"
        f"[{DIM}]Acciones automáticas: TS>40 / TS>60 / TS>80[/]",
        title="[bold white]EATON DYNAMICS — THREAT SCORE v1.0[/]",
        border_style=AMBER,
    ))
    time.sleep(1.2)

    engine   = ThreatScoreEngine()
    acciones = AccionesAutomaticas()
    tick     = 0
    stats    = {"acciones": 0, "max_ts": 0.0, "min_ts": 100.0}

    # Mensaje inicial
    enviar_telegram(
        f"📊 <b>CENTINELA THREAT SCORE ACTIVADO</b>\n\n"
        f"Índice unificado de amenaza iniciado.\n"
        f"🟢 Score inicial: calculando...\n"
        f"⚡ Acciones automáticas: TS>40, TS>60, TS>80\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}"
    )

    try:
        with Live(console=console, refresh_per_second=3) as live:
            while True:
                tick += 1
                resultado = engine.calcular()
                ts        = resultado["ts"]

                stats["max_ts"] = max(stats["max_ts"], ts)
                stats["min_ts"] = min(stats["min_ts"], ts)

                prev_acc = len(acciones.log)
                acciones.evaluar(resultado)
                stats["acciones"] += len(acciones.log) - prev_acc

                live.update(build_dashboard(
                    resultado, engine.historial,
                    acciones.log, tick, stats))

                time.sleep(0.8)

    except KeyboardInterrupt:
        console.print(f"\n[bold {AMBER}]◈ THREAT SCORE DETENIDO.[/]")
        console.print(f"  TS máximo registrado: {stats['max_ts']:.1f}")
        console.print(f"  TS mínimo registrado: {stats['min_ts']:.1f}")
        console.print(f"  Acciones automáticas: {stats['acciones']}")
        enviar_telegram(
            f"🔴 <b>THREAT SCORE DETENIDO</b>\n\n"
            f"📊 TS Máximo: {stats['max_ts']:.1f}\n"
            f"📊 TS Mínimo: {stats['min_ts']:.1f}\n"
            f"⚡ Acciones auto: {stats['acciones']}"
        )


if __name__ == "__main__":
    main()
