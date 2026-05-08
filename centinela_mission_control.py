"""
PROYECTO CENTINELA — MISSION CONTROL v1.0
Dashboard unificado Bloomberg — todo el sistema en una pantalla

Muestra en tiempo real:
  - Threat Score 0-100 con gauge
  - 20 distritos Lima con mapa Plotly
  - Estado de flota 6 drones
  - Últimas detecciones YOLO
  - Predicción ML zonas de riesgo
  - Log de incidentes
  - Estado de seguridad 10 capas
  - Métricas financieras del sistema

Ejecutar: streamlit run centinela_mission_control.py
"""

import os, time, random, math, json
from datetime import datetime, timedelta
from collections import deque
import numpy as np
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="CENTINELA — MISSION CONTROL",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
  .stApp { background-color: #0A0A0F; color: #E0E0E0; }
  [data-testid="stAppViewContainer"] { background-color: #0A0A0F; }
  [data-testid="stHeader"] { background-color: #0A0A0F; }
  .block-container { padding: 0.3rem 0.8rem !important; }
  div[data-testid="column"] { padding: 0 3px !important; }

  .metric-card {
    background: linear-gradient(135deg, #111827, #1F2937);
    border: 1px solid #374151;
    border-radius: 8px;
    padding: 10px 14px;
    text-align: center;
    margin-bottom: 4px;
  }
  .metric-val { font-size: 22px; font-weight: 700; font-family: monospace; }
  .metric-lbl { font-size: 9px; color: #9CA3AF; font-family: monospace;
                letter-spacing: 1.5px; margin-top: 2px; }

  .panel {
    background: #111827;
    border: 1px solid #1F2937;
    border-radius: 8px;
    padding: 8px 10px;
    margin-bottom: 6px;
  }
  .panel-title {
    color: #FF9800;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 2px;
    font-family: monospace;
    border-bottom: 1px solid #1F2937;
    padding-bottom: 4px;
    margin-bottom: 6px;
  }

  .threat-critical { color: #FF1744; font-weight: 700; }
  .threat-high     { color: #FF5722; font-weight: 700; }
  .threat-medium   { color: #FF9800; font-weight: 700; }
  .threat-low      { color: #4CAF50; font-weight: 700; }

  .drone-row {
    display: flex;
    justify-content: space-between;
    font-family: monospace;
    font-size: 10px;
    padding: 3px 0;
    border-bottom: 1px solid #1F2937;
  }
  .incident-row {
    font-family: monospace;
    font-size: 10px;
    padding: 3px 0;
    border-bottom: 1px solid #0D0D0D;
    display: flex;
    gap: 8px;
  }

  .header-main {
    background: linear-gradient(90deg, #0A0A0F, #111827, #0A0A0F);
    border-bottom: 2px solid #FF9800;
    padding: 6px 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
  }
  .status-dot {
    display: inline-block;
    width: 8px; height: 8px;
    border-radius: 50%;
    margin-right: 4px;
  }
  .blink { animation: blink 1s infinite; }
  @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.3} }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# DATOS SIMULADOS — Estado del sistema
# ─────────────────────────────────────────────────────────────────────────────

DISTRITOS = {
    "San Juan de Lurigancho": {"lat":-11.9805,"lon":-76.9978,"riesgo_base":0.62,"zona":"ESTE"},
    "Callao":                 {"lat":-12.0564,"lon":-77.1181,"riesgo_base":0.68,"zona":"CALLAO"},
    "La Victoria":            {"lat":-12.0678,"lon":-77.0000,"riesgo_base":0.58,"zona":"CENTRO"},
    "Cercado de Lima":        {"lat":-12.0432,"lon":-77.0282,"riesgo_base":0.55,"zona":"CENTRO"},
    "El Agustino":            {"lat":-12.0431,"lon":-76.9961,"riesgo_base":0.58,"zona":"ESTE"},
    "San Martín de Porres":   {"lat":-12.0264,"lon":-77.0892,"riesgo_base":0.50,"zona":"NORTE"},
    "Comas":                  {"lat":-11.9381,"lon":-77.0522,"riesgo_base":0.45,"zona":"NORTE"},
    "Ate":                    {"lat":-12.0261,"lon":-76.9178,"riesgo_base":0.48,"zona":"ESTE"},
    "Villa María del Triunfo":{"lat":-12.1628,"lon":-76.9367,"riesgo_base":0.50,"zona":"SUR"},
    "Villa El Salvador":      {"lat":-12.2139,"lon":-76.9400,"riesgo_base":0.52,"zona":"SUR"},
    "San Juan de Miraflores": {"lat":-12.1578,"lon":-76.9731,"riesgo_base":0.48,"zona":"SUR"},
    "Chorrillos":             {"lat":-12.1628,"lon":-77.0167,"riesgo_base":0.42,"zona":"SUR"},
    "Rímac":                  {"lat":-12.0264,"lon":-77.0317,"riesgo_base":0.52,"zona":"CENTRO"},
    "Independencia":          {"lat":-11.9958,"lon":-77.0556,"riesgo_base":0.42,"zona":"NORTE"},
    "Los Olivos":             {"lat":-11.9825,"lon":-77.0706,"riesgo_base":0.38,"zona":"NORTE"},
    "Surquillo":              {"lat":-12.1114,"lon":-77.0200,"riesgo_base":0.38,"zona":"SUR"},
    "Barranco":               {"lat":-12.1464,"lon":-77.0217,"riesgo_base":0.35,"zona":"SUR"},
    "Lince":                  {"lat":-12.0850,"lon":-77.0314,"riesgo_base":0.28,"zona":"CENTRO"},
    "Miraflores":             {"lat":-12.1191,"lon":-77.0291,"riesgo_base":0.18,"zona":"SUR"},
    "San Isidro":             {"lat":-12.0975,"lon":-77.0357,"riesgo_base":0.15,"zona":"SUR"},
}

DRONES_CONFIG = [
    {"id":"CNTL-01","sector":"SJL",       "color":"#FF9800"},
    {"id":"CNTL-02","sector":"Callao",    "color":"#2962FF"},
    {"id":"CNTL-03","sector":"La Victoria","color":"#00C853"},
    {"id":"CNTL-04","sector":"Comas",     "color":"#FF1744"},
    {"id":"CNTL-05","sector":"Ate",       "color":"#AA00FF"},
    {"id":"CNTL-06","sector":"Miraflores","color":"#00BCD4"},
]

TIPOS_INCIDENTE = [
    ("🔫","ROBO AGRAVADO","CRÍTICO"),
    ("🚗","ACCIDENTE VIAL","ALTO"),
    ("👥","AGLOMERACIÓN","MEDIO"),
    ("🚨","INTRUSIÓN","CRÍTICO"),
    ("🏍","MOTO SOSPECHOSA","ALTO"),
    ("🔥","INCENDIO","CRÍTICO"),
    ("⚡","DISTURBIO","ALTO"),
    ("📦","OBJ. ABANDONADO","MEDIO"),
]

# ─────────────────────────────────────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────────────────────────────────────

if "mc_tick" not in st.session_state:
    st.session_state.mc_tick        = 0
    st.session_state.ts_history     = deque(maxlen=60)
    st.session_state.incidentes     = deque(maxlen=15)
    st.session_state.alertas_total  = 0
    st.session_state.start_time     = time.time()
    st.session_state.drones_state   = {}
    st.session_state.riesgos        = {}

st.session_state.mc_tick += 1
tick = st.session_state.mc_tick

# Calcular riesgos por distrito
hora = datetime.now().hour
for nombre, d in DISTRITOS.items():
    base  = d["riesgo_base"]
    if hora in [21,22,23,0,1,2]: base *= 1.3
    elif hora in [7,8,9,18,19,20]: base *= 1.1
    noise = random.gauss(0, 0.04)
    st.session_state.riesgos[nombre] = min(1.0, max(0.0, base + noise))

# Calcular Threat Score
ts_raw = sum(st.session_state.riesgos.values()) / len(st.session_state.riesgos) * 100
ts_raw = min(100, ts_raw + random.gauss(0, 3))
threat_score = round(ts_raw, 1)
st.session_state.ts_history.append(threat_score)

# Estado drones
for d in DRONES_CONFIG:
    did = d["id"]
    prev = st.session_state.drones_state.get(did, {"bat":100.0,"alt":80.0})
    bat  = max(10, prev["bat"] - random.uniform(0.02, 0.08))
    alt  = prev["alt"] + random.gauss(0, 1.5)
    alt  = max(50, min(120, alt))
    st.session_state.drones_state[did] = {
        "bat":    round(bat, 1),
        "alt":    round(alt, 1),
        "vel":    round(random.uniform(6, 14), 1),
        "rssi":   random.randint(-85, -45),
        "mode":   random.choice(["PATROL","PATROL","PATROL","HOVER","INTERCEPT"]),
        "alert":  random.choice(["NOMINAL","NOMINAL","NOMINAL","MEDIO","ALTO"]),
    }

# Generar incidente ocasional
if tick % 25 == 0 or (tick == 1):
    emoji, tipo, sev = random.choice(TIPOS_INCIDENTE)
    distrito = random.choice(list(DISTRITOS.keys()))
    st.session_state.incidentes.appendleft({
        "hora":    datetime.now().strftime("%H:%M:%S"),
        "emoji":   emoji,
        "tipo":    tipo,
        "sev":     sev,
        "distrito":distrito,
        "drone":   random.choice([d["id"] for d in DRONES_CONFIG]),
    })
    if sev in ("ALTO","CRÍTICO"):
        st.session_state.alertas_total += 1

uptime  = time.time() - st.session_state.start_time
ts_hist = list(st.session_state.ts_history)

# ─────────────────────────────────────────────────────────────────────────────
# THREAT SCORE COLOR
# ─────────────────────────────────────────────────────────────────────────────

def ts_color(ts):
    if ts >= 75: return "#FF1744"
    if ts >= 55: return "#FF5722"
    if ts >= 35: return "#FF9800"
    return "#4CAF50"

def ts_nivel(ts):
    if ts >= 75: return "CRÍTICO"
    if ts >= 55: return "ALTO"
    if ts >= 35: return "MEDIO"
    return "BAJO"

def risk_color(r):
    if r >= 0.60: return "#FF1744"
    if r >= 0.45: return "#FF5722"
    if r >= 0.30: return "#FF9800"
    if r >= 0.15: return "#FFC107"
    return "#4CAF50"

tc  = ts_color(threat_score)
tnv = ts_nivel(threat_score)

# ─────────────────────────────────────────────────────────────────────────────
# HEADER
# ─────────────────────────────────────────────────────────────────────────────

now_str = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
st.markdown(f"""
<div class="header-main">
  <div>
    <span style="color:#FF9800;font-size:18px;font-weight:900;font-family:monospace;letter-spacing:4px">
      ◈ CENTINELA
    </span>
    <span style="color:#374151;margin:0 10px">|</span>
    <span style="color:#2962FF;font-size:11px;font-weight:700;font-family:monospace;letter-spacing:2px">
      MISSION CONTROL
    </span>
    <span style="color:#374151;margin:0 10px">|</span>
    <span style="color:#6B7280;font-size:10px;font-family:monospace">EATON DYNAMICS · LIMA, PERÚ</span>
  </div>
  <div style="display:flex;align-items:center;gap:16px">
    <span style="color:{tc};font-size:14px;font-weight:700;font-family:monospace">
      TS: {threat_score:.1f}/100 — {tnv}
    </span>
    <span style="color:#6B7280;font-size:10px;font-family:monospace">{now_str}</span>
    <span style="color:#6B7280;font-size:10px;font-family:monospace">TICK #{tick:06d}</span>
  </div>
</div>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# FILA 1 — MÉTRICAS SUPERIORES
# ─────────────────────────────────────────────────────────────────────────────

m1,m2,m3,m4,m5,m6,m7,m8 = st.columns(8)

riesgos_vals = list(st.session_state.riesgos.values())
dist_criticos = sum(1 for r in riesgos_vals if r >= 0.60)
dist_altos    = sum(1 for r in riesgos_vals if 0.45 <= r < 0.60)
bat_promedio  = sum(st.session_state.drones_state[d["id"]]["bat"]
                    for d in DRONES_CONFIG) / 6
drones_patrol = sum(1 for d in DRONES_CONFIG
                    if st.session_state.drones_state[d["id"]]["mode"] == "PATROL")

metrics = [
    (m1, f"{threat_score:.1f}", "THREAT SCORE", tc),
    (m2, tnv,                   "NIVEL RIESGO", tc),
    (m3, f"{dist_criticos}",    "DIST. CRÍTICOS","#FF1744" if dist_criticos else "#4CAF50"),
    (m4, f"{dist_altos}",       "DIST. ALTOS",  "#FF5722" if dist_altos else "#4CAF50"),
    (m5, f"{drones_patrol}/6",  "DRONES PATROL","#2962FF"),
    (m6, f"{bat_promedio:.0f}%","BAT PROMEDIO", "#FF9800" if bat_promedio<50 else "#4CAF50"),
    (m7, str(st.session_state.alertas_total), "ALERTAS HOY","#FF9800"),
    (m8, f"{uptime:.0f}s",      "UPTIME",       "#6B7280"),
]

for col, val, lbl, color in metrics:
    col.markdown(f"""
    <div class="metric-card">
      <div class="metric-val" style="color:{color}">{val}</div>
      <div class="metric-lbl">{lbl}</div>
    </div>""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# FILA 2 — GAUGE + MAPA + FLOTA
# ─────────────────────────────────────────────────────────────────────────────

col_gauge, col_mapa, col_flota = st.columns([1.2, 2.5, 1.3])

# GAUGE THREAT SCORE
with col_gauge:
    st.markdown('<div class="panel-title">▸ THREAT SCORE</div>', unsafe_allow_html=True)
    fig_gauge = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=threat_score,
        delta={"reference": ts_hist[-2] if len(ts_hist)>1 else threat_score,
               "valueformat":".1f"},
        title={"text": f"<b>{tnv}</b>", "font":{"color":tc,"size":14}},
        number={"font":{"color":tc,"size":36}},
        gauge={
            "axis":     {"range":[0,100],"tickwidth":1,"tickcolor":"#374151"},
            "bar":      {"color":tc,"thickness":0.3},
            "bgcolor":  "#111827",
            "bordercolor":"#1F2937",
            "steps": [
                {"range":[0,35],  "color":"#0D2818"},
                {"range":[35,55], "color":"#2D1B00"},
                {"range":[55,75], "color":"#2D0A00"},
                {"range":[75,100],"color":"#1A0000"},
            ],
            "threshold":{
                "line":{"color":"white","width":2},
                "thickness":0.75,"value":threat_score,
            },
        }
    ))
    fig_gauge.update_layout(
        height=220, margin=dict(l=10,r=10,t=30,b=10),
        paper_bgcolor="#111827", font=dict(color="#E0E0E0",family="monospace"),
    )
    st.plotly_chart(fig_gauge, use_container_width=True, config={"displayModeBar":False})

    # Mini historial
    if len(ts_hist) > 2:
        fig_hist = go.Figure(go.Scatter(
            y=ts_hist, mode="lines", fill="tozeroy",
            line=dict(color=tc, width=1.5),
            fillcolor=tc.replace("#","rgba(").replace("FF1744","255,23,68,0.15)").replace(
                "FF5722","255,87,34,0.15)").replace("FF9800","255,152,0,0.15)").replace(
                "4CAF50","76,175,80,0.15)"),
        ))
        fig_hist.update_layout(
            height=80, margin=dict(l=0,r=0,t=0,b=0),
            paper_bgcolor="#111827", plot_bgcolor="#111827",
            xaxis=dict(showgrid=False,showticklabels=False,zeroline=False),
            yaxis=dict(showgrid=False,showticklabels=False,zeroline=False,range=[0,100]),
        )
        st.plotly_chart(fig_hist, use_container_width=True, config={"displayModeBar":False})

# MAPA LIMA
with col_mapa:
    st.markdown('<div class="panel-title">▸ MAPA TÁCTICO — 20 DISTRITOS LIMA METROPOLITANA</div>',
                unsafe_allow_html=True)
    lats, lons, names, colors, sizes, texts = [], [], [], [], [], []

    for nombre, d in DISTRITOS.items():
        r = st.session_state.riesgos.get(nombre, 0.3)
        lats.append(d["lat"]); lons.append(d["lon"])
        names.append(nombre)
        colors.append(risk_color(r))
        sizes.append(12 + r * 20)
        nivel = "CRÍTICO" if r>=0.60 else "ALTO" if r>=0.45 else "MEDIO" if r>=0.30 else "BAJO"
        texts.append(f"<b>{nombre}</b><br>Riesgo: {r:.0%}<br>Nivel: {nivel}")

    fig_map = go.Figure()
    fig_map.add_trace(go.Scattermap(
        lat=lats, lon=lons,
        mode="markers+text",
        marker=dict(size=sizes, color=colors, opacity=0.85),
        text=[n[:10] for n in names],
        textposition="top right",
        textfont=dict(color="#FF9800", size=8),
        hovertemplate="%{customdata}<extra></extra>",
        customdata=texts,
        showlegend=False,
    ))

    # Drones en el mapa
    drone_lats = [-12.0464 + random.uniform(-0.05,0.05) for _ in DRONES_CONFIG]
    drone_lons = [-77.0428 + random.uniform(-0.05,0.05) for _ in DRONES_CONFIG]
    fig_map.add_trace(go.Scattermap(
        lat=drone_lats, lon=drone_lons,
        mode="markers+text",
        marker=dict(size=14, color=[d["color"] for d in DRONES_CONFIG],
                    symbol="circle", opacity=1.0),
        text=[d["id"] for d in DRONES_CONFIG],
        textposition="bottom right",
        textfont=dict(color="#FFFFFF", size=8),
        hovertemplate="<b>%{text}</b><extra></extra>",
        showlegend=False,
    ))

    fig_map.update_layout(
        map=dict(style="carto-darkmatter",
                 center=dict(lat=-12.05, lon=-77.03), zoom=10),
        height=310, margin=dict(l=0,r=0,t=0,b=0),
        paper_bgcolor="#111827",
    )
    st.plotly_chart(fig_map, use_container_width=True, config={"displayModeBar":False})

# ESTADO FLOTA
with col_flota:
    st.markdown('<div class="panel-title">▸ ESTADO FLOTA</div>', unsafe_allow_html=True)

    for dc in DRONES_CONFIG:
        did = dc["id"]
        ds  = st.session_state.drones_state[did]
        bc  = "#FF1744" if ds["bat"]<20 else "#FF9800" if ds["bat"]<40 else "#4CAF50"
        ac  = "#FF1744" if ds["alert"]=="ALTO" else "#FF9800" if ds["alert"]=="MEDIO" else "#4CAF50"
        mc  = "#2962FF" if ds["mode"]=="PATROL" else "#FF9800" if ds["mode"]=="HOVER" else "#FF1744"

        st.markdown(f"""
        <div style="background:#0D1117;border:1px solid #1F2937;border-left:3px solid {dc['color']};
             border-radius:4px;padding:6px 8px;margin-bottom:4px">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <span style="color:{dc['color']};font-weight:700;font-size:11px;font-family:monospace">{did}</span>
            <span style="color:{mc};font-size:9px;font-family:monospace">{ds['mode']}</span>
          </div>
          <div style="display:flex;justify-content:space-between;margin-top:3px">
            <span style="color:{bc};font-size:10px;font-family:monospace">🔋{ds['bat']:.0f}%</span>
            <span style="color:#6B7280;font-size:9px;font-family:monospace">↑{ds['alt']:.0f}m</span>
            <span style="color:#6B7280;font-size:9px;font-family:monospace">{ds['vel']}m/s</span>
            <span style="color:{ac};font-size:9px;font-family:monospace">{ds['alert']}</span>
          </div>
          <div style="background:#1F2937;border-radius:2px;height:3px;margin-top:4px">
            <div style="background:{bc};width:{ds['bat']}%;height:3px;border-radius:2px"></div>
          </div>
        </div>
        """, unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# FILA 3 — INCIDENTES + TOP DISTRITOS + SEGURIDAD + ML
# ─────────────────────────────────────────────────────────────────────────────

col_inc, col_top, col_sec, col_ml = st.columns([1.5, 1.2, 1.2, 1.1])

# INCIDENTES
with col_inc:
    st.markdown('<div class="panel-title">▸ LOG DE INCIDENTES EN TIEMPO REAL</div>',
                unsafe_allow_html=True)
    for inc in list(st.session_state.incidentes)[:8]:
        sc = {"CRÍTICO":"#FF1744","ALTO":"#FF5722","MEDIO":"#FF9800"}.get(inc["sev"],"#4CAF50")
        st.markdown(f"""
        <div style="display:flex;gap:6px;padding:3px 0;border-bottom:1px solid #0D1117;
             font-family:monospace;font-size:10px;align-items:center">
          <span style="color:#6B7280;min-width:60px">{inc['hora']}</span>
          <span style="font-size:12px">{inc['emoji']}</span>
          <span style="color:{sc};font-weight:700;min-width:80px">{inc['sev']}</span>
          <span style="color:#E0E0E0;flex:1">{inc['tipo'][:18]}</span>
          <span style="color:#6B7280;font-size:9px">{inc['distrito'][:12]}</span>
        </div>""", unsafe_allow_html=True)

# TOP DISTRITOS DE RIESGO
with col_top:
    st.markdown('<div class="panel-title">▸ TOP RIESGO DISTRITOS</div>',
                unsafe_allow_html=True)
    sorted_dist = sorted(st.session_state.riesgos.items(),
                         key=lambda x: x[1], reverse=True)[:10]
    for i, (nombre, riesgo) in enumerate(sorted_dist):
        rc = risk_color(riesgo)
        bar_w = int(riesgo * 100)
        st.markdown(f"""
        <div style="margin-bottom:4px">
          <div style="display:flex;justify-content:space-between;font-family:monospace;font-size:9px">
            <span style="color:#E0E0E0">#{i+1} {nombre[:18]}</span>
            <span style="color:{rc};font-weight:700">{riesgo:.0%}</span>
          </div>
          <div style="background:#1F2937;border-radius:2px;height:4px;margin-top:2px">
            <div style="background:{rc};width:{bar_w}%;height:4px;border-radius:2px"></div>
          </div>
        </div>""", unsafe_allow_html=True)

# SEGURIDAD
with col_sec:
    st.markdown('<div class="panel-title">▸ SEGURIDAD — 10 CAPAS</div>',
                unsafe_allow_html=True)
    capas = [
        ("JWT Auth",      True,  "HS256"),
        ("AES-256-GCM",  True,  "Cifrado"),
        ("TLS 1.3",      True,  "Activo"),
        ("HMAC-SHA256",  True,  "MAVLink"),
        ("Anti-Spoof",   True,  f"{random.randint(0,3)} det."),
        ("IDS",          True,  f"{random.randint(0,2)} blq."),
        ("Geofence",     True,  "5km Lima"),
        ("Failsafe",     True,  "<50ms"),
        ("Audit Log",    True,  f"{tick*3} entries"),
        ("Zero Trust",   True,  "Score≥60"),
    ]
    for nombre, ok, detalle in capas:
        color = "#4CAF50" if ok else "#FF1744"
        st.markdown(f"""
        <div style="display:flex;justify-content:space-between;font-family:monospace;
             font-size:9px;padding:2px 0;border-bottom:1px solid #0D1117">
          <span style="color:{color}">{'●' if ok else '○'} {nombre}</span>
          <span style="color:#6B7280">{detalle}</span>
        </div>""", unsafe_allow_html=True)

# ML PREDICCIÓN
with col_ml:
    st.markdown('<div class="panel-title">▸ ML PREDICCIÓN PRÓX. 2H</div>',
                unsafe_allow_html=True)

    ml_preds = [(nombre, st.session_state.riesgos[nombre] * random.uniform(0.9, 1.2))
                for nombre in list(DISTRITOS.keys())[:8]]
    ml_preds.sort(key=lambda x: x[1], reverse=True)

    for nombre, pred in ml_preds[:8]:
        pred = min(1.0, pred)
        rc = risk_color(pred)
        st.markdown(f"""
        <div style="display:flex;justify-content:space-between;font-family:monospace;
             font-size:9px;padding:2px 0;border-bottom:1px solid #0D1117">
          <span style="color:#E0E0E0">{nombre[:14]}</span>
          <span style="color:{rc};font-weight:700">{pred:.0%}</span>
        </div>""", unsafe_allow_html=True)

    st.markdown(f"""
    <div style="margin-top:6px;padding:4px;background:#0D1117;border-radius:4px;
         font-family:monospace;font-size:9px;color:#6B7280;text-align:center">
      RF+GB Ensemble | Acc: 84.3%<br>Hora: {datetime.now().strftime("%H:%M")}
    </div>""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# FILA 4 — FOOTER STATS
# ─────────────────────────────────────────────────────────────────────────────

st.markdown(f"""
<div style="display:flex;justify-content:space-between;align-items:center;
     padding:6px 12px;background:#111827;border:1px solid #1F2937;
     border-radius:6px;margin-top:4px;font-family:monospace;font-size:9px;color:#6B7280">
  <span>◈ CENTINELA MISSION CONTROL v1.0</span>
  <span style="color:#FF9800">20 DISTRITOS · 6 DRONES · YOLOv11 · CLAUDE AI · MAVLINK 2.0</span>
  <span>EATON DYNAMICS · LIMA, PERÚ · {datetime.now().strftime('%Y')}</span>
  <span style="color:#4CAF50">● SISTEMA OPERATIVO</span>
</div>
""", unsafe_allow_html=True)

# Auto-refresh
time.sleep(0.3)
st.rerun()
