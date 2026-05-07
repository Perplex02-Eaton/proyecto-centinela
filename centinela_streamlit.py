"""
PROYECTO CENTINELA — EATON DYNAMICS COMMAND CENTER
Dashboard Bloomberg con física real PyBullet
Stack: Streamlit + Plotly + PyBullet
Colores: Ámbar #FF9800 · Azul #2962FF · Fondo #0D0D0D
"""

import math, time, random
from collections import deque
from datetime import datetime
from dataclasses import dataclass
import threading

import numpy as np
import pybullet as pb
import pybullet_data
import streamlit as st
import plotly.graph_objects as go

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN DE PÁGINA
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="CENTINELA CMD",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
  /* Fondo Bloomberg negro */
  .stApp { background-color: #0D0D0D; color: #E0E0E0; }
  [data-testid="stAppViewContainer"] { background-color: #0D0D0D; }
  [data-testid="stHeader"] { background-color: #0D0D0D; }
  section[data-testid="stSidebar"] { background-color: #111111; }

  /* Métricas */
  [data-testid="stMetric"] {
    background: #111111;
    border: 1px solid #1E1E1E;
    border-radius: 6px;
    padding: 12px 16px;
  }
  [data-testid="stMetricLabel"] { color: #888888 !important; font-size: 12px !important; }
  [data-testid="stMetricValue"] { color: #FF9800 !important; font-size: 22px !important; font-weight: 600 !important; }
  [data-testid="stMetricDelta"] { font-size: 11px !important; }

  /* Eliminar padding excesivo */
  .block-container { padding: 1rem 1.5rem 0rem !important; }
  div[data-testid="column"] { padding: 0 4px !important; }

  /* Header personalizado */
  .centinela-header {
    background: linear-gradient(90deg, #0D0D0D 0%, #111827 50%, #0D0D0D 100%);
    border: 1px solid #FF9800;
    border-radius: 6px;
    padding: 10px 20px;
    margin-bottom: 12px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .centinela-title {
    color: #FF9800;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 3px;
    font-family: monospace;
  }
  .centinela-subtitle {
    color: #2962FF;
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 2px;
    font-family: monospace;
  }
  .centinela-time {
    color: #888;
    font-size: 12px;
    font-family: monospace;
  }

  /* Alert log */
  .alert-nominal  { color: #00E676; font-family: monospace; font-size: 12px; }
  .alert-warning  { color: #FF9800; font-family: monospace; font-size: 12px; }
  .alert-critical { color: #FF1744; font-family: monospace; font-size: 12px; }
  .alert-emergency{ color: #FF1744; font-family: monospace; font-size: 12px; font-weight: bold; }

  /* Panel oscuro */
  .dark-panel {
    background: #111111;
    border: 1px solid #1E1E1E;
    border-radius: 6px;
    padding: 12px;
    height: 100%;
  }
  .panel-title {
    color: #FF9800;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2px;
    font-family: monospace;
    margin-bottom: 8px;
    border-bottom: 1px solid #1E1E1E;
    padding-bottom: 6px;
  }

  /* Scrollbar oscuro */
  ::-webkit-scrollbar { width: 4px; }
  ::-webkit-scrollbar-track { background: #111; }
  ::-webkit-scrollbar-thumb { background: #333; border-radius: 2px; }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTES FÍSICAS
# ─────────────────────────────────────────────────────────────────────────────

REF_LAT           = -12.0464
REF_LON           = -77.0428
M_PER_DEG_LAT     = 111_320.0
M_PER_DEG_LON     = 111_320.0 * math.cos(math.radians(REF_LAT))
DRONE_MASS_KG     = 1.2
GRAVITY           = 9.81
HOVER_THRUST      = DRONE_MASS_KG * GRAVITY
K_DRAG            = 0.15
BATTERY_CAPACITY  = 100.0
BATTERY_DRAIN     = 0.012
TICK_DT           = 0.05
PHYSICS_PER_FRAME = 15
MAX_ALTITUDE      = 150.0


# ─────────────────────────────────────────────────────────────────────────────
# MOTOR DE FÍSICA PyBullet (en session_state para persistencia)
# ─────────────────────────────────────────────────────────────────────────────

def init_physics():
    """Inicializa PyBullet en modo DIRECT (sin GUI, sin crash RTX)."""
    client = pb.connect(pb.DIRECT)
    pb.setGravity(0, 0, -GRAVITY, physicsClientId=client)
    pb.setTimeStep(TICK_DT, physicsClientId=client)
    pb.setAdditionalSearchPath(pybullet_data.getDataPath())
    pb.loadURDF("plane.urdf", physicsClientId=client)

    col = pb.createCollisionShape(pb.GEOM_BOX,
          halfExtents=[0.15, 0.15, 0.04], physicsClientId=client)
    vis = pb.createVisualShape(pb.GEOM_BOX,
          halfExtents=[0.15, 0.15, 0.04],
          rgbaColor=[0.2, 0.6, 1.0, 1.0], physicsClientId=client)
    body = pb.createMultiBody(baseMass=DRONE_MASS_KG,
           baseCollisionShapeIndex=col, baseVisualShapeIndex=vis,
           basePosition=[0.0, 0.0, 80.0], physicsClientId=client)
    pb.changeDynamics(body, -1, linearDamping=K_DRAG,
                      angularDamping=0.9, physicsClientId=client)
    return client, body


def physics_step(client: int, body: int, mission_tick: int, battery: float):
    """
    Ejecuta un paso de física y retorna telemetría.
    Dinámica: hover + patrulla sinusoidal + viento gaussiano.
    """
    target_alt = 80.0 + 20.0 * math.sin(mission_tick * 0.018)
    pos, orn   = pb.getBasePositionAndOrientation(body, physicsClientId=client)
    vel_lin, _ = pb.getBaseVelocity(body, physicsClientId=client)

    alt_error  = target_alt - pos[2]
    thrust_cmd = HOVER_THRUST + 2.8 * alt_error
    thrust_cmd = max(0.0, min(thrust_cmd, HOVER_THRUST * 2.8))

    rotor_offsets = [[0.15,0.15,0.02],[-0.15,0.15,0.02],
                     [0.15,-0.15,0.02],[-0.15,-0.15,0.02]]
    rpms = []
    for off in rotor_offsets:
        t_i = thrust_cmd / 4.0 + random.gauss(0, 0.04)
        t_i = max(0.0, t_i)
        pb.applyExternalForce(body, -1, [0, 0, t_i],
                              off, pb.LINK_FRAME, physicsClientId=client)
        rpm = min(8000, max(0, t_i / HOVER_THRUST * 5000 * 4))
        rpms.append(rpm)

    # Viento
    pb.applyExternalForce(body, -1,
        [random.gauss(0, 0.08), random.gauss(0, 0.08), 0],
        [0,0,0], pb.LINK_FRAME, physicsClientId=client)

    # Patrulla circular
    pb.applyExternalForce(body, -1,
        [0.35 * math.cos(mission_tick * 0.013),
         0.35 * math.sin(mission_tick * 0.013), 0],
        [0,0,0], pb.WORLD_FRAME, physicsClientId=client)

    pb.stepSimulation(physicsClientId=client)

    pos, _ = pb.getBasePositionAndOrientation(body, physicsClientId=client)
    vel, _ = pb.getBaseVelocity(body, physicsClientId=client)
    speed  = math.sqrt(sum(v**2 for v in vel))
    alt    = max(0.0, pos[2])

    rpm_var      = float(np.std(rpms)) / 5000.0
    motor_health = max(0.0, min(1.0, 1.0 - rpm_var * 3))
    thrust_ratio = thrust_cmd / HOVER_THRUST
    new_battery  = max(0.0, battery - BATTERY_DRAIN * thrust_ratio)
    dist_m       = math.sqrt(pos[0]**2 + pos[1]**2)
    rssi         = int(max(-110, min(-40, -55 - dist_m * 0.3 + random.gauss(0,2))))
    lat          = REF_LAT + (pos[1] / M_PER_DEG_LAT)
    lon          = REF_LON + (pos[0] / M_PER_DEG_LON)

    return {
        "x_m": pos[0], "y_m": pos[1], "z_m": pos[2],
        "lat": lat, "lon": lon,
        "altitude_m": alt, "vel_ms": speed,
        "battery_pct": new_battery,
        "rssi_dbm": rssi,
        "motor_rpm": rpms, "motor_health": motor_health,
        "thrust_N": thrust_cmd,
    }, new_battery


# ─────────────────────────────────────────────────────────────────────────────
# INICIALIZACIÓN SESSION STATE
# ─────────────────────────────────────────────────────────────────────────────

if "physics_ready" not in st.session_state:
    client, body = init_physics()
    st.session_state.pb_client      = client
    st.session_state.pb_body        = body
    st.session_state.tick           = 0
    st.session_state.battery        = BATTERY_CAPACITY
    st.session_state.telem          = None
    st.session_state.alert_history  = deque(maxlen=30)
    st.session_state.lat_history    = deque(maxlen=120)
    st.session_state.lon_history    = deque(maxlen=120)
    st.session_state.alt_history    = deque(maxlen=120)
    st.session_state.bat_history    = deque(maxlen=120)
    st.session_state.vel_history    = deque(maxlen=120)
    st.session_state.start_time     = time.time()
    st.session_state.max_alt        = 0.0
    st.session_state.total_dist     = 0.0
    st.session_state.alert_count    = 0
    st.session_state.last_pos       = None
    st.session_state.physics_ready  = True


# ─────────────────────────────────────────────────────────────────────────────
# EJECUTAR FÍSICA
# ─────────────────────────────────────────────────────────────────────────────

for _ in range(PHYSICS_PER_FRAME):
    st.session_state.tick += 1
    telem, new_bat = physics_step(
        st.session_state.pb_client,
        st.session_state.pb_body,
        st.session_state.tick,
        st.session_state.battery,
    )
    st.session_state.battery = new_bat

st.session_state.telem = telem
t = telem

# Análisis táctico
bat = t["battery_pct"]
mh  = t["motor_health"]
rs  = t["rssi_dbm"]
if bat < 15.0:
    alert_label = "✗ EMERGENCY"
    alert_color = "alert-emergency"
    alert_desc  = f"Batería {bat:.1f}% — RETORNO INMEDIATO"
elif bat < 30.0:
    alert_label = "▲ WARNING"
    alert_color = "alert-warning"
    alert_desc  = f"Batería {bat:.1f}% — Planificar retorno"
elif rs < -90:
    alert_label = "■ CRITICAL"
    alert_color = "alert-critical"
    alert_desc  = f"RSSI {rs}dBm — Señal GPS degradada"
elif mh < 0.70:
    alert_label = "▲ WARNING"
    alert_color = "alert-warning"
    alert_desc  = f"Motor health {mh:.3f} — Anomalía detectada"
else:
    alert_label = "● NOMINAL"
    alert_color = "alert-nominal"
    alert_desc  = "Sistema nominal"

ts_str = datetime.now().strftime("%H:%M:%S")
st.session_state.alert_history.appendleft((ts_str, alert_label, alert_color, alert_desc))

# Históricos
st.session_state.lat_history.append(t["lat"])
st.session_state.lon_history.append(t["lon"])
st.session_state.alt_history.append(t["altitude_m"])
st.session_state.bat_history.append(t["battery_pct"])
st.session_state.vel_history.append(t["vel_ms"])

# KPIs
st.session_state.max_alt = max(st.session_state.max_alt, t["altitude_m"])
if st.session_state.last_pos:
    dx = t["x_m"] - st.session_state.last_pos[0]
    dy = t["y_m"] - st.session_state.last_pos[1]
    st.session_state.total_dist += math.sqrt(dx**2 + dy**2)
st.session_state.last_pos = (t["x_m"], t["y_m"])
uptime_h = (time.time() - st.session_state.start_time) / 3600
capital_savings = max(0.0, (2 * 25.0 - 4.0) * uptime_h)


# ─────────────────────────────────────────────────────────────────────────────
# RENDER DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

# ── HEADER ──
now_str  = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
tick_str = f"TICK #{st.session_state.tick:06d}"
st.markdown(f"""
<div class="centinela-header">
  <div>
    <span class="centinela-title">◈ PROYECTO CENTINELA</span>
    <span style="color:#444;margin:0 12px;">│</span>
    <span class="centinela-subtitle">EATON DYNAMICS CMD v2.0</span>
  </div>
  <div class="centinela-time">{now_str} &nbsp;│&nbsp; {tick_str} &nbsp;│&nbsp; PyBullet 3.2.7</div>
</div>
""", unsafe_allow_html=True)

# ── FILA 1: MÉTRICAS PRINCIPALES ──
m1, m2, m3, m4, m5 = st.columns(5)

bat_delta  = f"{-(BATTERY_DRAIN * t['thrust_N']/HOVER_THRUST * PHYSICS_PER_FRAME):.3f}%/frame"
alt_delta  = f"{t['z_m'] - 80.0:+.1f}m vs target"

m1.metric("🔋 BATERÍA",       f"{bat:.1f}%",          bat_delta)
m2.metric("✈ ALTITUD",        f"{t['altitude_m']:.1f}m", alt_delta)
m3.metric("⚡ VELOCIDAD",     f"{t['vel_ms']:.2f} m/s", None)
m4.metric("📡 RSSI",          f"{t['rssi_dbm']} dBm",   None)
m5.metric("⚙ MOTOR HEALTH",   f"{t['motor_health']:.3f}", None)

st.markdown("<div style='margin:8px 0'></div>", unsafe_allow_html=True)

# ── FILA 2: GAUGES + MAPA ──
gc1, gc2, gmap = st.columns([1, 1, 2])

PLOT_BG   = "#0D0D0D"
PLOT_PAP  = "#0D0D0D"
AMBER     = "#FF9800"
BLUE      = "#2962FF"
GREEN     = "#00E676"
RED       = "#FF1744"

# Gauge batería
bat_color = RED if bat < 20 else AMBER if bat < 35 else GREEN
fig_bat = go.Figure(go.Indicator(
    mode="gauge+number+delta",
    value=bat,
    delta={"reference": 100, "valueformat": ".1f",
           "font": {"color": "#888", "size": 11}},
    number={"suffix": "%", "font": {"color": bat_color, "size": 28}},
    title={"text": "BATERÍA", "font": {"color": AMBER, "size": 11}},
    gauge={
        "axis": {"range": [0, 100], "tickcolor": "#333",
                 "tickfont": {"color": "#666", "size": 9}},
        "bar": {"color": bat_color, "thickness": 0.25},
        "bgcolor": "#111",
        "bordercolor": "#1E1E1E",
        "steps": [
            {"range": [0,  20], "color": "#1a0000"},
            {"range": [20, 35], "color": "#1a0f00"},
            {"range": [35, 100],"color": "#001a00"},
        ],
        "threshold": {
            "line": {"color": RED, "width": 2},
            "thickness": 0.75, "value": 20,
        },
    },
))
fig_bat.update_layout(
    height=220, margin=dict(l=20, r=20, t=40, b=10),
    paper_bgcolor=PLOT_PAP, plot_bgcolor=PLOT_BG,
    font={"color": "#E0E0E0"},
)
gc1.plotly_chart(fig_bat, use_container_width=True, config={"displayModeBar": False})

# Gauge altitud
alt_color = RED if t["altitude_m"] < 10 else AMBER if t["altitude_m"] > 130 else BLUE
fig_alt = go.Figure(go.Indicator(
    mode="gauge+number",
    value=t["altitude_m"],
    number={"suffix": " m", "font": {"color": alt_color, "size": 28}},
    title={"text": "ALTITUD", "font": {"color": AMBER, "size": 11}},
    gauge={
        "axis": {"range": [0, MAX_ALTITUDE], "tickcolor": "#333",
                 "tickfont": {"color": "#666", "size": 9}},
        "bar": {"color": alt_color, "thickness": 0.25},
        "bgcolor": "#111",
        "bordercolor": "#1E1E1E",
        "steps": [
            {"range": [0,   10],  "color": "#1a0000"},
            {"range": [10,  130], "color": "#00001a"},
            {"range": [130, 150], "color": "#1a0f00"},
        ],
    },
))
fig_alt.update_layout(
    height=220, margin=dict(l=20, r=20, t=40, b=10),
    paper_bgcolor=PLOT_PAP, plot_bgcolor=PLOT_BG,
    font={"color": "#E0E0E0"},
)
gc2.plotly_chart(fig_alt, use_container_width=True, config={"displayModeBar": False})

# Mapa Lima
lats = list(st.session_state.lat_history)
lons = list(st.session_state.lon_history)

fig_map = go.Figure()
# Trayectoria
if len(lats) > 1:
    fig_map.add_trace(go.Scattermap(
        lat=lats, lon=lons,
        mode="lines",
        line={"color": BLUE, "width": 2},
        name="Trayectoria",
        showlegend=False,
    ))
# Posición actual
fig_map.add_trace(go.Scattermap(
    lat=[t["lat"]], lon=[t["lon"]],
    mode="markers+text",
    marker={"size": 14, "color": AMBER, "symbol": "circle"},
    text=["CNTL-SIM-01"],
    textposition="top right",
    textfont={"color": AMBER, "size": 11},
    name="Drone",
    showlegend=False,
))
# Centro del perímetro
fig_map.add_trace(go.Scattermap(
    lat=[REF_LAT], lon=[REF_LON],
    mode="markers",
    marker={"size": 8, "color": "#FF1744", "symbol": "cross"},
    name="Base",
    showlegend=False,
))
fig_map.update_layout(
    map={
        "style": "carto-darkmatter",
        "center": {"lat": REF_LAT, "lon": REF_LON},
        "zoom": 13,
    },
    height=220,
    margin=dict(l=0, r=0, t=0, b=0),
    paper_bgcolor=PLOT_PAP,
)
gmap.plotly_chart(fig_map, use_container_width=True, config={"displayModeBar": False})

st.markdown("<div style='margin:4px 0'></div>", unsafe_allow_html=True)

# ── FILA 3: CHART HISTÓRICO + LOG ALERTAS + KPIs ──
ch1, ch2, ch3 = st.columns([2, 1, 1])

# Chart histórico multivariable
ticks_x = list(range(len(st.session_state.alt_history)))
fig_hist = go.Figure()
fig_hist.add_trace(go.Scatter(
    x=ticks_x, y=list(st.session_state.alt_history),
    name="Altitud (m)", line={"color": BLUE, "width": 1.5},
    yaxis="y1",
))
fig_hist.add_trace(go.Scatter(
    x=ticks_x, y=list(st.session_state.bat_history),
    name="Batería (%)", line={"color": AMBER, "width": 1.5},
    yaxis="y2",
))
fig_hist.add_trace(go.Scatter(
    x=ticks_x, y=list(st.session_state.vel_history),
    name="Vel (m/s)", line={"color": GREEN, "width": 1, "dash": "dot"},
    yaxis="y1",
))
fig_hist.update_layout(
    height=200,
    paper_bgcolor=PLOT_PAP, plot_bgcolor="#111111",
    margin=dict(l=40, r=40, t=24, b=20),
    font={"color": "#888", "size": 10},
    title={"text": "▸ HISTÓRICO TELEMETRÍA",
           "font": {"color": AMBER, "size": 11}, "x": 0.01},
    legend={"font": {"size": 9}, "bgcolor": "rgba(0,0,0,0)",
            "orientation": "h", "y": -0.15},
    xaxis={"showgrid": True, "gridcolor": "#1E1E1E",
           "zeroline": False, "tickfont": {"size": 8}},
    yaxis={"showgrid": True, "gridcolor": "#1E1E1E",
           "zeroline": False, "tickfont": {"size": 8}, "title": "m / m/s"},
    yaxis2={"overlaying": "y", "side": "right",
            "showgrid": False, "tickfont": {"size": 8},
            "title": "%", "range": [0, 110]},
)
ch1.plotly_chart(fig_hist, use_container_width=True, config={"displayModeBar": False})

# Log de alertas
with ch2:
    st.markdown('<div class="panel-title">▸ LOG TÁCTICO — CNTL-SIM-01</div>',
                unsafe_allow_html=True)
    log_html = ""
    for ts, lbl, col, desc in list(st.session_state.alert_history)[:10]:
        log_html += f'<div class="{col}">{ts} {lbl}</div>'
        log_html += f'<div style="color:#555;font-size:10px;font-family:monospace;margin-bottom:4px;padding-left:8px">{desc[:40]}</div>'
    st.markdown(log_html, unsafe_allow_html=True)

# KPIs
with ch3:
    st.markdown('<div class="panel-title">▸ KPIs OPERATIVOS — GERENCIA</div>',
                unsafe_allow_html=True)
    uptime_s = time.time() - st.session_state.start_time

    def kpi_row(label, value, color="#E0E0E0"):
        return (f'<div style="display:flex;justify-content:space-between;'
                f'margin-bottom:6px;font-family:monospace;font-size:12px">'
                f'<span style="color:#666">{label}</span>'
                f'<span style="color:{color};font-weight:600">{value}</span></div>')

    kpis_html = ""
    kpis_html += kpi_row("Alt. máxima",
                         f"{st.session_state.max_alt:.1f}m", BLUE)
    kpis_html += kpi_row("Dist. recorrida",
                         f"{st.session_state.total_dist:.0f}m", GREEN)
    kpis_html += kpi_row("Bat. mínima",
                         f"{bat:.1f}%", AMBER)
    kpis_html += kpi_row("GPS actual",
                         f"{t['lat']:.5f}", "#888")
    kpis_html += kpi_row("",
                         f"{t['lon']:.5f}", "#888")
    kpis_html += kpi_row("Empuje",
                         f"{t['thrust_N']:.1f}N", AMBER)
    kpis_html += kpi_row("Uptime",
                         f"{uptime_s:.0f}s", "#888")
    kpis_html += kpi_row("Ahorro capital",
                         f"${capital_savings:.4f}", GREEN)
    kpis_html += kpi_row("Alertas totales",
                         str(st.session_state.alert_count), RED)

    st.markdown(kpis_html, unsafe_allow_html=True)

# ── FOOTER ──
st.markdown(f"""
<div style="text-align:center;padding:6px;border-top:1px solid #1E1E1E;
margin-top:8px;font-family:monospace;font-size:11px;color:#555">
  CENTINELA ACTIVE &nbsp;·&nbsp;
  <span style="color:{AMBER}">ESTADO: {alert_label}</span> &nbsp;·&nbsp;
  Lima, Perú &nbsp;·&nbsp;
  <span style="color:{BLUE}">PyBullet 3.2.7 + RTX 5050</span> &nbsp;·&nbsp;
  EATON DYNAMICS CMD v2.0
</div>
""", unsafe_allow_html=True)

# ── AUTO-REFRESH ──
time.sleep(0.4)
st.rerun()
