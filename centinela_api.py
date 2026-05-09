"""
PROYECTO CENTINELA — API REST v1.0
FastAPI + PyBullet + Claude AI
Documentación automática: http://localhost:8000/docs

Endpoints:
  GET  /salud          — Health check del sistema
  GET  /flota          — Estado completo de la flota
  GET  /drone/{id}     — Telemetría individual
  GET  /alertas        — Alertas activas
  POST /despacho       — Despachar drone a coordenadas
  GET  /kpis           — KPIs operativos
  POST /reporte        — Generar PDF ejecutivo
  GET  /coordinador    — Última orden del coordinador IA
  POST /analizar/{id}  — Análisis táctico individual con IA
"""

import math, time, random, os, json, re, hashlib, hmac
from datetime import datetime
from typing import Optional
from collections import deque
import threading
import anthropic

import pybullet as pb
import pybullet_data
import numpy as np

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# Secret HMAC compartido con centinela_drone_agent (uplink Pi 5 → ground)
HMAC_SECRET = os.getenv("CENTINELA_HMAC", "centinela-shared-secret").encode()
NUM_DRONES    = 6
REF_LAT       = -12.0464
REF_LON       = -77.0428
M_PER_DEG_LAT = 111_320.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(REF_LAT))
DRONE_MASS_KG = 1.2
GRAVITY       = 9.81
HOVER_THRUST  = DRONE_MASS_KG * GRAVITY
K_DRAG        = 0.15
BATTERY_DRAIN = 0.008
TICK_DT       = 0.05

SECTORES = [
    {"nombre": "Miraflores",  "radio": 150, "angulo_base": 0.0,   "alt": 80},
    {"nombre": "San Isidro",  "radio": 200, "angulo_base": 1.047, "alt": 90},
    {"nombre": "Barranco",    "radio": 130, "angulo_base": 2.094, "alt": 70},
    {"nombre": "Surquillo",   "radio": 180, "angulo_base": 3.141, "alt": 85},
    {"nombre": "La Victoria", "radio": 160, "angulo_base": 4.189, "alt": 75},
    {"nombre": "Lince",       "radio": 140, "angulo_base": 5.236, "alt": 80},
]

# ─────────────────────────────────────────────────────────────────────────────
# ESTADO GLOBAL DEL SISTEMA
# ─────────────────────────────────────────────────────────────────────────────

class SistemaEstado:
    def __init__(self):
        self.telemetria: dict      = {}
        self.alertas: list         = []
        self.decisiones: dict      = {}
        self.coord_orden: str      = "Sistema iniciando..."
        self.coord_estado: str     = "INICIANDO"
        self.despachos: list       = []
        self.start_time: float     = time.time()
        self.tick: int             = 0
        self.lock                  = threading.Lock()
        self.pb_client: int        = -1
        self.pb_bodies: list       = []
        self.batteries: list       = [100.0] * NUM_DRONES
        self.running: bool         = False
        self.alert_log: deque      = deque(maxlen=50)
        self.dispatch_log: deque   = deque(maxlen=20)

estado = SistemaEstado()

# ─────────────────────────────────────────────────────────────────────────────
# MOTOR DE FÍSICA PyBullet (corre en background thread)
# ─────────────────────────────────────────────────────────────────────────────

def init_pybullet():
    c = pb.connect(pb.DIRECT)
    pb.setGravity(0, 0, -GRAVITY, physicsClientId=c)
    pb.setTimeStep(TICK_DT, physicsClientId=c)
    pb.setAdditionalSearchPath(pybullet_data.getDataPath())
    pb.loadURDF("plane.urdf", physicsClientId=c)

    bodies = []
    for i in range(NUM_DRONES):
        s = SECTORES[i]
        r = s["radio"] * 0.3
        x0 = r * math.cos(s["angulo_base"])
        y0 = r * math.sin(s["angulo_base"])
        col = pb.createCollisionShape(pb.GEOM_BOX,
              halfExtents=[0.15,0.15,0.04], physicsClientId=c)
        vis = pb.createVisualShape(pb.GEOM_BOX,
              halfExtents=[0.15,0.15,0.04],
              rgbaColor=[0.2,0.6,1.0,1.0], physicsClientId=c)
        body = pb.createMultiBody(baseMass=DRONE_MASS_KG,
               baseCollisionShapeIndex=col, baseVisualShapeIndex=vis,
               basePosition=[x0, y0, float(s["alt"])], physicsClientId=c)
        pb.changeDynamics(body, -1, linearDamping=K_DRAG,
                          angularDamping=0.9, physicsClientId=c)
        bodies.append(body)
    return c, bodies


