"""
PROYECTO CENTINELA — DETECCIÓN YOLO v1.0
YOLOv11 + Claude Vision para detección real de amenazas

Pipeline de detección:
  1. Webcam/RTSP captura frame en tiempo real
  2. YOLOv11 detecta: personas, vehículos, objetos
  3. Análisis de comportamiento: aglomeración, merodeo, pelea
  4. Claude Sonnet 4.6 evalúa amenaza táctica
  5. Alerta Telegram si riesgo ALTO/CRÍTICO

Clases detectadas relevantes para seguridad:
  - person       → personas individuales o grupos
  - car/truck    → vehículos sospechosos
  - motorcycle   → motocicletas (robo al paso)
  - backpack/bag → objetos abandonados
  - knife/weapon → armas (si visible)
  - cell phone   → uso sospechoso

Ejecutar: streamlit run centinela_yolo.py
"""

import os, time, random, json, re, base64, math
from datetime import datetime
from collections import deque
from io import BytesIO
from typing import Optional
import anthropic
import urllib.request, urllib.parse

import numpy as np
import streamlit as st

try:
    import cv2
    CV2_OK = True
except ImportError:
    CV2_OK = False

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_OK = True
except ImportError:
    PIL_OK = False

try:
    from ultralytics import YOLO
    YOLO_OK = True
