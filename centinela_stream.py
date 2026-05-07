"""
PROYECTO CENTINELA — STREAM WEBSOCKET v1.0
FastAPI + WebSocket + YOLOv11 = Video en tiempo real a 30fps

Arquitectura:
  - FastAPI sirve HTML/JS frontend en localhost:8765
  - WebSocket /ws/video transmite frames JPEG a 30fps
  - WebSocket /ws/meta transmite JSON de detecciones
  - YOLOv11 procesa cada frame localmente
  - Claude Vision analiza cada 30 frames
  - Sin Streamlit reruns = latencia mínima ~30ms

Ejecutar: python centinela_stream.py
Abrir:    http://localhost:8765
"""

import os, time, json, base64, asyncio, threading, re
from datetime import datetime
from collections import deque
from io import BytesIO
from typing import Optional
import urllib.request, urllib.parse
import anthropic

import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

try:
    import cv2
    CV2_OK = True
except ImportError:
    CV2_OK = False

try:
    from PIL import Image, ImageDraw
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

ANTHROPIC_KEY    = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_TOKEN   = "8676501832:AAEYlMd3GogjL-tubZzOFUSfudhx_loh8lk"
TELEGRAM_CHAT_ID = "1638287560"

HOST         = "0.0.0.0"
PORT         = 8765
TARGET_FPS   = 30
JPEG_QUALITY = 75
FRAME_W      = 640
FRAME_H      = 480
YOLO_CONF    = 0.45
CLAUDE_EVERY = 30    # frames entre análisis Claude

CLASES_SEG = {
    "person":     {"color":(0,220,0),   "riesgo":0.10, "emoji":"👤"},
    "car":        {"color":(0,150,255), "riesgo":0.05, "emoji":"🚗"},
    "motorcycle": {"color":(0,80,255),  "riesgo":0.30, "emoji":"🏍"},
    "truck":      {"color":(100,100,255),"riesgo":0.10,"emoji":"🚚"},
    "bicycle":    {"color":(0,200,100), "riesgo":0.05, "emoji":"🚲"},
    "backpack":   {"color":(255,165,0), "riesgo":0.15, "emoji":"🎒"},
    "knife":      {"color":(255,0,0),   "riesgo":0.90, "emoji":"⚔"},
    "cell phone": {"color":(200,200,200),"riesgo":0.05,"emoji":"📱"},
}

# ─────────────────────────────────────────────────────────────────────────────
# ESTADO GLOBAL DEL SISTEMA
# ─────────────────────────────────────────────────────────────────────────────

class SistemaState:
    def __init__(self):
        self.frame_count    = 0
        self.fps_actual     = 0.0
        self.detecciones    = []
        self.personas       = 0
        self.motos          = 0
        self.score          = 0.0
        self.nivel          = "BAJO"
        self.analisis_claude= "Inicializando sistema..."
        self.alertas_total  = 0
        self.start_time     = time.time()
        self.ultimo_frame   = None      # JPEG bytes
        self.lock           = threading.Lock()
        self.clientes_video = set()
        self.clientes_meta  = set()
        self.modelo         = None
        self.modelo_nombre  = "Sin YOLO"
        self.fuente         = "SIMULADO"
        self.running        = True
        self.historial      = deque(maxlen=60)

state = SistemaState()

# ─────────────────────────────────────────────────────────────────────────────
# MOTOR DE CAPTURA Y PROCESAMIENTO
# ─────────────────────────────────────────────────────────────────────────────

def cargar_yolo():
    if not YOLO_OK:
        print("[YOLO] ultralytics no instalado — modo simulación")
        return None
    try:
        m = YOLO("yolo11n.pt")
        print("[YOLO] yolo11n.pt cargado ✓")
        state.modelo_nombre = "YOLOv11n"
        return m
    except:
        try:
            m = YOLO("yolov8n.pt")
            print("[YOLO] yolov8n.pt cargado ✓")
            state.modelo_nombre = "YOLOv8n"
            return m
        except Exception as e:
            print(f"[YOLO] Error: {e}")
            return None