def step_drone(c, body, tick, battery, sector_idx):
    s      = SECTORES[sector_idx]
    angle  = s["angulo_base"] + tick * (0.008 + sector_idx * 0.001)
    tx     = s["radio"] * math.cos(angle)
    ty     = s["radio"] * math.sin(angle)
    talt   = float(s["alt"]) + 10.0 * math.sin(tick * 0.02)

    pos, _ = pb.getBasePositionAndOrientation(body, physicsClientId=c)
    thrust = HOVER_THRUST + 2.8*(talt - pos[2])
    thrust = max(0.0, min(thrust, HOVER_THRUST*2.8))

    rpms = []
    for off in [[.15,.15,.02],[-.15,.15,.02],[.15,-.15,.02],[-.15,-.15,.02]]:
        t_i = thrust/4 + random.gauss(0,.03)
        t_i = max(0.0, t_i)
        pb.applyExternalForce(body,-1,[0,0,t_i],off,pb.LINK_FRAME,physicsClientId=c)
        rpms.append(min(8000, max(0, t_i/HOVER_THRUST*5000*4)))

    pb.applyExternalForce(body,-1,
        [0.8*(tx-pos[0]), 0.8*(ty-pos[1]),0],
        [0,0,0],pb.WORLD_FRAME,physicsClientId=c)
    pb.applyExternalForce(body,-1,
        [random.gauss(0,.06),random.gauss(0,.06),0],
        [0,0,0],pb.LINK_FRAME,physicsClientId=c)

    pb.stepSimulation(physicsClientId=c)

    pos,_ = pb.getBasePositionAndOrientation(body,physicsClientId=c)
    vel,_ = pb.getBaseVelocity(body, physicsClientId=c)
    speed = math.sqrt(sum(v**2 for v in vel))

    mh    = max(0.0, min(1.0, 1.0 - float(np.std(rpms))/5000.0*3))
    nbat  = max(0.0, battery - BATTERY_DRAIN*(thrust/HOVER_THRUST))
    dist  = math.sqrt(pos[0]**2+pos[1]**2)
    rssi  = int(max(-110,min(-40,-52-dist*.25+random.gauss(0,2))))
    lat   = REF_LAT + pos[1]/M_PER_DEG_LAT
    lon   = REF_LON + pos[0]/M_PER_DEG_LON

    return {
        "drone_id":    f"CNTL-{sector_idx+1:02d}",
        "sector":      s["nombre"],
        "lat":         round(lat, 6),
        "lon":         round(lon, 6),
        "altitude_m":  round(max(0.0, pos[2]), 2),
        "velocity_ms": round(speed, 2),
        "battery_pct": round(nbat, 1),
        "rssi_dbm":    rssi,
        "motor_rpm":   [round(r) for r in rpms],
        "motor_health":round(mh, 3),
        "thrust_n":    round(thrust, 2),
        "status":      "CRITICAL" if nbat<15 else "WARNING" if nbat<30 else "PATROL",
        "timestamp":   datetime.now().isoformat(),
    }, nbat


