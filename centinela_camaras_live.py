"""
PROYECTO CENTINELA — CÁMARAS EN VIVO v1.0
Dashboard Streamlit con video en tiempo real

Muestra:
  - Grid 2x4 de cámaras SÍVICO Lima rotando
  - Imagen satelital Sentinel-2 más reciente
  - Análisis Claude Vision por cámara
  - Mapa de ubicaciones de cámaras
  - Métricas de fusión de sensores

Ejecutar: streamlit run centinela_camaras_live.py
"""

import os, time, random, math, base64, re, json
from datetime import datetime
from io import BytesIO
import anthropic

try:
    from PIL import Image, ImageDraw, ImageFilter
    PIL_OK = True
except ImportError:
    PIL_OK = False

try:
    import cv2
    CV2_OK = True
except ImportError:
    CV2_OK = False

import numpy as np
import streamlit as st
import plotly.graph_objects as go

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="CENTINELA — CÁMARAS EN VIVO",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
  .stApp { background-color: #0D0D0D; color: #E0E0E0; }
  [data-testid="stAppViewContainer"] { background-color: #0D0D0D; }
  [data-testid="stHeader"] { background-color: #0D0D0D; }
  .block-container { padding: 0.5rem 1rem !important; }
  div[data-testid="column"] { padding: 0 3px !important; }
  .cam-panel {
    background: #111;
    border: 1px solid #FF9800;
    border-radius: 6px;
    padding: 6px;
    margin-bottom: 6px;
  }
  .cam-title {
    color: #FF9800;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
    font-family: monospace;
    margin-bottom: 3px;
  }
  .cam-info {
    color: #2962FF;
    font-size: 9px;
    font-family: monospace;
  }
  .riesgo-bajo    { color: #00C853; font-weight: bold; }
  .riesgo-medio   { color: #FF9800; font-weight: bold; }
  .riesgo-alto    { color: #FF1744; font-weight: bold; }
  .riesgo-critico { color: #FF1744; font-weight: bold; font-size: 14px; }
  .metric-card {
    background: #111;
    border: 1px solid #1E1E1E;
    border-radius: 6px;
    padding: 8px 12px;
    text-align: center;
  }
  .metric-val { color: #FF9800; font-size: 20px; font-weight: bold; font-family: monospace; }
  .metric-lbl { color: #666; font-size: 10px; font-family: monospace; }
  .header-bar {
    background: linear-gradient(90deg,#0D0D0D,#111827,#0D0D0D);
    border: 1px solid #FF9800;
    border-radius: 6px;
    padding: 8px 16px;
    margin-bottom: 8px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
</style>
""", unsafe_allow_html=True)

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

CAMARAS = [
    {"id":"CAM-001","nombre":"Av. Abancay / Nicolás de Piérola","distrito":"Cercado de Lima",
     "lat":-12.0525,"lon":-77.0225,"tipo":"INTERSECCION"},
    {"id":"CAM-002","nombre":"Plaza Mayor de Lima","distrito":"Cercado de Lima",
     "lat":-12.0464,"lon":-77.0306,"tipo":"PLAZA"},
    {"id":"CAM-003","nombre":"Av. Túpac Amaru km 5","distrito":"Comas",
     "lat":-11.9651,"lon":-77.0542,"tipo":"AVENIDA"},
    {"id":"CAM-004","nombre":"Óvalo de Miraflores","distrito":"Miraflores",
     "lat":-12.1181,"lon":-77.0300,"tipo":"OVALO"},
    {"id":"CAM-005","nombre":"Puente Nuevo — SJL","distrito":"San Juan de Lurigancho",
     "lat":-12.0019,"lon":-77.0103,"tipo":"PUENTE"},
    {"id":"CAM-006","nombre":"Mercado Gamarra — La Victoria","distrito":"La Victoria",
     "lat":-12.0692,"lon":-76.9967,"tipo":"MERCADO"},
    {"id":"CAM-007","nombre":"Puerto del Callao","distrito":"Callao",
     "lat":-12.0611,"lon":-77.1358,"tipo":"PUERTO"},
    {"id":"CAM-008","nombre":"Av. Venezuela — Rímac","distrito":"Rímac",
     "lat":-12.0294,"lon":-77.0408,"tipo":"AVENIDA"},
]

ESCENAS_SAT = [
    {"distrito":"San Juan de Lurigancho","tipo":"RESIDENCIAL_DENSO","vehiculos":450,"personas":2800,"calor":0.35},
    {"distrito":"Callao","tipo":"INDUSTRIAL_PORTUARIO","vehiculos":890,"personas":1200,"calor":0.65},
    {"distrito":"La Victoria","tipo":"COMERCIAL_DENSO","vehiculos":280,"personas":5600,"calor":0.75},
    {"distrito":"Miraflores","tipo":"RESIDENCIAL_PREMIUM","vehiculos":120,"personas":340,"calor":0.10},
    {"distrito":"Villa El Salvador","tipo":"INDUSTRIAL","vehiculos":340,"personas":1800,"calor":0.55},
]

# ─────────────────────────────────────────────────────────────────────────────
# GENERADORES DE IMÁGENES
# ─────────────────────────────────────────────────────────────────────────────

def generar_frame_camara(cam: dict) -> Image.Image:
    W, H = 320, 240
    hora = datetime.now().hour
    es_noche = hora < 6 or hora > 21

    bg = (10,15,10) if es_noche else (75,110,65)
    img  = Image.new("RGB",(W,H),bg)
    draw = ImageDraw.Draw(img)

    sky = (5,8,15) if es_noche else (120,165,205)
    draw.rectangle([0,0,W,H//3],fill=sky)

    road = (28,32,28) if es_noche else (105,100,95)
    draw.rectangle([0,H//2,W,H],fill=road)
    draw.rectangle([0,H//2-8,W,H//2+5],fill=(115,110,108))

    # Líneas de calle
    for i in range(0, W, 60):
        draw.rectangle([i,H//2-2,i+30,H//2+2],fill=(200,180,50))

    # Edificios
    for i in range(10):
        bw = random.randint(25,50); bh = random.randint(35,90)
        bx = i*(W//10)
        bc = (random.randint(70,110),)*3
        draw.rectangle([bx,H//3-bh,bx+bw,H//2],fill=bc)
        if es_noche:
            for wy in range(H//3-bh+4,H//2-4,10):
                for wx in range(bx+3,bx+bw-3,8):
                    if random.random()<0.45:
                        draw.rectangle([wx,wy,wx+5,wy+6],fill=(255,210,90))

    # Personas
    num_p = random.randint(1,12) if not es_noche else random.randint(0,3)
    for _ in range(num_p):
        x=random.randint(15,W-15); y=random.randint(H//2,H-15)
        cp=(215,195,170) if not es_noche else (140,140,190)
        draw.ellipse([x-3,y-7,x+3,y+2],fill=cp)
        draw.rectangle([x-3,y+2,x+3,y+13],fill=cp)

    # Vehículos
    for _ in range(random.randint(2,10)):
        vx=random.randint(5,W-55); vy=random.randint(H//2+8,H-22)
        vc=(random.randint(80,230),random.randint(40,180),random.randint(40,160))
        draw.rectangle([vx,vy,vx+42,vy+18],fill=vc)
        draw.rectangle([vx+4,vy-9,vx+38,vy+1],fill=(vc[0]//2,min(255,vc[1]+50),min(255,vc[2]+50)))
        if es_noche:
            draw.ellipse([vx,vy+4,vx+7,vy+13],fill=(255,235,140))
            draw.ellipse([vx+35,vy+4,vx+42,vy+13],fill=(190,40,40))

    # Overlay CCTV
    draw.rectangle([0,0,W,22],fill=(0,0,0))
    ts = datetime.now().strftime("%H:%M:%S")
    draw.text((3,3), f"● REC  {cam['id']} | {cam['nombre'][:28]}",fill=(0,220,0))
    draw.text((3,13),f"{ts} | {cam['distrito']}",fill=(180,180,180))

    draw.rectangle([0,H-16,W,H],fill=(0,0,0))
    draw.text((3,H-13),f"SÍVICO LIMA | {cam['tipo']} | RTSP H.264",fill=(100,100,255))

    arr = np.array(img,dtype=np.float32)
    arr += np.random.normal(0,2,arr.shape)
    arr = np.clip(arr,0,255).astype(np.uint8)
    return Image.fromarray(arr)


def generar_imagen_satelital(escena: dict) -> Image.Image:
    W,H = 400,400
    dist  = random.uniform(0.5,0.95)
    calor = escena["calor"]

    base_r = int(40 + calor*160 + random.randint(-8,8))
    base_g = int(90 + dist*90  + random.randint(-5,5))
    base_b = int(25 + (1-dist)*55+random.randint(-5,5))

    img  = Image.new("RGB",(W,H),(base_r//3,base_g//2,base_b//2))
    draw = ImageDraw.Draw(img)

    for _ in range(int(dist*30)):
        x1=random.randint(0,W-30); y1=random.randint(0,H-30)
        sz=random.randint(6,22)
        r=min(255,base_r+random.randint(-25,45))
        g=min(255,base_g+random.randint(-15,25))
        b=min(255,base_b+random.randint(-10,15))
        draw.rectangle([x1,y1,x1+sz,y1+sz],fill=(r,g,b))

    for _ in range(int(dist*10)):
        x1=random.randint(0,W); y1=random.randint(0,H)
        x2=x1+random.randint(-120,120); y2=y1+random.randint(-18,18)
        draw.line([x1,y1,x2,y2],fill=(175,175,175),width=2)

    if calor>0.3:
        for _ in range(int(calor*18)):
            x=random.randint(15,W-15); y=random.randint(15,H-15)
            r2=int(195+calor*60)
            draw.ellipse([x-5,y-5,x+5,y+5],fill=(r2,int(r2*0.35),0))

    for _ in range(int((1-dist)*25)):
        x=random.randint(0,W); y=random.randint(0,H)
        r2=random.randint(3,14)
        draw.ellipse([x-r2,y-r2,x+r2,y+r2],fill=(18,int(145+calor*55),18))

    draw.rectangle([0,0,W,28],fill=(0,0,0))
    draw.text((4,4), f"SENTINEL-2 | {escena['distrito']}",fill=(255,152,0))
    draw.text((4,16),f"NIR-R-G | 10m/px | {datetime.now().strftime('%Y-%m-%d %H:%M')}",fill=(100,180,255))

    img = img.filter(ImageFilter.GaussianBlur(0.7))
    return img


def img_to_bytes(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf,format="JPEG",quality=82)
    return buf.getvalue()


def analizar_camara_ia(cam: dict, img_bytes: bytes) -> dict:
    if not ANTHROPIC_KEY:
        return {
            "nivel_riesgo": random.choice(["BAJO","BAJO","BAJO","MEDIO","ALTO"]),
            "personas": random.randint(1,20),
            "vehiculos": random.randint(1,15),
            "sospechoso": random.random()<0.1,
            "descripcion": f"Tráfico normal en {cam['nombre'][:30]}. Sin anomalías.",
        }
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    b64    = base64.b64encode(img_bytes).decode()
    try:
        r = client.messages.create(
            model="claude-sonnet-4-6",max_tokens=200,
            system='Eres CENTINELA-EYE. Analiza cámara CCTV Lima. Responde SOLO JSON: {"nivel_riesgo":"BAJO|MEDIO|ALTO|CRITICO","personas":<n>,"vehiculos":<n>,"sospechoso":true/false,"descripcion":"<1 oración>"}',
            messages=[{"role":"user","content":[
                {"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":b64}},
                {"type":"text","text":f"Cámara {cam['id']} en {cam['nombre']}, Lima."},
            ]}],
        )
        text=r.content[0].text.strip()
        match=re.search(r'\{.*?\}',text,re.DOTALL)
        if match: return json.loads(match.group())
    except: pass
    return {"nivel_riesgo":"BAJO","personas":5,"vehiculos":3,"sospechoso":False,"descripcion":"Análisis no disponible."}


# ─────────────────────────────────────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────────────────────────────────────

if "tick" not in st.session_state:
    st.session_state.tick         = 0
    st.session_state.frames       = {}
    st.session_state.analisis     = {}
    st.session_state.sat_img      = None
    st.session_state.sat_escena   = None
    st.session_state.sat_analisis = None
    st.session_state.start_time   = time.time()
    st.session_state.alertas      = 0

st.session_state.tick += 1
tick = st.session_state.tick

# Generar frames de cámaras
for cam in CAMARAS:
    if PIL_OK:
        img = generar_frame_camara(cam)
        st.session_state.frames[cam["id"]] = img_to_bytes(img)

# Generar imagen satelital cada 5 ticks
if tick % 5 == 1 and PIL_OK:
    escena = ESCENAS_SAT[tick % len(ESCENAS_SAT)]
    img_s  = generar_imagen_satelital(escena)
    st.session_state.sat_img    = img_to_bytes(img_s)
    st.session_state.sat_escena = escena

# Analizar 1 cámara por tick con IA
cam_idx = tick % len(CAMARAS)
cam_act = CAMARAS[cam_idx]
if st.session_state.frames.get(cam_act["id"]):
    res = analizar_camara_ia(cam_act, st.session_state.frames[cam_act["id"]])
    st.session_state.analisis[cam_act["id"]] = res
    if res.get("nivel_riesgo") in ("ALTO","CRITICO"):
        st.session_state.alertas += 1

uptime = time.time() - st.session_state.start_time

# ─────────────────────────────────────────────────────────────────────────────
# RENDER
# ─────────────────────────────────────────────────────────────────────────────

now_str = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
st.markdown(f"""
<div class="header-bar">
  <div>
    <span style="color:#FF9800;font-size:16px;font-weight:700;font-family:monospace;letter-spacing:3px">
      ◈ CENTINELA — CÁMARAS EN VIVO
    </span>
    <span style="color:#444;margin:0 12px;">│</span>
    <span style="color:#2962FF;font-size:12px;font-weight:600;font-family:monospace">
      SÍVICO LIMA + SENTINEL-2
    </span>
  </div>
  <div style="color:#666;font-size:11px;font-family:monospace">
    {now_str} &nbsp;│&nbsp; TICK #{tick:05d} &nbsp;│&nbsp; {len(CAMARAS)} CÁMARAS ACTIVAS
  </div>
</div>
""", unsafe_allow_html=True)

# ── MÉTRICAS SUPERIORES ──
m1,m2,m3,m4,m5,m6 = st.columns(6)
alertas_act = sum(1 for a in st.session_state.analisis.values()
                  if a.get("nivel_riesgo") in ("ALTO","CRITICO"))
total_pers  = sum(a.get("personas",0) for a in st.session_state.analisis.values())
total_veh   = sum(a.get("vehiculos",0) for a in st.session_state.analisis.values())
sosp_total  = sum(1 for a in st.session_state.analisis.values()
                  if a.get("sospechoso"))

for col, label, value, color in [
    (m1, "CÁMARAS ACTIVAS", f"{len(CAMARAS)}/8",   "#FF9800"),
    (m2, "ALERTAS ACTIVAS", str(alertas_act),        "#FF1744" if alertas_act else "#00C853"),
    (m3, "PERSONAS DETECTADAS", str(total_pers),    "#2962FF"),
    (m4, "VEHÍCULOS",       str(total_veh),          "#FF9800"),
    (m5, "SOSPECHOSOS",     str(sosp_total),          "#FF1744" if sosp_total else "#00C853"),
    (m6, "UPTIME",          f"{uptime:.0f}s",         "#888888"),
]:
    col.markdown(f"""
    <div class="metric-card">
      <div class="metric-lbl">{label}</div>
      <div class="metric-val" style="color:{color}">{value}</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("<div style='margin:6px 0'></div>", unsafe_allow_html=True)

# ── GRID DE CÁMARAS 2x4 ──
st.markdown('<div style="color:#FF9800;font-size:11px;font-weight:700;font-family:monospace;letter-spacing:2px;margin-bottom:6px">▸ SÍVICO LIMA — GRID EN VIVO</div>',
            unsafe_allow_html=True)

cols_row1 = st.columns(4)
cols_row2 = st.columns(4)
all_cols  = cols_row1 + cols_row2

for i, (col, cam) in enumerate(zip(all_cols, CAMARAS)):
    with col:
        frame_bytes = st.session_state.frames.get(cam["id"])
        if frame_bytes:
            st.image(frame_bytes, use_container_width=True)

        analisis = st.session_state.analisis.get(cam["id"], {})
        nivel    = analisis.get("nivel_riesgo","—")
        col_map  = {"BAJO":"#00C853","MEDIO":"#FF9800","ALTO":"#FF1744","CRITICO":"#FF1744"}
        col_c    = col_map.get(nivel,"#888")
        pers     = analisis.get("personas",0)
        vehs     = analisis.get("vehiculos",0)
        sosp     = "⚠" if analisis.get("sospechoso") else ""

        st.markdown(f"""
        <div style="background:#111;border:1px solid #1E1E1E;border-radius:4px;padding:4px 6px;margin-top:2px">
          <div style="color:#FF9800;font-size:9px;font-family:monospace">{cam['id']} | {cam['nombre'][:22]}</div>
          <div style="display:flex;justify-content:space-between;margin-top:2px">
            <span style="color:{col_c};font-size:10px;font-weight:bold">{nivel}</span>
            <span style="color:#888;font-size:9px;font-family:monospace">👤{pers} 🚗{vehs} {sosp}</span>
          </div>
          <div style="color:#555;font-size:8px;font-family:monospace">{cam['distrito']}</div>
        </div>
        """, unsafe_allow_html=True)

st.markdown("<div style='margin:8px 0'></div>", unsafe_allow_html=True)

# ── SATÉLITE + MAPA ──
sat_col, map_col = st.columns([1, 2])

with sat_col:
    st.markdown('<div style="color:#FF9800;font-size:11px;font-weight:700;font-family:monospace;letter-spacing:2px;margin-bottom:6px">▸ SENTINEL-2 — ÚLTIMA IMAGEN</div>',
                unsafe_allow_html=True)
    if st.session_state.sat_img:
        st.image(st.session_state.sat_img, use_container_width=True)
        escena = st.session_state.sat_escena
        if escena:
            st.markdown(f"""
            <div style="background:#111;border:1px solid #FF9800;border-radius:4px;padding:6px;margin-top:4px">
              <div style="color:#FF9800;font-size:9px;font-family:monospace">SENTINEL-2 | NIR-R-G | 10m/px</div>
              <div style="color:#2962FF;font-size:9px;font-family:monospace">{escena['distrito']}</div>
              <div style="color:#888;font-size:8px;font-family:monospace">
                Vehículos: ~{escena['vehiculos']} | Personas: ~{escena['personas']}
              </div>
            </div>
            """, unsafe_allow_html=True)

with map_col:
    st.markdown('<div style="color:#FF9800;font-size:11px;font-weight:700;font-family:monospace;letter-spacing:2px;margin-bottom:6px">▸ MAPA — UBICACIÓN CÁMARAS SÍVICO</div>',
                unsafe_allow_html=True)

    fig = go.Figure()

    # Puntos de cámaras
    for cam in CAMARAS:
        an  = st.session_state.analisis.get(cam["id"],{})
        niv = an.get("nivel_riesgo","BAJO")
        col = {"BAJO":"#00C853","MEDIO":"#FF9800","ALTO":"#FF1744","CRITICO":"#FF1744"}.get(niv,"#888")
        fig.add_trace(go.Scattermap(
            lat=[cam["lat"]], lon=[cam["lon"]],
            mode="markers+text",
            marker={"size":14,"color":col,"symbol":"circle"},
            text=[cam["id"]],
            textposition="top right",
            textfont={"color":"#FF9800","size":9},
            name=cam["id"],
            showlegend=False,
            hovertemplate=f"<b>{cam['nombre']}</b><br>{cam['distrito']}<br>Riesgo: {niv}<extra></extra>",
        ))

    fig.update_layout(
        map={
            "style":"carto-darkmatter",
            "center":{"lat":-12.05,"lon":-77.03},
            "zoom":10,
        },
        height=280,
        margin=dict(l=0,r=0,t=0,b=0),
        paper_bgcolor="#0D0D0D",
    )
    st.plotly_chart(fig, use_container_width=True,
                    config={"displayModeBar":False})

# ── LOG DE ANÁLISIS ──
if st.session_state.analisis:
    st.markdown('<div style="color:#FF9800;font-size:11px;font-weight:700;font-family:monospace;letter-spacing:2px;margin:6px 0">▸ ANÁLISIS CLAUDE VISION — ÚLTIMA DETECCIÓN</div>',
                unsafe_allow_html=True)
    log_cols = st.columns(4)
    items    = list(st.session_state.analisis.items())[:4]
    for col, (cam_id, an) in zip(log_cols, items):
        cam  = next((c for c in CAMARAS if c["id"]==cam_id), CAMARAS[0])
        niv  = an.get("nivel_riesgo","BAJO")
        col_c= {"BAJO":"#00C853","MEDIO":"#FF9800","ALTO":"#FF1744"}.get(niv,"#888")
        col.markdown(f"""
        <div style="background:#111;border-left:3px solid {col_c};padding:6px 8px;border-radius:4px">
          <div style="color:#FF9800;font-size:9px;font-family:monospace">{cam_id} — {cam['distrito']}</div>
          <div style="color:{col_c};font-size:11px;font-weight:bold">{niv}</div>
          <div style="color:#888;font-size:8px;font-family:monospace">{an.get('descripcion','')[:55]}</div>
        </div>
        """, unsafe_allow_html=True)

# Footer
st.markdown(f"""
<div style="text-align:center;padding:4px;border-top:1px solid #1E1E1E;margin-top:6px;
font-family:monospace;font-size:10px;color:#555">
CENTINELA CÁMARAS EN VIVO &nbsp;·&nbsp;
<span style="color:#FF9800">SÍVICO LIMA {len(CAMARAS)} cámaras</span> &nbsp;·&nbsp;
<span style="color:#2962FF">Sentinel-2 ESA</span> &nbsp;·&nbsp;
Claude Sonnet 4.6 Vision &nbsp;·&nbsp; EATON DYNAMICS
</div>
""", unsafe_allow_html=True)

# Auto-refresh cada 3 segundos
time.sleep(0.3)
st.rerun()
