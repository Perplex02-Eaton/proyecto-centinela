"""
PROYECTO CENTINELA — DRONE AGENT v1.0 (Raspberry Pi 5)
Agente embarcado para drones físicos Centinela CNTL-XX

Target HW:
  · Raspberry Pi 5 (8GB) Bookworm 64-bit
  · Flight controller: Pixhawk 6X / Cube Orange (MAVLink @ 921600 baud)
  · Cámara: Pi Camera v3 (IMX708) o USB UVC
  · GPS: u-blox NEO-M9N (NMEA por UART)
  · Comms: WiFi 802.11ac + 4G LTE failover
  · NPU opcional: Hailo-8 (26 TOPS)
  · Cooling: PWM fan activo

Capas:
  · Captura de cámara (PiCamera2 / USB OpenCV / sintético)
  · YOLOv11n on-edge (~8-12 fps en Pi 5 sin NPU)
  · Claude Vision en eventos críticos (rate-limited, sólo con uplink)
  · MAVLink 2.0 al flight controller (heartbeat + comandos de modo)
  · Telemetría firmada HMAC-SHA256 al ground (Mission Control)
  · Buffer offline + reenvío al reconectar
  · Failsafe local: RTL al perder uplink, HOVER al perder GPS, RTL si bat<20%
  · Threat score local 0-100
  · Dashboard Rich en TTY local

Ejecución en Pi 5 (producción):
  python3 centinela_drone_agent.py --id CNTL-01 \\
      --uplink http://192.168.1.10:8000 \\
      --fcu /dev/serial0 --camera picamera

Dev/test (sin hardware real, Windows/Mac):
  python centinela_drone_agent.py --id CNTL-DEV --mock
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import platform
import random
import signal
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np

# ─── imports opcionales — graceful fallback en dev/Windows ───────────────────
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    from picamera2 import Picamera2
    HAS_PICAM = True
except ImportError:
    HAS_PICAM = False

try:
    from pymavlink import mavutil
    HAS_MAVLINK = True
except ImportError:
    HAS_MAVLINK = False

try:
    import serial as pyserial
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    from ultralytics import YOLO
    HAS_YOLO = True
except ImportError:
    HAS_YOLO = False

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

try:
    from anthropic import AsyncAnthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

from rich.align import Align
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

# ─── CONSTANTES ──────────────────────────────────────────────────────────────
VERSION         = "1.0.0"
TELEMETRY_HZ    = 2.0          # Frecuencia de uplink al ground
HEARTBEAT_HZ    = 1.0          # Heartbeat MAVLink al FCU
YOLO_FPS        = 8            # Inferencia local YOLOv11n
CLAUDE_MIN_GAP  = 8.0          # Segundos mínimos entre llamadas a Claude
UPLINK_BUFFER   = 500          # Frames de telemetría guardados offline
RTH_TIMEOUT     = 30.0         # Sin heartbeat ground antes de RTL
LOW_BATT_PCT    = 20.0         # Batería del drone que dispara RTL
PI_TEMP_THROT   = 80.0         # °C que dispara reducción de procesamiento
MODEL_DEFAULT   = "claude-sonnet-4-6"
YOLO_WEIGHTS    = "yolo11n.pt"

LIMA_HOME = (-12.0464, -77.0428)   # Plaza Mayor de Lima — RTL por defecto


# ─── DATA CLASSES ────────────────────────────────────────────────────────────
@dataclass
class AgentConfig:
    drone_id:    str = "CNTL-01"
    uplink_url:  str = "http://localhost:8000"
    fcu_device:  str = "/dev/serial0"
    fcu_baud:    int = 921600
    gps_device:  str = "/dev/ttyAMA0"
    camera:      str = "auto"           # auto | picamera | usb | mock
    home_lat:    float = LIMA_HOME[0]
    home_lon:    float = LIMA_HOME[1]
    cruise_alt:  float = 80.0
    hmac_secret: str = "centinela-shared-secret"
    api_key:     Optional[str] = None
    mock:        bool = False


@dataclass
class DroneState:
    lat:       float = 0.0
    lon:       float = 0.0
    alt_m:     float = 0.0
    speed_ms:  float = 0.0
    heading:   float = 0.0
    batt_pct:  float = 100.0
    batt_v:    float = 22.4
    mode:      str = "INIT"             # INIT | PATROL | HOVER | INTERCEPT | RTL
    armed:     bool = False
    gps_fix:   bool = False
    sats:      int = 0
    rssi_dbm:  int = -55
    link_ok:   bool = False             # Uplink al ground


@dataclass
class Detection:
    label:    str
    conf:     float
    bbox:     tuple[int, int, int, int]
    severity: str = "MEDIO"             # BAJO | MEDIO | ALTO | CRÍTICO


@dataclass
class TelemetryFrame:
    ts:         float
    drone_id:   str
    state:      dict
    pi_health:  dict
    detections: list = field(default_factory=list)
    threat:     float = 0.0
    notes:      str = ""


# ─── HW: CÁMARA ──────────────────────────────────────────────────────────────
class CameraSource:
    """PiCamera v3 → USB OpenCV → sintético (auto-fallback)."""

    def __init__(self, mode: str = "auto"):
        self.mode = mode
        self.picam = None
        self.cap = None
        self._tick = 0
        self._init()

    def _init(self):
        if self.mode in ("auto", "picamera") and HAS_PICAM:
            try:
                self.picam = Picamera2()
                cfg = self.picam.create_video_configuration(
                    main={"size": (1280, 720), "format": "RGB888"})
                self.picam.configure(cfg)
                self.picam.start()
                self.mode = "picamera"
                return
            except Exception as e:
                console.log(f"[yellow]PiCamera init falló: {e} → fallback USB[/]")

        if self.mode in ("auto", "usb") and HAS_CV2:
            self.cap = cv2.VideoCapture(0)
            if self.cap.isOpened():
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                self.mode = "usb"
                return
            self.cap = None

        self.mode = "mock"

    def read(self) -> Optional[np.ndarray]:
        self._tick += 1
        if self.mode == "picamera" and self.picam is not None:
            return self.picam.capture_array()
        if self.mode == "usb" and self.cap is not None:
            ok, frame = self.cap.read()
            return frame if ok else None
        h, w = 480, 640
        img = np.zeros((h, w, 3), dtype=np.uint8)
        img[:, :, 0] = (self._tick * 2) % 255
        img[:, :, 1] = ((self._tick + 80) * 3) % 255
        if HAS_CV2:
            cv2.putText(img, f"MOCK FRAME {self._tick}", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
        return img

    def close(self):
        if self.picam:
            try: self.picam.stop()
            except Exception: pass
        if self.cap:
            self.cap.release()


# ─── HW: GPS NMEA ────────────────────────────────────────────────────────────
class GPSReader:
    """u-blox NEO-M9N por UART (NMEA 0183). Mock si no hay serial."""

    def __init__(self, device: str, mock: bool = False):
        self.device = device
        self.mock = mock or not HAS_SERIAL
        self.ser = None
        self.last_fix = (LIMA_HOME[0], LIMA_HOME[1], 0.0)
        self.sats = 0
        self.fix_ok = False
        if not self.mock:
            try:
                self.ser = pyserial.Serial(device, 9600, timeout=0.1)
            except Exception as e:
                console.log(f"[yellow]GPS UART falló: {e} → mock[/]")
                self.mock = True

    async def read_loop(self):
        while True:
            if self.mock:
                lat = LIMA_HOME[0] + random.gauss(0, 0.005)
                lon = LIMA_HOME[1] + random.gauss(0, 0.005)
                self.last_fix = (lat, lon, 80.0 + random.gauss(0, 2))
                self.sats = random.randint(8, 14)
                self.fix_ok = True
            else:
                try:
                    line = self.ser.readline().decode(errors="ignore").strip()
                    if line.startswith("$GPGGA") or line.startswith("$GNGGA"):
                        p = line.split(",")
                        if len(p) > 9 and p[6] != "0":
                            lat = self._nmea(p[2], p[3])
                            lon = self._nmea(p[4], p[5])
                            alt = float(p[9]) if p[9] else 0.0
                            self.last_fix = (lat, lon, alt)
                            self.sats = int(p[7]) if p[7] else 0
                            self.fix_ok = True
                except Exception:
                    self.fix_ok = False
            await asyncio.sleep(0.5)

    @staticmethod
    def _nmea(coord: str, hemi: str) -> float:
        if not coord: return 0.0
        cut = 2 if hemi in "NS" else 3
        deg = int(coord[:cut])
        mins = float(coord[cut:])
        dec = deg + mins / 60.0
        return -dec if hemi in "SW" else dec


# ─── HW: PI 5 SYSTEM MONITOR ─────────────────────────────────────────────────
class SystemMonitor:
    """Salud del Pi 5: CPU, RAM, temp, throttle."""

    @staticmethod
    def snapshot() -> dict:
        out = {"cpu_pct": 0.0, "ram_pct": 0.0, "temp_c": 0.0,
               "throttle": False, "uptime_s": 0.0}
        if HAS_PSUTIL:
            out["cpu_pct"] = psutil.cpu_percent(interval=None)
            out["ram_pct"] = psutil.virtual_memory().percent
            out["uptime_s"] = time.time() - psutil.boot_time()
        try:
            t = Path("/sys/class/thermal/thermal_zone0/temp")
            if t.exists():
                out["temp_c"] = int(t.read_text().strip()) / 1000.0
        except Exception:
            pass
        out["throttle"] = out["temp_c"] >= PI_TEMP_THROT
        return out


# ─── HW: MAVLINK AL FLIGHT CONTROLLER ────────────────────────────────────────
class MAVLinkLink:
    """Heartbeat + cambio de modo a Pixhawk/Cube via pymavlink."""

    def __init__(self, device: str, baud: int, mock: bool = False):
        self.mock = mock or not HAS_MAVLINK
        self.master = None
        if not self.mock:
            try:
                self.master = mavutil.mavlink_connection(device, baud=baud)
                self.master.wait_heartbeat(timeout=5)
                console.log(f"[green]MAVLink conectado: {device}[/]")
            except Exception as e:
                console.log(f"[yellow]MAVLink falló: {e} → mock[/]")
                self.mock = True

    def heartbeat(self):
        if self.mock or not self.master: return
        try:
            self.master.mav.heartbeat_send(
                mavutil.mavlink.MAV_TYPE_ONBOARD_CONTROLLER,
                mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)
        except Exception:
            pass

    def set_mode(self, mode: str):
        if self.mock or not self.master: return
        try:
            mode_id = self.master.mode_mapping().get(mode)
            if mode_id is not None:
                self.master.set_mode(mode_id)
        except Exception as e:
            console.log(f"[red]MAVLink set_mode {mode} falló: {e}[/]")

    def request_rtl(self):  self.set_mode("RTL")
    def request_loiter(self): self.set_mode("LOITER")


# ─── AI: YOLOv11 LOCAL ───────────────────────────────────────────────────────
SEV_BY_LABEL = {
    "person":     "MEDIO",
    "knife":      "CRÍTICO",
    "gun":        "CRÍTICO",
    "fire":       "CRÍTICO",
    "motorcycle": "ALTO",
    "car":        "BAJO",
    "truck":      "BAJO",
    "backpack":   "BAJO",
    "bicycle":    "BAJO",
}


class YoloDetector:
    """YOLOv11n on-edge. Mock con detecciones sintéticas si no hay ultralytics."""

    def __init__(self, weights: str = YOLO_WEIGHTS, mock: bool = False):
        self.mock = mock or not HAS_YOLO
        self.model = None
        if not self.mock:
            try:
                self.model = YOLO(weights)
                console.log(f"[green]YOLOv11 cargado: {weights}[/]")
            except Exception as e:
                console.log(f"[yellow]YOLO falló: {e} → mock[/]")
                self.mock = True

    def infer(self, frame: np.ndarray) -> list[Detection]:
        if self.mock:
            if random.random() < 0.15:
                lbl = random.choice(list(SEV_BY_LABEL.keys()))
                return [Detection(
                    label=lbl, conf=random.uniform(0.55, 0.92),
                    bbox=(100, 100, 300, 400),
                    severity=SEV_BY_LABEL[lbl])]
            return []
        try:
            res = self.model(frame, verbose=False)[0]
            dets = []
            for b in res.boxes:
                lbl = res.names[int(b.cls[0])]
                conf = float(b.conf[0])
                if conf < 0.45: continue
                x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())
                dets.append(Detection(
                    label=lbl, conf=conf, bbox=(x1, y1, x2, y2),
                    severity=SEV_BY_LABEL.get(lbl, "BAJO")))
            return dets
        except Exception:
            return []


# ─── AI: CLAUDE VISION — gated trigger ───────────────────────────────────────
class ClaudeVisionTrigger:
    """Llama a Claude Vision sólo en eventos relevantes (rate-limited)."""

    def __init__(self, api_key: Optional[str], model: str = MODEL_DEFAULT):
        self.client = None
        self.model = model
        self.last_call = 0.0
        self.last_analysis = ""
        if api_key and HAS_ANTHROPIC:
            try:
                self.client = AsyncAnthropic(api_key=api_key)
            except Exception as e:
                console.log(f"[yellow]Anthropic init falló: {e}[/]")

    def should_fire(self, dets: list[Detection]) -> bool:
        if not self.client: return False
        if time.time() - self.last_call < CLAUDE_MIN_GAP: return False
        return any(d.severity in ("ALTO", "CRÍTICO") for d in dets)

    async def analyze(self, frame: np.ndarray, dets: list[Detection]) -> str:
        if not self.client or not HAS_CV2: return ""
        try:
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if not ok: return ""
            b64 = base64.b64encode(buf.tobytes()).decode()
            sev_list = ", ".join(f"{d.label}({d.severity})" for d in dets[:5])
            prompt = (
                f"Eres analista táctico de Centinela. YOLO detectó: {sev_list}. "
                "En 1 frase, evalúa amenaza y recomienda acción "
                "(PATROL/HOVER/INTERCEPT/RTL).")
            msg = await self.client.messages.create(
                model=self.model, max_tokens=120,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": prompt}]}])
            self.last_call = time.time()
            txt = "".join(c.text for c in msg.content if hasattr(c, "text"))
            self.last_analysis = txt.strip()
            return self.last_analysis
        except Exception as e:
            return f"(Claude error: {type(e).__name__})"


# ─── COMMS: UPLINK A MISSION CONTROL ─────────────────────────────────────────
class UplinkClient:
    """POST de telemetría firmada HMAC, con buffer offline + reenvío."""

    def __init__(self, url: str, secret: str, drone_id: str):
        self.url = url.rstrip("/")
        self.secret = secret.encode()
        self.drone_id = drone_id
        self.buffer: deque = deque(maxlen=UPLINK_BUFFER)
        self.last_ack = 0.0
        self.session: Optional[Any] = None

    async def start(self):
        if HAS_AIOHTTP:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=3.0))

    async def stop(self):
        if self.session:
            await self.session.close()

    def _sign(self, payload: bytes) -> str:
        return hmac.new(self.secret, payload, hashlib.sha256).hexdigest()

    def _endpoint(self) -> str:
        return f"{self.url}/api/drones/{self.drone_id}/telemetry"

    async def send(self, frame: TelemetryFrame) -> bool:
        body = json.dumps(asdict(frame), default=str).encode()
        if not self.session:
            self.buffer.append(frame)
            return False
        sig = self._sign(body)
        try:
            async with self.session.post(
                    self._endpoint(), data=body,
                    headers={"Content-Type": "application/json",
                             "X-Centinela-Signature": sig}) as r:
                if r.status < 400:
                    self.last_ack = time.time()
                    await self._flush_buffer()
                    return True
                self.buffer.append(frame)
                return False
        except Exception:
            self.buffer.append(frame)
            return False

    async def _flush_buffer(self):
        if not self.buffer or not self.session: return
        for _ in range(min(len(self.buffer), 20)):
            old = self.buffer.popleft()
            body = json.dumps(asdict(old), default=str).encode()
            sig = self._sign(body)
            try:
                async with self.session.post(
                        self._endpoint(), data=body,
                        headers={"Content-Type": "application/json",
                                 "X-Centinela-Signature": sig,
                                 "X-Centinela-Replay": "1"}) as r:
                    if r.status >= 400:
                        self.buffer.appendleft(old)
                        return
            except Exception:
                self.buffer.appendleft(old)
                return


# ─── DRONE AGENT — orquestador ───────────────────────────────────────────────
class DroneAgent:
    def __init__(self, cfg: AgentConfig):
        self.cfg = cfg
        self.state = DroneState()
        self.detections: list[Detection] = []
        self.last_analysis = ""
        self.threat = 0.0
        self.tick = 0
        self.start_t = time.time()
        self.running = True
        self.pi = SystemMonitor.snapshot()

        self.cam    = CameraSource(cfg.camera if not cfg.mock else "mock")
        self.gps    = GPSReader(cfg.gps_device, mock=cfg.mock)
        self.fcu    = MAVLinkLink(cfg.fcu_device, cfg.fcu_baud, mock=cfg.mock)
        self.yolo   = YoloDetector(mock=cfg.mock)
        self.claude = ClaudeVisionTrigger(
            cfg.api_key or os.getenv("ANTHROPIC_API_KEY"))
        self.uplink = UplinkClient(cfg.uplink_url, cfg.hmac_secret, cfg.drone_id)

    # ── Async loops ─────────────────────────────────────────────────────────
    async def loop_telemetry(self):
        period = 1.0 / TELEMETRY_HZ
        while self.running:
            self._update_state()
            self.pi = SystemMonitor.snapshot()
            self.threat = self._compute_threat()
            tf = TelemetryFrame(
                ts=time.time(), drone_id=self.cfg.drone_id,
                state=asdict(self.state), pi_health=self.pi,
                detections=[asdict(d) for d in self.detections[-5:]],
                threat=self.threat, notes=self.last_analysis[:160])
            self.state.link_ok = await self.uplink.send(tf)
            await asyncio.sleep(period)

    async def loop_vision(self):
        period = 1.0 / YOLO_FPS
        while self.running:
            img = self.cam.read()
            if img is not None:
                self.detections = self.yolo.infer(img)
                if self.claude.should_fire(self.detections):
                    asyncio.create_task(self._fire_claude(img.copy()))
            await asyncio.sleep(period)

    async def _fire_claude(self, img: np.ndarray):
        txt = await self.claude.analyze(img, self.detections)
        if txt:
            self.last_analysis = txt
            console.log(f"[cyan]Claude:[/] {txt}")

    async def loop_heartbeat(self):
        period = 1.0 / HEARTBEAT_HZ
        while self.running:
            self.fcu.heartbeat()
            await asyncio.sleep(period)

    async def loop_failsafe(self):
        while self.running:
            now = time.time()
            link_age = now - (self.uplink.last_ack or self.start_t)

            if link_age > RTH_TIMEOUT and self.state.mode != "RTL":
                console.log(f"[red]FAILSAFE: uplink perdido {link_age:.0f}s → RTL[/]")
                self._goto_rtl()
            elif self.state.batt_pct < LOW_BATT_PCT and self.state.mode != "RTL":
                console.log(f"[red]FAILSAFE: bat {self.state.batt_pct:.0f}% → RTL[/]")
                self._goto_rtl()
            elif not self.state.gps_fix and self.state.mode == "PATROL":
                console.log("[yellow]FAILSAFE: GPS lost → HOVER[/]")
                self.state.mode = "HOVER"
                self.fcu.request_loiter()

            await asyncio.sleep(2.0)

    def _goto_rtl(self):
        self.state.mode = "RTL"
        self.fcu.request_rtl()

    # ── State + threat ──────────────────────────────────────────────────────
    def _update_state(self):
        self.tick += 1
        lat, lon, alt = self.gps.last_fix
        self.state.lat = lat
        self.state.lon = lon
        self.state.alt_m = alt or self.cfg.cruise_alt
        self.state.gps_fix = self.gps.fix_ok
        self.state.sats = self.gps.sats

        if self.cfg.mock:
            self.state.batt_pct = max(15.0,
                100.0 - (time.time() - self.start_t) * 0.05)
            self.state.batt_v = 19.5 + (self.state.batt_pct / 100) * 4.5
            self.state.speed_ms = (random.uniform(6, 14)
                                   if self.state.mode == "PATROL" else 0.0)
            self.state.heading = (self.tick * 4) % 360
            self.state.armed = self.state.mode != "INIT"
            self.state.rssi_dbm = -55 + random.randint(-10, 5)

        if self.state.mode == "INIT" and self.state.gps_fix and self.tick > 4:
            self.state.mode = "PATROL"

    def _compute_threat(self) -> float:
        sev_w = {"BAJO": 5, "MEDIO": 15, "ALTO": 35, "CRÍTICO": 60}
        det_score  = sum(sev_w.get(d.severity, 0) for d in self.detections)
        bat_score  = max(0, LOW_BATT_PCT - self.state.batt_pct)
        link_score = 0 if self.state.link_ok else 15
        gps_score  = 0 if self.state.gps_fix else 10
        thr_score  = 10 if self.pi.get("throttle") else 0
        return min(100.0, det_score + bat_score + link_score + gps_score + thr_score)

    # ── Run ─────────────────────────────────────────────────────────────────
    async def run(self):
        await self.uplink.start()
        gps_task = asyncio.create_task(self.gps.read_loop())
        try:
            with Live(render_dashboard(self), console=console,
                      refresh_per_second=2) as live:
                async def render_loop():
                    while self.running:
                        live.update(render_dashboard(self))
                        await asyncio.sleep(0.5)
                await asyncio.gather(
                    self.loop_telemetry(),
                    self.loop_vision(),
                    self.loop_heartbeat(),
                    self.loop_failsafe(),
                    render_loop(),
                )
        except asyncio.CancelledError:
            pass
        finally:
            self.running = False
            gps_task.cancel()
            self.cam.close()
            await self.uplink.stop()


# ─── DASHBOARD RICH ──────────────────────────────────────────────────────────
def threat_color(t: float) -> str:
    if t >= 75: return "red"
    if t >= 55: return "orange1"
    if t >= 35: return "yellow"
    return "green"


def render_dashboard(a: DroneAgent) -> Layout:
    s = a.state
    layout = Layout()
    layout.split_column(
        Layout(name="head", size=3),
        Layout(name="body"),
        Layout(name="foot", size=3),
    )
    layout["body"].split_row(Layout(name="left"), Layout(name="right"))

    tc = threat_color(a.threat)
    head = Text.assemble(
        ("◈ CENTINELA · DRONE AGENT  ", "bold orange1"),
        (f"{a.cfg.drone_id}  ", "bold cyan"),
        (f"v{VERSION}  ", "dim"),
        (f"MODE: {s.mode}  ", "bold yellow"),
        (f"THREAT: {a.threat:.0f}/100  ", f"bold {tc}"),
        (f"UP: {time.time()-a.start_t:.0f}s", "dim"),
    )
    layout["head"].update(Panel(Align.center(head), border_style="orange1"))

    t1 = Table.grid(padding=(0, 2))
    t1.add_column(style="dim", justify="right")
    t1.add_column(style="bold white")
    t1.add_row("LAT/LON", f"{s.lat:.5f}, {s.lon:.5f}")
    t1.add_row("ALT",     f"{s.alt_m:.1f} m")
    t1.add_row("VEL",     f"{s.speed_ms:.1f} m/s")
    t1.add_row("HDG",     f"{s.heading:.0f}°")
    t1.add_row("BAT",     f"[{'green' if s.batt_pct>40 else 'red'}]"
                          f"{s.batt_pct:.0f}% / {s.batt_v:.1f}V[/]")
    t1.add_row("GPS",     f"[{'green' if s.gps_fix else 'red'}]"
                          f"{'FIX' if s.gps_fix else 'NO FIX'} · {s.sats} sats[/]")
    t1.add_row("RSSI",    f"{s.rssi_dbm} dBm")
    t1.add_row("LINK",    f"[{'green' if s.link_ok else 'red'}]"
                          f"{'ONLINE' if s.link_ok else 'OFFLINE'}[/]")
    t1.add_row("ARMED",   "YES" if s.armed else "NO")
    layout["left"].update(Panel(t1, title="◀ TELEMETRY", border_style="cyan"))

    t2 = Table.grid(padding=(0, 1))
    t2.add_column(style="dim", justify="right")
    t2.add_column()
    t2.add_row("YOLO", f"{len(a.detections)} detecciones")
    for d in a.detections[:5]:
        sc = {"CRÍTICO": "red", "ALTO": "orange1",
              "MEDIO": "yellow", "BAJO": "green"}.get(d.severity, "white")
        t2.add_row("·", f"[{sc}]{d.label:12}[/] {d.conf:.0%}  [{sc}]{d.severity}[/]")

    t2.add_row("", "")
    t2.add_row("CAM",    a.cam.mode)
    t2.add_row("YOLO",   "real" if not a.yolo.mock else "mock")
    t2.add_row("FCU",    "real" if not a.fcu.mock else "mock")
    t2.add_row("CLAUDE", "ready" if a.claude.client else "off")
    t2.add_row("BUFFER", f"{len(a.uplink.buffer)} pendientes")
    t2.add_row("", "")
    t2.add_row("PI CPU", f"{a.pi['cpu_pct']:.0f}%")
    t2.add_row("PI RAM", f"{a.pi['ram_pct']:.0f}%")
    t2.add_row("PI TMP", f"[{'red' if a.pi['temp_c']>=70 else 'green'}]"
                         f"{a.pi['temp_c']:.1f}°C[/]")
    if a.last_analysis:
        t2.add_row("", "")
        t2.add_row("AI", Text(a.last_analysis[:200], style="cyan"))
    layout["right"].update(Panel(t2, title="▶ VISION & SYSTEM", border_style="green"))

    foot = Text.assemble(
        ("◈ EATON DYNAMICS · LIMA, PERÚ  ", "dim"),
        (f"uplink={a.cfg.uplink_url}  ", "dim cyan"),
        (f"home={a.cfg.home_lat:.4f},{a.cfg.home_lon:.4f}  ", "dim"),
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "dim yellow"),
    )
    layout["foot"].update(Panel(Align.center(foot), border_style="dim"))
    return layout


# ─── CLI ─────────────────────────────────────────────────────────────────────
def parse_args() -> AgentConfig:
    p = argparse.ArgumentParser(
        description="Centinela Drone Agent — Raspberry Pi 5 onboard")
    p.add_argument("--id", default="CNTL-01", help="Drone ID (CNTL-XX)")
    p.add_argument("--uplink", default="http://localhost:8000",
                   help="Mission Control URL")
    p.add_argument("--fcu", default="/dev/serial0", help="FCU device")
    p.add_argument("--baud", type=int, default=921600)
    p.add_argument("--gps", default="/dev/ttyAMA0")
    p.add_argument("--camera", default="auto",
                   choices=["auto", "picamera", "usb", "mock"])
    p.add_argument("--home", default=f"{LIMA_HOME[0]},{LIMA_HOME[1]}")
    p.add_argument("--alt", type=float, default=80.0)
    p.add_argument("--secret", default=os.getenv(
        "CENTINELA_HMAC", "centinela-shared-secret"))
    p.add_argument("--api-key", default=os.getenv("ANTHROPIC_API_KEY"))
    p.add_argument("--mock", action="store_true",
                   help="Modo dev sin hardware (Windows/Mac)")
    a = p.parse_args()
    h = a.home.split(",")
    return AgentConfig(
        drone_id=a.id, uplink_url=a.uplink,
        fcu_device=a.fcu, fcu_baud=a.baud, gps_device=a.gps,
        camera=a.camera, home_lat=float(h[0]), home_lon=float(h[1]),
        cruise_alt=a.alt, hmac_secret=a.secret, api_key=a.api_key, mock=a.mock)


def banner(cfg: AgentConfig):
    is_pi = (Path("/sys/firmware/devicetree/base/model").exists() or
             "raspberry" in platform.uname().node.lower())
    console.print(Panel.fit(
        Text.assemble(
            ("◈ CENTINELA DRONE AGENT\n", "bold orange1"),
            (f"  ID:        {cfg.drone_id}\n", "cyan"),
            (f"  Host:      {platform.node()} ({platform.machine()})\n", "dim"),
            (f"  Pi 5:      {'sí' if is_pi else 'no (dev mode)'}\n", "dim"),
            (f"  Cámara:    {cfg.camera}\n", "dim"),
            (f"  FCU:       {cfg.fcu_device} @ {cfg.fcu_baud}\n", "dim"),
            (f"  Uplink:    {cfg.uplink_url}\n", "dim"),
            (f"  HMAC:      {'sí' if cfg.hmac_secret else 'no'}\n", "dim"),
            (f"  Claude:    {'sí' if cfg.api_key else 'no'}\n", "dim"),
            (f"  Mock:      {cfg.mock}", "yellow" if cfg.mock else "green"),
        ), border_style="orange1"))


async def main():
    cfg = parse_args()
    banner(cfg)
    agent = DroneAgent(cfg)

    def stop(*_): agent.running = False
    for s in (signal.SIGINT, signal.SIGTERM):
        try: signal.signal(s, stop)
        except (ValueError, AttributeError, OSError): pass

    await agent.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]◈ Drone agent detenido[/]")