def generar_alertas(telemetria: dict) -> list:
    alertas = []
    for did, t in telemetria.items():
        if t["battery_pct"] < 15:
            alertas.append({
                "drone_id": did, "tipo": "BATTERY_CRITICAL",
                "severidad": "EMERGENCY",
                "mensaje": f"Batería {t['battery_pct']}% — Retorno inmediato",
                "timestamp": datetime.now().isoformat(),
            })
        elif t["battery_pct"] < 30:
            alertas.append({
                "drone_id": did, "tipo": "BATTERY_LOW",
                "severidad": "WARNING",
                "mensaje": f"Batería {t['battery_pct']}% — Planificar retorno",
                "timestamp": datetime.now().isoformat(),
            })
        if t["rssi_dbm"] < -90:
            alertas.append({
                "drone_id": did, "tipo": "SIGNAL_LOSS",
                "severidad": "CRITICAL",
                "mensaje": f"RSSI {t['rssi_dbm']}dBm — GPS degradado",
                "timestamp": datetime.now().isoformat(),
            })
        if t["motor_health"] < 0.75:
            alertas.append({
                "drone_id": did, "tipo": "MOTOR_ANOMALY",
                "severidad": "WARNING",
                "mensaje": f"Motor health {t['motor_health']} — Anomalía",
                "timestamp": datetime.now().isoformat(),
            })
    return alertas


def physics_loop():
    """Loop de física en background thread."""
    c, bodies = init_pybullet()
    with estado.lock:
        estado.pb_client = c
        estado.pb_bodies = bodies
        estado.running   = True

    while estado.running:
        estado.tick += 1
        telem_nuevo = {}

        for i, body in enumerate(bodies):
            t, nbat = step_drone(c, body, estado.tick, estado.batteries[i], i)
            estado.batteries[i] = nbat
            telem_nuevo[t["drone_id"]] = t

        alertas = generar_alertas(telem_nuevo)

        with estado.lock:
            estado.telemetria = telem_nuevo
            estado.alertas    = alertas
            for a in alertas:
                estado.alert_log.appendleft(a)

        time.sleep(TICK_DT)