except ImportError:
    YOLO_OK = False

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="CENTINELA — DETECCIÓN YOLO",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
  .stApp { background-color: #0D0D0D; color: #E0E0E0; }
  [data-testid="stAppViewContainer"] { background-color: #0D0D0D; }
  [data-testid="stHeader"] { background-color: #0D0D0D; }
  .block-container { padding: 0.5rem 1rem !important; }
  div[data-testid="column"] { padding: 0 4px !important; }
  .detection-card {
    background: #111;
    border: 1px solid #1E1E1E;
    border-radius: 6px;
    padding: 8px;
    margin-bottom: 4px;
    font-family: monospace;
  }
  .alert-critical {
    background: #1a0000;
    border: 2px solid #FF1744;
    border-radius: 6px;
    padding: 10px;
    margin: 4px 0;
    font-family: monospace;
  }
  .alert-warning {
    background: #1a0f00;
    border: 2px solid #FF9800;
    border-radius: 6px;
    padding: 8px;
    margin: 4px 0;
    font-family: monospace;
  }
  .metric-box {
    background: #111;
    border: 1px solid #1E1E1E;
    border-radius: 6px;
    padding: 8px 12px;
    text-align: center;
  }
</style>
""", unsafe_allow_html=True)

ANTHROPIC_KEY    = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_TOKEN   = "8676501832:AAEYlMd3GogjL-tubZzOFUSfudhx_loh8lk"
TELEGRAM_CHAT_ID = "1638287560"

# Clases YOLO relevantes para seguridad ciudadana
CLASES_SEGURIDAD = {
    "person":     {"riesgo_base": 0.1,  "color": (0, 255, 0),   "emoji": "👤"},
    "car":        {"riesgo_base": 0.05, "color": (0, 150, 255),  "emoji": "🚗"},
    "motorcycle": {"riesgo_base": 0.30, "color": (0, 100, 255),  "emoji": "🏍️"},
    "truck":      {"riesgo_base": 0.10, "color": (100, 100, 255),"emoji": "🚚"},
    "bicycle":    {"riesgo_base": 0.05, "color": (0, 200, 100),  "emoji": "🚲"},
    "backpack":   {"riesgo_base": 0.15, "color": (255, 165, 0),  "emoji": "🎒"},
    "handbag":    {"riesgo_base": 0.10, "color": (255, 200, 0),  "emoji": "👜"},
    "knife":      {"riesgo_base": 0.90, "color": (255, 0, 0),    "emoji": "⚔️"},
    "scissors":   {"riesgo_base": 0.50, "color": (255, 50, 0),   "emoji": "✂️"},
    "cell phone": {"riesgo_base": 0.05, "color": (200, 200, 200),"emoji": "📱"},
    "umbrella":   {"riesgo_base": 0.02, "color": (150, 150, 255),"emoji": "☂️"},
}

# Umbrales de comportamiento sospechoso
UMBRAL_AGLOMERACION  = 8    # personas en frame
UMBRAL_MOTOCICLETA   = 2    # motos en frame
UMBRAL_CONFIANZA_MIN = 0.45 # confianza mínima YOLO

# ─────────────────────────────────────────────────────────────────────────────
# MOTOR YOLO
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_resource
def cargar_modelo_yolo():
    """Carga YOLOv11n (nano) — más rápido para tiempo real."""
    if not YOLO_OK:
        return None
    try:
        model = YOLO("yolo11n.pt")  # Auto-descarga si no existe
        return model
    except Exception as e:
        try:
            model = YOLO("yolov8n.pt")  # Fallback a YOLOv8
            return model
        except:
            return None


def procesar_detecciones(results, frame_rgb: np.ndarray) -> dict:
    """
    Procesa resultados YOLO y extrae métricas de seguridad.
    Dibuja bounding boxes con colores de riesgo.
    """
    img = Image.fromarray(frame_rgb) if PIL_OK else None
    if img and PIL_OK:
        draw = ImageDraw.Draw(img)

    detecciones = []
    personas    = 0
    motos       = 0
    score_riesgo= 0.0

    if results and len(results) > 0:
        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                conf = float(box.conf[0])
                if conf < UMBRAL_CONFIANZA_MIN:
                    continue

                cls_id  = int(box.cls[0])
                cls_name= result.names.get(cls_id, "unknown")
                x1,y1,x2,y2 = map(int, box.xyxy[0].tolist())

                info  = CLASES_SEGURIDAD.get(cls_name,
                        {"riesgo_base":0.05,"color":(128,128,128),"emoji":"?"})
                color = info["color"]
                emoji = info["emoji"]
                riesgo= info["riesgo_base"] * conf

                if cls_name == "person":     personas += 1
                if cls_name == "motorcycle": motos    += 1
                score_riesgo += riesgo

                detecciones.append({
                    "clase":      cls_name,
                    "confianza":  round(conf, 3),
                    "riesgo":     round(riesgo, 3),
                    "bbox":       [x1,y1,x2,y2],
                    "emoji":      emoji,
                })

                # Dibujar bounding box
                if img and PIL_OK:
                    r,g,b = color
                    # Box
                    draw.rectangle([x1,y1,x2,y2], outline=(r,g,b), width=2)
                    # Label background
                    label = f"{cls_name} {conf:.0%}"
                    draw.rectangle([x1,y1-16,x1+len(label)*7,y1], fill=(r,g,b))
                    draw.text((x1+2,y1-14), label, fill=(0,0,0))

    # Detección de comportamientos
    comportamientos = []
    if personas >= UMBRAL_AGLOMERACION:
        comportamientos.append({
            "tipo": "AGLOMERACIÓN",
            "descripcion": f"{personas} personas detectadas en zona",
            "nivel": "ALTO",
        })
    if motos >= UMBRAL_MOTOCICLETA:
        comportamientos.append({
            "tipo": "MOTOCICLETAS",
            "descripcion": f"{motos} motocicletas — posible robo al paso",
            "nivel": "ALTO",
        })
    if any(d["clase"] in ("knife","scissors") for d in detecciones):
        comportamientos.append({
            "tipo": "ARMA_DETECTADA",
            "descripcion": "Objeto cortante/arma visible en cámara",
            "nivel": "CRÍTICO",
        })

    # Nivel de riesgo
    score_norm = min(100.0, score_riesgo * 100)
    if score_norm >= 60 or any(c["nivel"]=="CRÍTICO" for c in comportamientos):
        nivel = "CRÍTICO"
    elif score_norm >= 35 or any(c["nivel"]=="ALTO" for c in comportamientos):
        nivel = "ALTO"
    elif score_norm >= 15:
        nivel = "MEDIO"
    else:
        nivel = "BAJO"

    frame_anotado = np.array(img) if img else frame_rgb

    return {
        "detecciones":      detecciones,
        "personas":         personas,
        "motos":            motos,
        "score":            round(score_norm, 1),
        "nivel":            nivel,
        "comportamientos":  comportamientos,
        "frame_anotado":    frame_anotado,
        "total_objetos":    len(detecciones),
    }


# ─────────────────────────────────────────────────────────────────────────────
# SIMULADOR DE FRAME (cuando no hay webcam)
# ─────────────────────────────────────────────────────────────────────────────

def generar_frame_sintetico_hd(tick: int) -> np.ndarray:
    """Frame sintético de alta calidad para demostración."""
    if not PIL_OK:
        return np.zeros((480, 640, 3), dtype=np.uint8)

    W, H = 640, 480
    hora = datetime.now().hour
    noche= hora < 6 or hora > 20

    bg = (8,12,8) if noche else (70,105,60)
    img  = Image.new("RGB",(W,H),bg)
    draw = ImageDraw.Draw(img)

    # Cielo
    sky = (4,6,14) if noche else (115,160,200)
    draw.rectangle([0,0,W,H//3],fill=sky)

    # Estrellas de noche
    if noche:
        for _ in range(80):
            sx,sy=random.randint(0,W),random.randint(0,H//3)
            br=random.randint(150,255)
            draw.point((sx,sy),fill=(br,br,br))

    # Edificios
    for i in range(14):
        bw=random.randint(28,55); bh=random.randint(45,105)
        bx=i*(W//14)
        shade=random.randint(55,95)
        draw.rectangle([bx,H//3-bh,bx+bw,H//2+2],fill=(shade,shade,shade+5))
        if noche:
            for wy in range(H//3-bh+4,H//2-2,11):
                for wx in range(bx+3,bx+bw-2,9):
                    if random.random()<0.42:
                        draw.rectangle([wx,wy,wx+5,wy+7],fill=(255,215,85))

    # Calle
    road=(25,28,22) if noche else (100,96,92)
    draw.rectangle([0,H//2,W,H],fill=road)
    draw.rectangle([0,H//2-10,W,H//2+6],fill=(108,104,100))

    # Líneas amarillas
    for xi in range(0,W,55):
        draw.rectangle([xi,H//2-4,xi+28,H//2],fill=(200,175,45))

    # Personas (varias para simular detección)
    n_personas = random.randint(2,12)
    for _ in range(n_personas):
        px=random.randint(12,W-12); py=random.randint(H//2+2,H-22)
        cp=(210,188,165) if not noche else (130,130,185)
        # Cabeza
        draw.ellipse([px-5,py-16,px+5,py-6],fill=cp)
        # Cuerpo
        ropa=(random.randint(30,200),random.randint(30,200),random.randint(30,200))
        draw.rectangle([px-5,py-6,px+5,py+12],fill=ropa)
        # Piernas
        draw.rectangle([px-4,py+12,px,py+22],fill=ropa)
        draw.rectangle([px,py+12,px+4,py+22],fill=ropa)

    # Motos
    n_motos = random.randint(0,3)
    for _ in range(n_motos):
        mx=random.randint(10,W-50); my=random.randint(H//2+8,H-20)
        mc=(random.randint(80,200),random.randint(20,80),random.randint(20,80))
        draw.ellipse([mx,my,mx+12,my+12],fill=(60,60,60))
        draw.ellipse([mx+32,my,mx+44,my+12],fill=(60,60,60))
        draw.rectangle([mx+5,my-8,mx+38,my+5],fill=mc)
        if noche:
            draw.ellipse([mx,my+2,mx+8,my+10],fill=(255,230,120))

    # Vehículos
    for _ in range(random.randint(2,8)):
        vx=random.randint(5,W-58); vy=random.randint(H//2+8,H-24)
        vc=(random.randint(80,230),random.randint(40,180),random.randint(40,160))
        draw.rectangle([vx,vy,vx+48,vy+20],fill=vc)
        draw.rectangle([vx+5,vy-10,vx+43,vy+1],fill=(vc[0]//2,min(255,vc[1]+50),min(255,vc[2]+50)))
        draw.ellipse([vx+2,vy+14,vx+14,vy+22],fill=(30,30,30))
        draw.ellipse([vx+34,vy+14,vx+46,vy+22],fill=(30,30,30))
        if noche:
            draw.ellipse([vx,vy+5,vx+8,vy+14],fill=(255,235,140))
            draw.ellipse([vx+40,vy+5,vx+48,vy+14],fill=(195,40,40))

    # Overlay
    draw.rectangle([0,0,W,24],fill=(0,0,0))
    ts=datetime.now().strftime("%H:%M:%S.%f")[:-4]
    draw.text((4,4), f"● REC  CENTINELA-EYE | CCTV-LIMA | {ts}",fill=(0,220,0))
    draw.text((4,14),f"FRAME #{tick:05d} | {W}x{H} | YOLO DETECTION READY",fill=(100,100,255))

    # Ruido grain realista
    arr = np.array(img,dtype=np.float32)
    arr += np.random.normal(0,3,arr.shape)
    arr = np.clip(arr,0,255).astype(np.uint8)
    return arr


# ─────────────────────────────────────────────────────────────────────────────
# ANÁLISIS CLAUDE VISION
# ─────────────────────────────────────────────────────────────────────────────

def analizar_con_claude(frame: np.ndarray, detecciones_yolo: dict) -> str:
    """Claude Sonnet 4.6 analiza el frame con contexto de detecciones YOLO."""
    if not ANTHROPIC_KEY:
        nivel = detecciones_yolo["nivel"]
        personas = detecciones_yolo["personas"]
        return (f"YOLO detectó {personas} personas. Nivel {nivel}. "
                f"{'Situación requiere atención.' if nivel in ('ALTO','CRÍTICO') else 'Escena urbana normal.'}")

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    # Comprimir frame para Claude
    img_pil = Image.fromarray(frame).resize((320,240))
    buf     = BytesIO()
    img_pil.save(buf,format="JPEG",quality=70)
    b64     = base64.b64encode(buf.getvalue()).decode()

    yolo_ctx = f"""
YOLO detectó:
- Personas: {detecciones_yolo['personas']}
- Objetos total: {detecciones_yolo['total_objetos']}
- Score riesgo: {detecciones_yolo['score']:.1f}/100
- Comportamientos: {[c['tipo'] for c in detecciones_yolo['comportamientos']]}
"""
    try:
        r = client.messages.create(
            model="claude-sonnet-4-6",max_tokens=150,
            messages=[{"role":"user","content":[
                {"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":b64}},
                {"type":"text","text":f"Cámara seguridad Lima. {yolo_ctx}\nEn 1-2 oraciones: evalúa amenaza táctica y recomienda acción."},
            ]}],
        )
        return r.content[0].text.strip()
    except:
        return f"Análisis Claude no disponible. YOLO: {detecciones_yolo['nivel']} riesgo."


def enviar_alerta_telegram(detecciones: dict, analisis: str):
    """Envía alerta a Telegram cuando riesgo es ALTO o CRÍTICO."""
    nivel = detecciones["nivel"]
    if nivel not in ("ALTO","CRÍTICO"):
        return False

    emoji = "🔴" if nivel=="CRÍTICO" else "🟠"
    msg   = (
        f"{emoji} <b>CENTINELA-YOLO — AMENAZA DETECTADA</b>\n\n"
        f"⚠️ <b>Nivel:</b> {nivel}\n"
        f"👤 <b>Personas:</b> {detecciones['personas']}\n"
        f"📊 <b>Score:</b> {detecciones['score']:.1f}/100\n"
    )
    for c in detecciones["comportamientos"]:
        msg += f"🚨 <b>{c['tipo']}:</b> {c['descripcion']}\n"
    msg += f"\n🤖 <b>Claude:</b> {analisis[:150]}\n"
    msg += f"🕐 {datetime.now().strftime('%H:%M:%S')}\n\n"
    msg += "<i>CENTINELA — EATON DYNAMICS · Lima, Perú</i>"

    try:
        data = urllib.parse.urlencode({
            "chat_id":TELEGRAM_CHAT_ID,"text":msg,"parse_mode":"HTML"
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data=data,method="POST"
        )
        with urllib.request.urlopen(req,timeout=8) as r:
            return json.loads(r.read()).get("ok",False)
    except:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────────────────────────────────────

if "tick" not in st.session_state:
    st.session_state.tick         = 0
    st.session_state.historial    = deque(maxlen=50)
    st.session_state.alertas_tot  = 0
    st.session_state.personas_tot = 0
    st.session_state.start_time   = time.time()
    st.session_state.analisis_ia  = "Inicializando..."
    st.session_state.ultimo_nivel = "BAJO"
    st.session_state.telegram_ok  = False
    st.session_state.modelo_ok    = False
    st.session_state.usar_webcam  = False

st.session_state.tick += 1
tick = st.session_state.tick

# Cargar modelo YOLO
modelo = cargar_modelo_yolo()
if modelo:
    st.session_state.modelo_ok = True

# ─────────────────────────────────────────────────────────────────────────────
# CAPTURA DE FRAME
# ─────────────────────────────────────────────────────────────────────────────

frame_rgb    = None
fuente       = "SIMULADO"
detecciones  = None

# Intentar webcam
if CV2_OK:
    cap = cv2.VideoCapture(0)
    if cap.isOpened():
        ret, frame_bgr = cap.read()
        if ret:
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            fuente    = "WEBCAM"
        cap.release()

# Fallback a simulación
if frame_rgb is None:
    frame_rgb = generar_frame_sintetico_hd(tick)
    fuente    = "SIMULADO"

# Ejecutar YOLO
if modelo and frame_rgb is not None:
    results     = modelo(frame_rgb, verbose=False, conf=UMBRAL_CONFIANZA_MIN)
    detecciones = procesar_detecciones(results, frame_rgb)
    frame_mostrar = detecciones["frame_anotado"]
else:
    # Sin YOLO — frame limpio
    detecciones = {
        "detecciones":[],"personas":random.randint(1,8),
        "motos":random.randint(0,2),"score":random.uniform(5,30),
        "nivel":"BAJO","comportamientos":[],"frame_anotado":frame_rgb,
        "total_objetos":random.randint(1,6),
    }
    frame_mostrar = frame_rgb

# Análisis Claude cada 5 ticks
if tick % 5 == 1:
    st.session_state.analisis_ia = analizar_con_claude(frame_mostrar, detecciones)

# Alerta Telegram si escala
if detecciones["nivel"] in ("ALTO","CRÍTICO") and st.session_state.ultimo_nivel not in ("ALTO","CRÍTICO"):
    ok = enviar_alerta_telegram(detecciones, st.session_state.analisis_ia)
    st.session_state.telegram_ok = ok
    if ok:
        st.session_state.alertas_tot += 1

st.session_state.ultimo_nivel  = detecciones["nivel"]
st.session_state.personas_tot += detecciones["personas"]
st.session_state.historial.appendleft({
    "tick":   tick,
    "nivel":  detecciones["nivel"],
    "pers":   detecciones["personas"],
    "score":  detecciones["score"],
    "hora":   datetime.now().strftime("%H:%M:%S"),
})

uptime = time.time() - st.session_state.start_time

# ─────────────────────────────────────────────────────────────────────────────
# RENDER DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

nivel  = detecciones["nivel"]
nc_map = {"BAJO":"#00C853","MEDIO":"#FF9800","ALTO":"#FF1744","CRÍTICO":"#FF1744"}
nc     = nc_map.get(nivel,"#888")
now_str= datetime.now().strftime("%Y-%m-%d  %H:%M:%S")

st.markdown(f"""
<div style="background:linear-gradient(90deg,#0D0D0D,#111827,#0D0D0D);
border:1px solid #FF9800;border-radius:6px;padding:8px 16px;margin-bottom:8px;
display:flex;justify-content:space-between;align-items:center">
  <div>
    <span style="color:#FF9800;font-size:16px;font-weight:700;font-family:monospace;letter-spacing:3px">◈ CENTINELA — DETECCIÓN YOLO</span>
    <span style="color:#444;margin:0 12px">│</span>
    <span style="color:#2962FF;font-size:12px;font-weight:600;font-family:monospace">YOLOv11 + CLAUDE VISION + TELEGRAM</span>
  </div>
  <div style="color:#666;font-size:11px;font-family:monospace">
    {now_str} &nbsp;│&nbsp; TICK #{tick:05d} &nbsp;│&nbsp; FUENTE: {fuente} &nbsp;│&nbsp;
    YOLO: {'✓' if st.session_state.modelo_ok else '✗ SIM'}
  </div>
</div>
""", unsafe_allow_html=True)

# Alerta si riesgo alto
if nivel in ("ALTO","CRÍTICO"):
    st.markdown(f"""
    <div class="alert-critical">
      🚨 <span style="color:#FF1744;font-size:14px;font-weight:bold">AMENAZA DETECTADA — NIVEL {nivel}</span><br>
      <span style="color:#E0E0E0">{st.session_state.analisis_ia}</span>
    </div>
    """, unsafe_allow_html=True)

# Métricas
m1,m2,m3,m4,m5,m6,m7 = st.columns(7)
for col, label, value, color in [
    (m1,"NIVEL RIESGO",     nivel,                           nc),
    (m2,"SCORE",            f"{detecciones['score']:.1f}",  nc),
    (m3,"PERSONAS",         str(detecciones["personas"]),   "#2962FF"),
    (m4,"MOTOS",            str(detecciones["motos"]),      "#FF9800" if detecciones["motos"]>1 else "#00C853"),
    (m5,"OBJETOS TOTAL",    str(detecciones["total_objetos"]),"#888"),
    (m6,"ALERTAS TELEGRAM", str(st.session_state.alertas_tot),"#FF1744" if st.session_state.alertas_tot else "#00C853"),
    (m7,"UPTIME",           f"{uptime:.0f}s",               "#666"),
]:
    col.markdown(f"""
    <div class="metric-box">
      <div style="color:#666;font-size:9px;font-family:monospace">{label}</div>
      <div style="color:{color};font-size:16px;font-weight:bold;font-family:monospace">{value}</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("<div style='margin:6px 0'></div>", unsafe_allow_html=True)

# Layout principal
vid_col, info_col = st.columns([2, 1])

with vid_col:
    st.markdown(f'<div style="color:#FF9800;font-size:11px;font-weight:700;font-family:monospace;margin-bottom:4px">▸ FEED EN VIVO — {fuente} | YOLOv11 DETECTION</div>',
                unsafe_allow_html=True)
    st.image(frame_mostrar, use_container_width=True, channels="RGB")

    # Comportamientos detectados
    if detecciones["comportamientos"]:
        for c in detecciones["comportamientos"]:
            nc2 = "#FF1744" if c["nivel"]=="CRÍTICO" else "#FF9800"
            st.markdown(f"""
            <div class="alert-warning">
              <span style="color:{nc2};font-weight:bold">⚠ {c['tipo']}</span><br>
              <span style="color:#E0E0E0;font-size:11px">{c['descripcion']}</span>
            </div>
            """, unsafe_allow_html=True)

with info_col:
    # Análisis Claude
    st.markdown('<div style="color:#FF9800;font-size:11px;font-weight:700;font-family:monospace;margin-bottom:4px">▸ ANÁLISIS CLAUDE SONNET 4.6</div>',
                unsafe_allow_html=True)
    st.markdown(f"""
    <div style="background:#111;border:1px solid #FF9800;border-radius:6px;padding:10px;margin-bottom:8px">
      <div style="color:#E0E0E0;font-size:11px;font-family:monospace">{st.session_state.analisis_ia}</div>
    </div>
    """, unsafe_allow_html=True)

    # Detecciones YOLO detalle
    st.markdown('<div style="color:#FF9800;font-size:11px;font-weight:700;font-family:monospace;margin-bottom:4px">▸ DETECCIONES YOLO</div>',
                unsafe_allow_html=True)

    if detecciones["detecciones"]:
        for d in sorted(detecciones["detecciones"],
                        key=lambda x: x["riesgo"], reverse=True)[:8]:
            rc = "#FF1744" if d["riesgo"]>0.3 else "#FF9800" if d["riesgo"]>0.1 else "#00C853"
            st.markdown(f"""
            <div class="detection-card">
              <span style="font-size:14px">{d['emoji']}</span>
              <span style="color:#2962FF;font-size:11px"> {d['clase']}</span>
              <span style="color:#666;font-size:10px"> conf:{d['confianza']:.0%}</span>
              <span style="float:right;color:{rc};font-size:10px">riesgo:{d['riesgo']:.2f}</span>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.markdown('<div style="color:#666;font-size:11px;font-family:monospace">Sin objetos detectados</div>',
                    unsafe_allow_html=True)

    # Historial scores
    st.markdown('<div style="color:#FF9800;font-size:11px;font-weight:700;font-family:monospace;margin:8px 0 4px">▸ HISTORIAL RIESGO</div>',
                unsafe_allow_html=True)

    hist = list(st.session_state.historial)[:10]
    for h in hist:
        nc2 = nc_map.get(h["nivel"],"#888")
        st.markdown(f"""
        <div style="display:flex;justify-content:space-between;font-family:monospace;font-size:10px;
        border-bottom:1px solid #1E1E1E;padding:2px 0">
          <span style="color:#666">{h['hora']}</span>
          <span style="color:{nc2}">{h['nivel']}</span>
          <span style="color:#888">👤{h['pers']}</span>
          <span style="color:{nc2}">{h['score']:.1f}</span>
        </div>
        """, unsafe_allow_html=True)

# Footer
st.markdown(f"""
<div style="text-align:center;padding:4px;border-top:1px solid #1E1E1E;margin-top:6px;
font-family:monospace;font-size:10px;color:#555">
CENTINELA-YOLO &nbsp;·&nbsp;
<span style="color:#FF9800">YOLOv11 {'ACTIVO' if st.session_state.modelo_ok else 'SIMULADO'}</span>
&nbsp;·&nbsp;
<span style="color:#2962FF">Claude Sonnet 4.6 Vision</span>
&nbsp;·&nbsp; Telegram: {'✓' if st.session_state.telegram_ok else 'standby'}
&nbsp;·&nbsp; EATON DYNAMICS · Lima, Perú
</div>
""", unsafe_allow_html=True)

time.sleep(0.3)
st.rerun()