def generar_frame_sintetico(tick: int) -> np.ndarray:
    if not PIL_OK:
        return np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8)

    hora  = datetime.now().hour
    noche = hora < 6 or hora > 20
    bg    = (8,12,8) if noche else (65,100,55)
    img   = Image.new("RGB",(FRAME_W,FRAME_H),bg)
    draw  = ImageDraw.Draw(img)

    sky = (3,5,14) if noche else (110,155,200)
    draw.rectangle([0,0,FRAME_W,FRAME_H//3],fill=sky)

    for i in range(14):
        bw=28+i*3; bh=40+i*5; bx=i*(FRAME_W//14)
        sh=50+i*3
        draw.rectangle([bx,FRAME_H//3-bh,bx+bw,FRAME_H//2+2],fill=(sh,sh,sh+4))
        if noche:
            for wy in range(FRAME_H//3-bh+4,FRAME_H//2-2,11):
                for wx in range(bx+3,bx+bw-2,9):
                    import random
                    if random.random()<0.4:
                        draw.rectangle([wx,wy,wx+5,wy+7],fill=(255,215,85))

    road=(22,26,20) if noche else (98,94,90)
    draw.rectangle([0,FRAME_H//2,FRAME_W,FRAME_H],fill=road)
    draw.rectangle([0,FRAME_H//2-8,FRAME_W,FRAME_H//2+5],fill=(105,101,98))
    for xi in range(0,FRAME_W,55):
        draw.rectangle([xi,FRAME_H//2-3,xi+26,FRAME_H//2],fill=(195,170,40))

    import random
    for _ in range(random.randint(2,10)):
        px=random.randint(15,FRAME_W-15); py=random.randint(FRAME_H//2+5,FRAME_H-20)
        cp=(205,185,162) if not noche else (125,125,182)
        draw.ellipse([px-5,py-16,px+5,py-6],fill=cp)
        ropa=(random.randint(30,200),)*3
        draw.rectangle([px-4,py-6,px+4,py+12],fill=ropa)
        draw.rectangle([px-4,py+12,px,py+22],fill=ropa)
        draw.rectangle([px,py+12,px+4,py+22],fill=ropa)

    for _ in range(random.randint(1,4)):
        vx=random.randint(5,FRAME_W-55); vy=random.randint(FRAME_H//2+8,FRAME_H-22)
        vc=(random.randint(80,230),random.randint(40,180),random.randint(40,160))
        draw.rectangle([vx,vy,vx+46,vy+19],fill=vc)
        draw.rectangle([vx+5,vy-9,vx+41,vy+1],fill=(vc[0]//2,min(255,vc[1]+50),min(255,vc[2]+50)))
        draw.ellipse([vx+2,vy+13,vx+14,vy+21],fill=(28,28,28))
        draw.ellipse([vx+32,vy+13,vx+44,vy+21],fill=(28,28,28))

    draw.rectangle([0,0,FRAME_W,22],fill=(0,0,0))
    ts=datetime.now().strftime("%H:%M:%S.%f")[:-4]
    draw.text((4,4),f"● SIM  CENTINELA-EYE | Lima, Perú | {ts}",fill=(0,200,0))
    draw.text((4,13),f"FRAME #{tick:05d} | {FRAME_W}x{FRAME_H} | YOLO READY",fill=(80,80,255))

    arr=np.array(img,dtype=np.float32)
    arr+=np.random.normal(0,2.5,arr.shape)
    return np.clip(arr,0,255).astype(np.uint8)


def procesar_frame_yolo(frame_rgb: np.ndarray, modelo) -> dict:
    if modelo is None:
        import random
        return {
            "detecciones":   [],
            "personas":      random.randint(1,6),
            "motos":         0,
            "score":         random.uniform(5,25),
            "nivel":         "BAJO",
            "comportamientos":[],
            "frame_anotado": frame_rgb,
            "total_objetos": random.randint(1,4),
        }

    results = modelo(frame_rgb, verbose=False, conf=YOLO_CONF)
    img_pil = Image.fromarray(frame_rgb) if PIL_OK else None
    draw    = ImageDraw.Draw(img_pil) if img_pil else None

    detecciones  = []
    personas = motos = 0
    score    = 0.0

    for result in results:
        if result.boxes is None:
            continue
        for box in result.boxes:
            conf     = float(box.conf[0])
            if conf < YOLO_CONF:
                continue
            cls_id   = int(box.cls[0])
            cls_name = result.names.get(cls_id,"?")
            x1,y1,x2,y2 = map(int,box.xyxy[0].tolist())

            info  = CLASES_SEG.get(cls_name,{"color":(128,128,128),"riesgo":0.05,"emoji":"?"})
            r,g,b = info["color"]
            riesgo= info["riesgo"] * conf

            if cls_name=="person":     personas += 1
            if cls_name=="motorcycle": motos    += 1
            score += riesgo

            detecciones.append({
                "clase":     cls_name,
                "confianza": round(conf,3),
                "riesgo":    round(riesgo,3),
                "emoji":     info["emoji"],
            })

            if draw:
                draw.rectangle([x1,y1,x2,y2],outline=(r,g,b),width=2)
                lbl=f"{cls_name} {conf:.0%}"
                draw.rectangle([x1,y1-16,x1+len(lbl)*7,y1],fill=(r,g,b))
                draw.text((x1+2,y1-14),lbl,fill=(0,0,0))

    comportamientos = []
    if personas >= 8:
        comportamientos.append({"tipo":"AGLOMERACIÓN","nivel":"ALTO",
            "desc":f"{personas} personas en zona"})
    if motos >= 2:
        comportamientos.append({"tipo":"MOTOS_SOSPECHOSAS","nivel":"ALTO",
            "desc":f"{motos} motocicletas detectadas"})
    if any(d["clase"]=="knife" for d in detecciones):
        comportamientos.append({"tipo":"ARMA_DETECTADA","nivel":"CRÍTICO",
            "desc":"Objeto cortante visible"})

    score_norm = min(100.0, score*100)
    if score_norm>=60 or any(c["nivel"]=="CRÍTICO" for c in comportamientos):
        nivel="CRÍTICO"
    elif score_norm>=35 or comportamientos:
        nivel="ALTO"
    elif score_norm>=15:
        nivel="MEDIO"
    else:
        nivel="BAJO"

    frame_out = np.array(img_pil) if img_pil else frame_rgb
    return {
        "detecciones":    detecciones,
        "personas":       personas,
        "motos":          motos,
        "score":          round(score_norm,1),
        "nivel":          nivel,
        "comportamientos":comportamientos,
        "frame_anotado":  frame_out,
        "total_objetos":  len(detecciones),
    }


def analizar_claude_vision(frame: np.ndarray, det: dict) -> str:
    if not ANTHROPIC_KEY:
        return (f"YOLO: {det['personas']} personas | Score {det['score']:.1f} | "
                f"Nivel {det['nivel']} | Monitoreo {'activo' if det['nivel']=='BAJO' else 'ALERTA'}.")
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    img_s  = Image.fromarray(frame).resize((320,240)) if PIL_OK else None
    if not img_s:
        return "Sin imagen para análisis."
    buf = BytesIO()
    img_s.save(buf,format="JPEG",quality=65)
    b64 = base64.b64encode(buf.getvalue()).decode()
    ctx = f"YOLO: personas={det['personas']} motos={det['motos']} score={det['score']:.1f} nivel={det['nivel']}"
    try:
        r = client.messages.create(
            model="claude-sonnet-4-6",max_tokens=120,
            messages=[{"role":"user","content":[
                {"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":b64}},
                {"type":"text","text":f"Cámara seguridad Lima. {ctx}. Evalúa en 1-2 oraciones."},
            ]}],
        )
        return r.content[0].text.strip()
    except:
        return "Claude no disponible."


def enviar_telegram_alerta(det: dict, analisis: str):
    if det["nivel"] not in ("ALTO","CRÍTICO"):
        return
    emoji = "🔴" if det["nivel"]=="CRÍTICO" else "🟠"
    msg   = (f"{emoji} <b>CENTINELA STREAM — {det['nivel']}</b>\n\n"
             f"👤 Personas: {det['personas']}\n"
             f"📊 Score: {det['score']:.1f}/100\n"
             f"🤖 {analisis[:120]}\n"
             f"🕐 {datetime.now().strftime('%H:%M:%S')}")
    try:
        data = urllib.parse.urlencode({
            "chat_id":TELEGRAM_CHAT_ID,"text":msg,"parse_mode":"HTML"
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data=data,method="POST")
        urllib.request.urlopen(req,timeout=6)
    except:
        pass


def loop_captura():
    """Hilo de captura y procesamiento de video."""
    modelo = cargar_yolo()
    state.modelo = modelo

    cap = None
    if CV2_OK:
        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
            cap.set(cv2.CAP_PROP_FPS, TARGET_FPS)
            state.fuente = "WEBCAM"
            print("[CAM] Webcam conectada ✓")
        else:
            cap = None
            state.fuente = "SIMULADO"
            print("[CAM] Sin webcam — modo simulación")

    tick       = 0
    t_fps      = time.time()
    nivel_prev = "BAJO"

    while state.running:
        t0 = time.time()
        tick += 1
        state.frame_count = tick

        # Capturar frame
        if cap and cap.isOpened():
            ret, bgr = cap.read()
            frame_rgb = cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB) if ret else generar_frame_sintetico(tick)
        else:
            frame_rgb = generar_frame_sintetico(tick)

        # YOLO
        det = procesar_frame_yolo(frame_rgb, modelo)

        # Claude cada CLAUDE_EVERY frames
        if tick % CLAUDE_EVERY == 0:
            state.analisis_claude = analizar_claude_vision(det["frame_anotado"], det)

        # Telegram si escala
        if det["nivel"] in ("ALTO","CRÍTICO") and nivel_prev not in ("ALTO","CRÍTICO"):
            threading.Thread(target=enviar_telegram_alerta,
                             args=(det,state.analisis_claude),daemon=True).start()
            state.alertas_total += 1

        nivel_prev = det["nivel"]

        # Codificar JPEG
        frame_out = det["frame_anotado"]
        if PIL_OK:
            buf = BytesIO()
            Image.fromarray(frame_out).save(buf,format="JPEG",quality=JPEG_QUALITY)
            jpeg_bytes = buf.getvalue()
        elif CV2_OK:
            bgr_out = cv2.cvtColor(frame_out,cv2.COLOR_RGB2BGR)
            _,enc   = cv2.imencode(".jpg",bgr_out,[cv2.IMWRITE_JPEG_QUALITY,JPEG_QUALITY])
            jpeg_bytes = enc.tobytes()
        else:
            jpeg_bytes = b""

        # Actualizar estado global
        with state.lock:
            state.ultimo_frame  = jpeg_bytes
            state.detecciones   = det["detecciones"]
            state.personas      = det["personas"]
            state.motos         = det["motos"]
            state.score         = det["score"]
            state.nivel         = det["nivel"]

        # Historial
        state.historial.appendleft({
            "tick":  tick,
            "nivel": det["nivel"],
            "pers":  det["personas"],
            "score": det["score"],
            "hora":  datetime.now().strftime("%H:%M:%S"),
        })

        # FPS
        elapsed = time.time() - t0
        state.fps_actual = round(1.0/max(elapsed,0.001),1)

        # Throttle para TARGET_FPS
        sleep_t = max(0, (1.0/TARGET_FPS) - elapsed)
        time.sleep(sleep_t)

    if cap:
        cap.release()


# ─────────────────────────────────────────────────────────────────────────────
# FRONTEND HTML + JS (Bloomberg Aesthetic)
# ─────────────────────────────────────────────────────────────────────────────

HTML_PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CENTINELA STREAM — EATON DYNAMICS</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body {
  background:#0D0D0D; color:#E0E0E0;
  font-family:'Courier New',monospace;
  height:100vh; overflow:hidden;
}
#header {
  background:linear-gradient(90deg,#0D0D0D,#111827,#0D0D0D);
  border-bottom:2px solid #FF9800;
  padding:8px 16px;
  display:flex; justify-content:space-between; align-items:center;
}
.h-title { color:#FF9800; font-size:15px; font-weight:700; letter-spacing:3px; }
.h-sub   { color:#2962FF; font-size:11px; font-weight:600; letter-spacing:2px; }
.h-time  { color:#666; font-size:11px; }
#main {
  display:grid;
  grid-template-columns: 1fr 320px;
  grid-template-rows: 1fr 140px;
  height:calc(100vh - 50px);
  gap:4px; padding:4px;
}
#video-panel {
  grid-row:1/2; grid-column:1/2;
  background:#000; border:1px solid #1E1E1E;
  border-radius:6px; overflow:hidden; position:relative;
}
#video-feed {
  width:100%; height:100%; object-fit:cover;
}
#video-overlay {
  position:absolute; top:0; left:0; right:0;
  padding:6px 10px;
  background:linear-gradient(#000a,transparent);
  display:flex; justify-content:space-between;
}
.ov-badge {
  background:rgba(0,0,0,0.7); border:1px solid #FF9800;
  border-radius:4px; padding:2px 8px;
  color:#FF9800; font-size:10px; font-weight:700;
}
#threat-bar {
  position:absolute; bottom:0; left:0; right:0;
  padding:6px 10px;
  background:linear-gradient(transparent,#000d);
  display:flex; gap:8px; align-items:center;
}
.tb-label { font-size:10px; color:#888; }
#threat-fill {
  flex:1; height:8px; background:#1E1E1E;
  border-radius:4px; overflow:hidden;
}
#threat-bar-inner {
  height:100%; width:0%; background:#00C853;
  transition:width 0.3s ease, background 0.3s ease;
  border-radius:4px;
}
#threat-score-val { font-size:12px; font-weight:700; color:#FF9800; min-width:50px; }

#info-panel {
  grid-row:1/2; grid-column:2/3;
  display:flex; flex-direction:column; gap:4px;
}
.panel {
  background:#111; border:1px solid #1E1E1E;
  border-radius:6px; padding:8px;
  flex:1; overflow:hidden;
}
.panel-title {
  color:#FF9800; font-size:9px; font-weight:700;
  letter-spacing:2px; border-bottom:1px solid #1E1E1E;
  padding-bottom:4px; margin-bottom:6px;
}
#nivel-badge {
  font-size:18px; font-weight:700; text-align:center;
  padding:6px; border-radius:6px;
  background:#001a00; color:#00C853;
  transition:all 0.3s ease;
}
.metrics-grid {
  display:grid; grid-template-columns:1fr 1fr; gap:4px; margin-top:6px;
}
.metric-item {
  background:#0D0D0D; border-radius:4px; padding:4px 6px; text-align:center;
}
.metric-val { font-size:16px; font-weight:700; color:#2962FF; }
.metric-lbl { font-size:8px; color:#666; }
#detecciones-list { font-size:10px; overflow:hidden; max-height:150px; }
.det-item {
  display:flex; justify-content:space-between;
  padding:2px 0; border-bottom:1px solid #0D0D0D;
}
.det-clase { color:#2962FF; }
.det-conf  { color:#888; }
.det-riesgo{ color:#FF9800; }
#claude-text {
  font-size:10px; color:#E0E0E0; line-height:1.5;
  overflow:hidden; max-height:120px;
}
#historial-grid {
  display:flex; flex-direction:column; gap:2px;
  max-height:120px; overflow:hidden;
}
.hist-item {
  display:flex; justify-content:space-between;
  font-size:9px; padding:1px 0;
  border-bottom:1px solid #0D0D0D;
}

#bottom-bar {
  grid-row:2/3; grid-column:1/3;
  background:#111; border:1px solid #1E1E1E;
  border-radius:6px; padding:8px 12px;
  display:flex; gap:16px; align-items:center; overflow:hidden;
}
.stat-block { text-align:center; min-width:80px; }
.stat-val { font-size:16px; font-weight:700; }
.stat-lbl { font-size:8px; color:#666; }
#fps-bar {
  flex:1; display:flex; align-items:center; gap:8px;
}
.fps-label { color:#666; font-size:10px; }
#fps-chart {
  flex:1; height:40px; display:flex;
  align-items:flex-end; gap:2px;
}
.fps-bar-col {
  flex:1; background:#FF9800;
  min-height:2px; border-radius:1px 1px 0 0;
  transition:height 0.2s ease;
}
#claude-bottom {
  flex:2; background:#0D0D0D; border-radius:6px;
  padding:6px 10px; font-size:10px; color:#888;
}
#connection-status {
  position:fixed; top:50px; right:10px;
  background:#1a0000; border:1px solid #FF1744;
  border-radius:6px; padding:6px 12px;
  color:#FF1744; font-size:11px; font-weight:700;
  display:none;
}
</style>
</head>
<body>

<div id="header">
  <div>
    <div class="h-title">◈ CENTINELA STREAM — VIDEO EN VIVO 30fps</div>
    <div class="h-sub">YOLOv11 + CLAUDE VISION + WEBSOCKET</div>
  </div>
  <div class="h-time" id="clock">--:--:--</div>
</div>

<div id="main">

  <!-- VIDEO -->
  <div id="video-panel">
    <img id="video-feed" src="" alt="Esperando stream...">
    <div id="video-overlay">
      <span class="ov-badge" id="source-badge">● CONECTANDO</span>
      <span class="ov-badge" id="fps-badge">-- FPS</span>
    </div>
    <div id="threat-bar">
      <span class="tb-label">THREAT</span>
      <div id="threat-fill">
        <div id="threat-bar-inner"></div>
      </div>
      <span id="threat-score-val">0/100</span>
    </div>
  </div>

  <!-- INFO PANEL -->
  <div id="info-panel">

    <!-- Nivel + Métricas -->
    <div class="panel">
      <div class="panel-title">▸ ESTADO TÁCTICO</div>
      <div id="nivel-badge">● BAJO</div>
      <div class="metrics-grid">
        <div class="metric-item">
          <div class="metric-val" id="m-personas">0</div>
          <div class="metric-lbl">PERSONAS</div>
        </div>
        <div class="metric-item">
          <div class="metric-val" id="m-motos">0</div>
          <div class="metric-lbl">MOTOS</div>
        </div>
        <div class="metric-item">
          <div class="metric-val" id="m-objetos">0</div>
          <div class="metric-lbl">OBJETOS</div>
        </div>
        <div class="metric-item">
          <div class="metric-val" id="m-alertas">0</div>
          <div class="metric-lbl">ALERTAS</div>
        </div>
      </div>
    </div>

    <!-- Detecciones -->
    <div class="panel">
      <div class="panel-title">▸ DETECCIONES YOLO</div>
      <div id="detecciones-list">
        <div style="color:#444;font-size:10px">Esperando detecciones...</div>
      </div>
    </div>

    <!-- Claude -->
    <div class="panel">
      <div class="panel-title">▸ CLAUDE SONNET 4.6</div>
      <div id="claude-text">Inicializando análisis...</div>
    </div>

    <!-- Historial -->
    <div class="panel">
      <div class="panel-title">▸ HISTORIAL</div>
      <div id="historial-grid"></div>
    </div>

  </div>

  <!-- BOTTOM BAR -->
  <div id="bottom-bar">
    <div class="stat-block">
      <div class="stat-val" id="s-fps" style="color:#FF9800">--</div>
      <div class="stat-lbl">FPS ACTUAL</div>
    </div>
    <div class="stat-block">
      <div class="stat-val" id="s-frames" style="color:#2962FF">0</div>
      <div class="stat-lbl">FRAMES</div>
    </div>
    <div class="stat-block">
      <div class="stat-val" id="s-uptime" style="color:#888">0s</div>
      <div class="stat-lbl">UPTIME</div>
    </div>
    <div class="stat-block">
      <div class="stat-val" id="s-modelo" style="color:#FF9800;font-size:10px">--</div>
      <div class="stat-lbl">MODELO</div>
    </div>
    <div id="fps-bar">
      <span class="fps-label">FPS →</span>
      <div id="fps-chart"></div>
    </div>
    <div id="claude-bottom" id="claude-bot">
      <span style="color:#FF9800">CLAUDE:</span> <span id="claude-bot-txt">Analizando...</span>
    </div>
  </div>

</div>

<div id="connection-status">⚠ CONEXIÓN PERDIDA — RECONECTANDO...</div>

<script>
const NIVEL_COLORS = {
  "BAJO":   {bg:"#001a00", text:"#00C853", bar:"#00C853"},
  "MEDIO":  {bg:"#1a0f00", text:"#FF9800", bar:"#FF9800"},
  "ALTO":   {bg:"#1a0000", text:"#FF1744", bar:"#FF1744"},
  "CRÍTICO":{bg:"#200000", text:"#FF1744", bar:"#FF0000"},
};

let wsVideo = null;
let wsMeta  = null;
let fpsHistory = Array(30).fill(0);
let startTime  = Date.now();
let reconnectTimer = null;

function connectVideo() {
  wsVideo = new WebSocket(`ws://${location.hostname}:${location.port}/ws/video`);
  wsVideo.binaryType = "arraybuffer";

  wsVideo.onopen = () => {
    document.getElementById("source-badge").textContent = "● EN VIVO";
    document.getElementById("source-badge").style.color = "#00C853";
    document.getElementById("connection-status").style.display = "none";
  };

  wsVideo.onmessage = (evt) => {
    const blob = new Blob([evt.data], {type:"image/jpeg"});
    const url  = URL.createObjectURL(blob);
    const img  = document.getElementById("video-feed");
    const old  = img.src;
    img.src    = url;
    if (old.startsWith("blob:")) URL.revokeObjectURL(old);
  };

  wsVideo.onclose = wsVideo.onerror = () => {
    document.getElementById("connection-status").style.display = "block";
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connectVideo, 1500);
  };
}

function connectMeta() {
  wsMeta = new WebSocket(`ws://${location.hostname}:${location.port}/ws/meta`);

  wsMeta.onmessage = (evt) => {
    const d = JSON.parse(evt.data);
    updateUI(d);
  };

  wsMeta.onclose = wsMeta.onerror = () => {
    setTimeout(connectMeta, 1500);
  };
}

function updateUI(d) {
  const nivel = d.nivel || "BAJO";
  const cols  = NIVEL_COLORS[nivel] || NIVEL_COLORS["BAJO"];

  // Nivel badge
  const nb = document.getElementById("nivel-badge");
  nb.textContent       = "■ " + nivel;
  nb.style.background  = cols.bg;
  nb.style.color       = cols.text;
  nb.style.border      = `1px solid ${cols.text}`;

  // Threat bar
  const score = d.score || 0;
  document.getElementById("threat-bar-inner").style.width      = score + "%";
  document.getElementById("threat-bar-inner").style.background = cols.bar;
  document.getElementById("threat-score-val").textContent      = score.toFixed(1) + "/100";
  document.getElementById("threat-score-val").style.color      = cols.text;

  // Métricas
  document.getElementById("m-personas").textContent = d.personas || 0;
  document.getElementById("m-motos").textContent    = d.motos    || 0;
  document.getElementById("m-objetos").textContent  = d.total_objetos || 0;
  document.getElementById("m-alertas").textContent  = d.alertas_total || 0;

  // FPS
  const fps = d.fps || 0;
  document.getElementById("fps-badge").textContent = fps.toFixed(1) + " FPS";
  document.getElementById("s-fps").textContent     = fps.toFixed(1);
  document.getElementById("s-frames").textContent  = d.frame_count || 0;
  document.getElementById("s-modelo").textContent  = d.modelo || "--";
  document.getElementById("s-uptime").textContent  =
    Math.floor((Date.now()-startTime)/1000) + "s";

  fpsHistory.shift(); fpsHistory.push(fps);
  const chart = document.getElementById("fps-chart");
  chart.innerHTML = fpsHistory.map(f =>
    `<div class="fps-bar-col" style="height:${Math.min(100,f/TARGET_FPS*100)}%"></div>`
  ).join("");

  // Detecciones
  const dl = document.getElementById("detecciones-list");
  if (d.detecciones && d.detecciones.length) {
    dl.innerHTML = d.detecciones.slice(0,8).map(det => `
      <div class="det-item">
        <span>${det.emoji} <span class="det-clase">${det.clase}</span></span>
        <span class="det-conf">${(det.confianza*100).toFixed(0)}%</span>
        <span class="det-riesgo">r:${det.riesgo.toFixed(2)}</span>
      </div>`).join("");
  } else {
    dl.innerHTML = '<div style="color:#444;font-size:10px">Sin objetos detectados</div>';
  }

  // Claude
  if (d.analisis_claude) {
    document.getElementById("claude-text").textContent    = d.analisis_claude;
    document.getElementById("claude-bot-txt").textContent = d.analisis_claude.slice(0,80);
  }

  // Historial
  if (d.historial && d.historial.length) {
    const hg = document.getElementById("historial-grid");
    const nc = (n) => NIVEL_COLORS[n]?.text || "#888";
    hg.innerHTML = d.historial.slice(0,8).map(h => `
      <div class="hist-item">
        <span style="color:#555">${h.hora}</span>
        <span style="color:${nc(h.nivel)}">${h.nivel}</span>
        <span style="color:#888">👤${h.pers}</span>
        <span style="color:${nc(h.nivel)}">${h.score.toFixed(1)}</span>
      </div>`).join("");
  }

  // Comportamientos
  if (d.comportamientos && d.comportamientos.length) {
    const msg = d.comportamientos.map(c=>c.tipo).join(" | ");
    document.title = `🚨 ${msg} — CENTINELA`;
  } else {
    document.title = "◈ CENTINELA STREAM — EATON DYNAMICS";
  }
}

// Reloj
setInterval(()=>{
  document.getElementById("clock").textContent =
    new Date().toLocaleTimeString("es-PE",{hour12:false});
},1000);

const TARGET_FPS = 30;

connectVideo();
connectMeta();
</script>
</body>
</html>"""

# ─────────────────────────────────────────────────────────────────────────────
# FASTAPI APP
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="CENTINELA STREAM")

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE


@app.websocket("/ws/video")
async def ws_video(ws: WebSocket):
    await ws.accept()
    state.clientes_video.add(ws)
    try:
        while True:
            with state.lock:
                frame_bytes = state.ultimo_frame
            if frame_bytes:
                await ws.send_bytes(frame_bytes)
            await asyncio.sleep(1.0/TARGET_FPS)
    except (WebSocketDisconnect, Exception):
        state.clientes_video.discard(ws)


@app.websocket("/ws/meta")
async def ws_meta(ws: WebSocket):
    await ws.accept()
    state.clientes_meta.add(ws)
    try:
        while True:
            with state.lock:
                meta = {
                    "nivel":         state.nivel,
                    "score":         state.score,
                    "personas":      state.personas,
                    "motos":         state.motos,
                    "total_objetos": len(state.detecciones),
                    "detecciones":   state.detecciones[:8],
                    "analisis_claude":state.analisis_claude,
                    "alertas_total": state.alertas_total,
                    "fps":           state.fps_actual,
                    "frame_count":   state.frame_count,
                    "modelo":        state.modelo_nombre,
                    "fuente":        state.fuente,
                    "historial":     list(state.historial)[:10],
                    "comportamientos":[],
                    "timestamp":     datetime.now().isoformat(),
                }
            await ws.send_text(json.dumps(meta))
            await asyncio.sleep(0.1)
    except (WebSocketDisconnect, Exception):
        state.clientes_meta.discard(ws)


TARGET_FPS = TARGET_FPS  # exportar para HTML template

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  CENTINELA STREAM — EATON DYNAMICS")
    print("  Video WebSocket a 30fps con YOLOv11")
    print("=" * 60)
    print(f"\n  Abre tu navegador en: http://localhost:{PORT}")
    print(f"  WebSocket video: ws://localhost:{PORT}/ws/video")
    print(f"  WebSocket meta:  ws://localhost:{PORT}/ws/meta")
    print("\n  Ctrl+C para detener\n")

    # Iniciar captura en hilo separado
    hilo_captura = threading.Thread(target=loop_captura, daemon=True)
    hilo_captura.start()

    time.sleep(1.5)  # Esperar init

    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