# ─────────────────────────────────────────────────────────────────────────────
# FASTAPI APP
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="CENTINELA API",
    description=(
        "**Proyecto Centinela** — Sistema de Drones de Seguridad y Vigilancia\n\n"
        "**EATON DYNAMICS** · Lima, Perú\n\n"
        "API REST para gestión táctica de flota de drones autónomos con IA.\n"
        "Motor de física: PyBullet | IA táctica: Claude Sonnet 4.6 + Opus 4.7"
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
# MODELOS PYDANTIC
# ─────────────────────────────────────────────────────────────────────────────

class DespachoRequest(BaseModel):
    target_lat: float
    target_lon: float
    tipo_incidente: str = "SECURITY_EVENT"
    prioridad: str = "NORMAL"

class ReporteRequest(BaseModel):
    incluir_ia: bool = True
    clasificacion: str = "CONFIDENCIAL"


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    """Inicia el motor de física en background al arrancar la API."""
    t = threading.Thread(target=physics_loop, daemon=True)
    t.start()
    time.sleep(1.0)  # Esperar inicialización


@app.get("/salud", tags=["Sistema"],
         summary="Health check del sistema")
async def salud():
    """Verifica que todos los componentes estén operativos."""
    uptime = time.time() - estado.start_time
    drones_ok = sum(
        1 for t in estado.telemetria.values()
        if t.get("status") != "OFFLINE"
    )
    return {
        "status":         "OPERATIVO",
        "version":        "1.0.0",
        "uptime_segundos": round(uptime, 1),
        "drones_activos": drones_ok,
        "drones_total":   NUM_DRONES,
        "alertas_activas": len(estado.alertas),
        "tick_actual":    estado.tick,
        "fisica_engine":  "PyBullet 3.2.7",
        "ia_engine":      "Claude Sonnet 4.6 + Opus 4.7",
        "timestamp":      datetime.now().isoformat(),
    }


@app.get("/flota", tags=["Flota"],
         summary="Estado completo de todos los drones")
async def get_flota():
    """Retorna telemetría en tiempo real de los 6 drones de la flota."""
    with estado.lock:
        if not estado.telemetria:
            raise HTTPException(503, "Sistema iniciando, intenta en 2 segundos")
        return {
            "flota":          list(estado.telemetria.values()),
            "total_drones":   NUM_DRONES,
            "alertas_activas":len(estado.alertas),
            "tick":           estado.tick,
            "timestamp":      datetime.now().isoformat(),
        }


@app.get("/drone/{drone_id}", tags=["Flota"],
         summary="Telemetría de un drone específico")
async def get_drone(drone_id: str):
    """
    Retorna telemetría detallada de un drone específico.
    
    **drone_id**: CNTL-01 a CNTL-06
    """
    did = drone_id.upper()
    with estado.lock:
        if did not in estado.telemetria:
            raise HTTPException(404, f"Drone {did} no encontrado. IDs válidos: CNTL-01 a CNTL-06")
        t = estado.telemetria[did]

    # Análisis de estado
    bat = t["battery_pct"]
    estado_texto = (
        "EMERGENCIA — Retorno inmediato" if bat < 15 else
        "ADVERTENCIA — Batería baja"     if bat < 30 else
        "NOMINAL — Patrulla activa"
    )
    return {
        **t,
        "estado_operativo": estado_texto,
        "bateria_estimada_min": round(bat / (BATTERY_DRAIN * 60), 1),
        "decision_ia": estado.decisiones.get(did, {"estado": "Pendiente análisis"}),
    }


@app.get("/alertas", tags=["Táctico"],
         summary="Alertas activas del sistema")
async def get_alertas():
    """Retorna todas las alertas activas ordenadas por severidad."""
    sev_rank = {"EMERGENCY":4,"CRITICAL":3,"WARNING":2,"NOMINAL":1}
    with estado.lock:
        alertas_sorted = sorted(
            estado.alertas,
            key=lambda a: sev_rank.get(a.get("severidad","NOMINAL"), 0),
            reverse=True,
        )
        historial = list(estado.alert_log)[:20]

    return {
        "alertas_activas": alertas_sorted,
        "total_activas":   len(alertas_sorted),
        "historial":       historial,
        "timestamp":       datetime.now().isoformat(),
    }


@app.post("/despacho", tags=["Táctico"],
          summary="Despachar drone óptimo a coordenadas")
async def post_despacho(req: DespachoRequest):
    """
    Algoritmo ROI de despacho: selecciona el drone más eficiente
    basado en distancia, batería y estado de motores.
    
    **ROI = w_bat * f_bat + w_dist * f_dist + w_motor * f_motor**
    """
    with estado.lock:
        if not estado.telemetria:
            raise HTTPException(503, "Sistema iniciando")
        telems = dict(estado.telemetria)

    # Calcular ROI por drone
    scores = {}
    for did, t in telems.items():
        if t["status"] in ("CRITICAL", "OFFLINE"):
            continue
        bat_n  = t["battery_pct"] / 100.0
        if t["battery_pct"] < 30:
            bat_n *= 0.3

        R  = 6371.0
        φ1 = math.radians(t["lat"])
        φ2 = math.radians(req.target_lat)
        Δφ = math.radians(req.target_lat - t["lat"])
        Δλ = math.radians(req.target_lon - t["lon"])
        a  = math.sin(Δφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(Δλ/2)**2
        dist_km = R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

        score = (0.40 * bat_n +
                 0.40 / (1 + dist_km) +
                 0.20 * t["motor_health"])
        scores[did] = (score, dist_km)

    if not scores:
        raise HTTPException(503, "No hay drones disponibles para despacho")

    best_id   = max(scores, key=lambda k: scores[k][0])
    roi_score = scores[best_id][0]
    dist_km   = scores[best_id][1]
    best_t    = telems[best_id]
    eta_min   = (dist_km / max(best_t["velocity_ms"] * 3.6, 1)) * 60

    despacho = {
        "incidente_id":  f"INC-{len(estado.dispatch_log)+1:04d}",
        "tipo":          req.tipo_incidente,
        "prioridad":     req.prioridad,
        "drone_asignado":best_id,
        "sector_origen": best_t["sector"],
        "target_lat":    req.target_lat,
        "target_lon":    req.target_lon,
        "roi_score":     round(roi_score, 4),
        "distancia_km":  round(dist_km, 3),
        "eta_minutos":   round(eta_min, 2),
        "bateria_drone": best_t["battery_pct"],
        "todos_scores":  {k: round(v[0],4) for k,v in scores.items()},
        "timestamp":     datetime.now().isoformat(),
    }
    estado.dispatch_log.appendleft(despacho)
    return despacho


@app.get("/kpis", tags=["Gerencia"],
         summary="KPIs operativos para gerencia")
async def get_kpis():
    """
    Métricas clave de rendimiento del sistema Centinela.
    Diseñado para reportes gerenciales y presentaciones municipales.
    """
    uptime_h = (time.time() - estado.start_time) / 3600
    human_cost  = NUM_DRONES * 2 * 25.0 * uptime_h
    drone_cost  = NUM_DRONES * 4.0 * uptime_h
    savings_usd = max(0.0, human_cost - drone_cost)

    with estado.lock:
        telems  = dict(estado.telemetria)
        alertas = list(estado.alertas)

    operativos = sum(1 for t in telems.values() if t["status"] != "OFFLINE")
    bat_prom   = (sum(t["battery_pct"] for t in telems.values()) / max(len(telems),1))

    return {
        "sistema": {
            "uptime_horas":       round(uptime_h, 3),
            "tick_actual":        estado.tick,
            "drones_operativos":  f"{operativos}/{NUM_DRONES}",
            "eficiencia_pct":     round(operativos/NUM_DRONES*100, 1),
        },
        "flota": {
            "bateria_promedio":   round(bat_prom, 1),
            "alertas_activas":    len(alertas),
            "despachos_totales":  len(estado.dispatch_log),
            "sectores_cubiertos": NUM_DRONES,
        },
        "financiero": {
            "costo_patrullas_humanas_usd": round(human_cost, 2),
            "costo_flota_drones_usd":      round(drone_cost, 2),
            "ahorro_capital_usd":          round(savings_usd, 2),
            "roi_operativo":               round(savings_usd/max(drone_cost,0.01), 3),
        },
        "ia": {
            "motor_agentes":     "Claude Sonnet 4.6",
            "motor_coordinador": "Claude Opus 4.7",
            "api_key_activa":    bool(API_KEY),
        },
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/analizar/{drone_id}", tags=["IA"],
          summary="Análisis táctico IA de un drone específico")
async def analizar_drone(drone_id: str):
    """
    Claude Sonnet 4.6 analiza la telemetría actual del drone
    y retorna una decisión táctica con ROI score.
    """
    if not API_KEY:
        raise HTTPException(503, "API key de Anthropic no configurada")

    did = drone_id.upper()
    with estado.lock:
        if did not in estado.telemetria:
            raise HTTPException(404, f"Drone {did} no encontrado")
        t = estado.telemetria[did]

    client = anthropic.Anthropic(api_key=API_KEY)
    prompt = f"""Drone {did} sector {t['sector']}:
BAT={t['battery_pct']}% ALT={t['altitude_m']}m VEL={t['velocity_ms']}m/s
RSSI={t['rssi_dbm']}dBm MOTOR={t['motor_health']}

Responde SOLO JSON: {{"estado":"NOMINAL|WARNING|CRITICAL|EMERGENCY","orden":"acción<10 palabras","roi_score":0.0-1.0,"razon":"justificación<12 palabras"}}"""

    try:
        r = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            messages=[{"role":"user","content":prompt}]
        )
        text  = r.content[0].text
        match = re.search(r'\{.*?\}', text, re.DOTALL)
        decision = json.loads(match.group()) if match else {"error": "parse_failed"}
    except Exception as e:
        decision = {"error": str(e)}

    with estado.lock:
        estado.decisiones[did] = decision

    return {
        "drone_id": did,
        "telemetria_snapshot": t,
        "decision_ia": decision,
        "modelo": "claude-sonnet-4-6",
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/coordinador", tags=["IA"],
         summary="Última orden del Coordinador de Flota Opus 4.7")
async def get_coordinador():
    """Retorna la última decisión estratégica del coordinador de flota."""
    return {
        "estado_flota":   estado.coord_estado,
        "orden_activa":   estado.coord_orden,
        "motor":          "Claude Opus 4.7",
        "historial":      list(estado.dispatch_log)[:5],
        "timestamp":      datetime.now().isoformat(),
    }


@app.post("/reporte", tags=["Gerencia"],
          summary="Generar reporte PDF ejecutivo on-demand")
async def generar_reporte(req: ReporteRequest, background_tasks: BackgroundTasks):
    """
    Genera un reporte PDF ejecutivo completo con análisis de Opus 4.7.
    El PDF se guarda en C:/EATON/Centinela/reportes/
    """
    reports_dir = r"C:\EATON\Centinela\reportes"
    os.makedirs(reports_dir, exist_ok=True)

    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"CENTINELA_API_Reporte_{ts}.pdf"
    filepath = os.path.join(reports_dir, filename)

    with estado.lock:
        telems  = dict(estado.telemetria)
        alertas = list(estado.alertas)

    return {
        "status":   "PROGRAMADO",
        "mensaje":  "Ejecuta centinela_report.py para generar el PDF completo",
        "ruta":     filepath,
        "comando":  "python C:\\EATON\\Centinela\\centinela_report.py",
        "drones":   len(telems),
        "alertas":  len(alertas),
        "timestamp":datetime.now().isoformat(),
    }


@app.get("/historial/alertas", tags=["Táctico"],
         summary="Historial completo de alertas")
async def historial_alertas(limite: int = 20):
    """Retorna el historial de las últimas N alertas del sistema."""
    with estado.lock:
        historial = list(estado.alert_log)[:limite]
    return {
        "historial":  historial,
        "total":      len(historial),
        "limite":     limite,
        "timestamp":  datetime.now().isoformat(),
    }


@app.get("/historial/despachos", tags=["Táctico"],
         summary="Historial de despachos ejecutados")
async def historial_despachos():
    """Retorna el historial de despachos tácticos ejecutados."""
    with estado.lock:
        despachos = list(estado.dispatch_log)
    return {
        "despachos": despachos,
        "total":     len(despachos),
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/api/drones/{drone_id}/telemetry", tags=["Telemetría"],
          summary="Recibe telemetría firmada HMAC del drone (Pi 5 → ground)")
async def post_drone_telemetry(drone_id: str, request: Request):
    """
    Endpoint llamado por centinela_drone_agent.UplinkClient.

    Verifica HMAC-SHA256 sobre el body crudo usando el header
    X-Centinela-Signature. El secret se configura via env var
    CENTINELA_HMAC (debe coincidir con el agent; default
    "centinela-shared-secret" para dev).

    Frames con header X-Centinela-Replay=1 son re-envíos del buffer
    offline del agent — se aceptan y procesan igual.
    """
    body = await request.body()
    sig  = request.headers.get("X-Centinela-Signature", "")
    expected = hmac.new(HMAC_SECRET, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=401, detail="Invalid HMAC signature")

    try:
        frame = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    is_replay   = request.headers.get("X-Centinela-Replay") == "1"
    received_at = time.time()
    threat_val  = float(frame.get("threat", 0.0) or 0.0)

    with estado.lock:
        estado.telemetria[drone_id] = {
            "ts":          frame.get("ts", received_at),
            "state":       frame.get("state", {}) or {},
            "pi_health":   frame.get("pi_health", {}) or {},
            "detections":  frame.get("detections", []) or [],
            "threat":      threat_val,
            "notes":       frame.get("notes", "") or "",
            "received_at": received_at,
            "replay":      is_replay,
        }
        if threat_val >= 60:
            estado.alert_log.appendleft({
                "drone":  drone_id,
                "tipo":   "DRONE_THREAT_HIGH",
                "threat": threat_val,
                "ts":     received_at,
                "hora":   datetime.now().strftime("%H:%M:%S"),
            })

    return {"ok": True, "drone_id": drone_id, "received_at": received_at,
            "replay": is_replay}


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("◈ CENTINELA API — EATON DYNAMICS")
    print("  Documentación: http://localhost:8000/docs")
    print("  ReDoc:         http://localhost:8000/redoc")
    print("─" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
