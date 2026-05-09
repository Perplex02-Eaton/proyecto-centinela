"""
PROYECTO CENTINELA — BASE DE DATOS v1.0
Historial persistente con SQLAlchemy (SQLite dev / PostgreSQL prod)

Tablas:
  - incidentes       → log completo de incidentes detectados
  - detecciones      → cada detección YOLO por frame
  - threat_scores    → historial del Threat Score 0-100
  - telemetria       → GPS/batería/modo por drone en tiempo real
  - alertas          → todas las alertas enviadas a Telegram
  - predicciones_ml  → predicciones de riesgo por distrito

Uso:
  # SQLite (desarrollo — sin configuración)
  python centinela_database.py

  # PostgreSQL (producción)
  set DATABASE_URL=postgresql://user:pass@localhost:5432/centinela
  python centinela_database.py
"""

import os, time, math, random, json
from datetime import datetime, timedelta
from collections import deque

from sqlalchemy import (
    create_engine, Column, Integer, Float, String,
    DateTime, Boolean, Text, ForeignKey, func, desc
)
from sqlalchemy.orm import declarative_base, Session, relationship
from sqlalchemy.exc import SQLAlchemyError

from rich import box as rbox
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text as RText
from rich.columns import Columns
from rich.align import Align
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "sqlite:///C:/EATON/Centinela/centinela.db"
)

# Si usa PostgreSQL en producción:
# DATABASE_URL = "postgresql://centinela_user:password@localhost:5432/centinela_db"

AMBER = "bright_yellow"
BLUE  = "bright_cyan"
RED   = "bright_red"
GREEN = "bright_green"
DIM   = "dim white"
MAG   = "magenta"
WHITE = "white"

console = Console()

# ─────────────────────────────────────────────────────────────────────────────
# MODELOS ORM
# ─────────────────────────────────────────────────────────────────────────────

Base = declarative_base()


class Incidente(Base):
    """
    Registro de cada incidente detectado por el sistema.
    Cadena: Visión IA → Agente → Coordinador → Despacho → Telegram
    """
    __tablename__ = "incidentes"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    incidente_id    = Column(String(20), unique=True, nullable=False)
    timestamp       = Column(DateTime, default=datetime.utcnow, nullable=False)
    tipo            = Column(String(50), nullable=False)
    severidad       = Column(String(20), nullable=False)   # BAJO/MEDIO/ALTO/CRITICO
    sector          = Column(String(100), nullable=False)
    distrito        = Column(String(100))
    lat             = Column(Float)
    lon             = Column(Float)
    drone_asignado  = Column(String(20))
    eta_min         = Column(Float)
    roi_score       = Column(Float)
    tiempo_cadena_s = Column(Float)     # segundos para procesar la cadena completa
    descripcion     = Column(Text)
    anomalia        = Column(Text)
    accion          = Column(Text)
    telegram_enviado= Column(Boolean, default=False)
    resuelto        = Column(Boolean, default=False)
    hora_resolucion = Column(DateTime)

    def __repr__(self):
        return f"<Incidente {self.incidente_id} | {self.tipo} | {self.severidad}>"


class Deteccion(Base):
    """
    Cada detección YOLO procesada por frame.
    """
    __tablename__ = "detecciones"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    timestamp   = Column(DateTime, default=datetime.utcnow)
    camara_id   = Column(String(20))
    distrito    = Column(String(100))
    clase       = Column(String(50))    # person, car, motorcycle, knife...
    confianza   = Column(Float)
    riesgo      = Column(Float)
    bbox_x1     = Column(Integer)
    bbox_y1     = Column(Integer)
    bbox_x2     = Column(Integer)
    bbox_y2     = Column(Integer)
    frame_numero= Column(Integer)
    fuente      = Column(String(20))    # WEBCAM, SIMULADO, RTSP


class ThreatScore(Base):
    """
    Historial del Threat Score compuesto 0-100.
    """
    __tablename__ = "threat_scores"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    timestamp   = Column(DateTime, default=datetime.utcnow)
    score       = Column(Float, nullable=False)
    nivel       = Column(String(20))    # VERDE/AMARILLO/NARANJA/ROJO/CRITICO
    f_telemetria= Column(Float)
    f_vision    = Column(Float)
    f_ml        = Column(Float)
    f_alertas   = Column(Float)
    f_incidentes= Column(Float)
    acciones    = Column(Integer, default=0)


class Telemetria(Base):
    """
    Telemetría de cada drone por tick de simulación.
    """
    __tablename__ = "telemetria"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    timestamp   = Column(DateTime, default=datetime.utcnow)
    drone_id    = Column(String(20), nullable=False)
    sector      = Column(String(100))
    lat         = Column(Float)
    lon         = Column(Float)
    altitud_m   = Column(Float)
    vel_ms      = Column(Float)
    battery_pct = Column(Float)
    rssi_dbm    = Column(Integer)
    motor_health= Column(Float)
    modo        = Column(String(30))    # PATROL/RTH/HOVER/EMERGENCY
    alerta      = Column(String(20))


class Alerta(Base):
    """
    Registro de alertas enviadas a Telegram.
    """
    __tablename__ = "alertas"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    timestamp   = Column(DateTime, default=datetime.utcnow)
    tipo        = Column(String(50))    # BATERIA/GPS/VISION/INCIDENTE/THREAT
    drone_id    = Column(String(20))
    sector      = Column(String(100))
    severidad   = Column(String(20))
    mensaje     = Column(Text)
    enviado     = Column(Boolean, default=True)
    chat_id     = Column(String(30), default="1638287560")
    mensaje_id  = Column(String(30))    # ID del mensaje Telegram para editar


class PrediccionML(Base):
    """
    Predicciones de riesgo por distrito generadas por el modelo ML.
    """
    __tablename__ = "predicciones_ml"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    timestamp   = Column(DateTime, default=datetime.utcnow)
    distrito    = Column(String(100), nullable=False)
    zona        = Column(String(20))
    prob_h0     = Column(Float)     # Probabilidad ahora
    prob_h1     = Column(Float)     # Probabilidad en 1 hora
    prob_h2     = Column(Float)     # Probabilidad en 2 horas
    nivel       = Column(String(20))
    tendencia   = Column(String(5)) # ↑ ↓ →
    modelo      = Column(String(50), default="RF+GB Ensemble")
    accuracy    = Column(Float, default=0.843)


# ─────────────────────────────────────────────────────────────────────────────
# GESTOR DE BASE DE DATOS
# ─────────────────────────────────────────────────────────────────────────────

class CentinelaDB:
    """
    Gestor principal de la base de datos Centinela.
    Compatible con SQLite (dev) y PostgreSQL (prod).
    """

    def __init__(self, url: str = DATABASE_URL):
        self.url    = url
        self.engine = create_engine(
            url,
            echo=False,
            connect_args={"check_same_thread": False} if "sqlite" in url else {},
        )
        Base.metadata.create_all(self.engine)
        self._ops_insert = 0
        self._ops_query  = 0
        self.tipo_db = "SQLite" if "sqlite" in url else "PostgreSQL"

    def session(self) -> Session:
        return Session(self.engine)

    # ── INSERT ──────────────────────────────────────────────────────────────

    def insertar_incidente(self, **kwargs) -> bool:
        try:
            with self.session() as s:
                inc = Incidente(**kwargs)
                s.add(inc); s.commit()
                self._ops_insert += 1
                return True
        except SQLAlchemyError:
            return False

    def insertar_deteccion(self, **kwargs) -> bool:
        try:
            with self.session() as s:
                det = Deteccion(**kwargs)
                s.add(det); s.commit()
                self._ops_insert += 1
                return True
        except SQLAlchemyError:
            return False

    def insertar_threat_score(self, **kwargs) -> bool:
        try:
            with self.session() as s:
                ts = ThreatScore(**kwargs)
                s.add(ts); s.commit()
                self._ops_insert += 1
                return True
        except SQLAlchemyError:
            return False

    def insertar_telemetria(self, **kwargs) -> bool:
        try:
            with self.session() as s:
                tel = Telemetria(**kwargs)
                s.add(tel); s.commit()
                self._ops_insert += 1
                return True
        except SQLAlchemyError:
            return False

    def insertar_alerta(self, **kwargs) -> bool:
        try:
            with self.session() as s:
                al = Alerta(**kwargs)
                s.add(al); s.commit()
                self._ops_insert += 1
                return True
        except SQLAlchemyError:
            return False

    def insertar_prediccion(self, **kwargs) -> bool:
        try:
            with self.session() as s:
                pred = PrediccionML(**kwargs)
                s.add(pred); s.commit()
                self._ops_insert += 1
                return True
        except SQLAlchemyError:
            return False

    # ── QUERIES ─────────────────────────────────────────────────────────────

    def stats_generales(self) -> dict:
        self._ops_query += 1
        try:
            with self.session() as s:
                return {
                    "incidentes":   s.query(func.count(Incidente.id)).scalar() or 0,
                    "detecciones":  s.query(func.count(Deteccion.id)).scalar() or 0,
                    "threat_scores":s.query(func.count(ThreatScore.id)).scalar() or 0,
                    "telemetria":   s.query(func.count(Telemetria.id)).scalar() or 0,
                    "alertas":      s.query(func.count(Alerta.id)).scalar() or 0,
                    "predicciones": s.query(func.count(PrediccionML.id)).scalar() or 0,
                }
        except:
            return {k:0 for k in ["incidentes","detecciones","threat_scores",
                                   "telemetria","alertas","predicciones"]}

    def ultimos_incidentes(self, n: int = 8) -> list:
        self._ops_query += 1
        try:
            with self.session() as s:
                return s.query(Incidente).order_by(
                    desc(Incidente.timestamp)).limit(n).all()
        except:
            return []

    def ultimas_alertas(self, n: int = 6) -> list:
        self._ops_query += 1
        try:
            with self.session() as s:
                return s.query(Alerta).order_by(
                    desc(Alerta.timestamp)).limit(n).all()
        except:
            return []

    def ts_promedio_hoy(self) -> float:
        self._ops_query += 1
        try:
            with self.session() as s:
                hoy = datetime.utcnow().date()
                avg = s.query(func.avg(ThreatScore.score)).filter(
                    func.date(ThreatScore.timestamp) == hoy
                ).scalar()
                return round(float(avg or 0), 1)
        except:
            return 0.0

    def incidentes_por_severidad(self) -> dict:
        self._ops_query += 1
        try:
            with self.session() as s:
                rows = s.query(
                    Incidente.severidad,
                    func.count(Incidente.id)
                ).group_by(Incidente.severidad).all()
                return {sev: cnt for sev, cnt in rows}
        except:
            return {}

    def top_sectores_incidentes(self, n: int = 5) -> list:
        self._ops_query += 1
        try:
            with self.session() as s:
                return s.query(
                    Incidente.sector,
                    func.count(Incidente.id).label("total")
                ).group_by(Incidente.sector).order_by(
                    desc("total")).limit(n).all()
        except:
            return []

    def bat_promedio_flota(self) -> float:
        self._ops_query += 1
        try:
            with self.session() as s:
                subq = s.query(
                    Telemetria.drone_id,
                    func.max(Telemetria.timestamp).label("ultimo")
                ).group_by(Telemetria.drone_id).subquery()
                avg = s.query(func.avg(Telemetria.battery_pct)).join(
                    subq,
                    (Telemetria.drone_id == subq.c.drone_id) &
                    (Telemetria.timestamp == subq.c.ultimo)
                ).scalar()
                return round(float(avg or 0), 1)
        except:
            return 0.0

    def historial_ts_24h(self) -> list:
        self._ops_query += 1
        try:
            with self.session() as s:
                hace_24h = datetime.utcnow() - timedelta(hours=24)
                rows = s.query(ThreatScore.timestamp, ThreatScore.score).filter(
                    ThreatScore.timestamp >= hace_24h
                ).order_by(ThreatScore.timestamp).all()
                return [(str(r.timestamp), r.score) for r in rows]
        except:
            return []

    @property
    def total_ops(self) -> int:
        return self._ops_insert + self._ops_query


# ─────────────────────────────────────────────────────────────────────────────
# GENERADOR DE DATOS SIMULADOS
# ─────────────────────────────────────────────────────────────────────────────

SECTORES_LIMA = [
    "San Juan de Lurigancho","Callao","La Victoria","Cercado de Lima",
    "El Agustino","San Martín de Porres","Comas","Ate",
    "Villa María del Triunfo","Miraflores","San Isidro","Barranco",
]

TIPOS_INCIDENTE = [
    "ROBO_AGRAVADO","ACCIDENTE_VEHICULAR","AGLOMERACION_SOSPECHOSA",
    "INTRUSION_PERIMETRO","PERSONA_SOSPECHOSA","DISTURBIO_CIVIL",
    "VEHICULO_ABANDONADO","INCENDIO_ESTRUCTURAL",
]

SEVERIDADES = ["BAJO","MEDIO","ALTO","CRITICO"]
DRONES      = [f"CNTL-{i+1:02d}" for i in range(6)]
CLASES_YOLO = ["person","car","motorcycle","backpack","bicycle","truck"]


def generar_datos_historicos(db: CentinelaDB, n_dias: int = 30):
    """Genera datos históricos simulados para los últimos n_dias."""
    console.print(f"  [{BLUE}]Generando {n_dias} días de historial...[/]")

    now = datetime.utcnow()

    with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                  BarColumn(), console=console) as prog:
        t = prog.add_task("Insertando registros...", total=n_dias*24)

        for dia in range(n_dias, 0, -1):
            for hora in range(24):
                ts = now - timedelta(days=dia, hours=hora)

                # Threat Score
                score = random.uniform(15, 75)
                nivel = ("VERDE" if score<21 else "AMARILLO" if score<41
                         else "NARANJA" if score<61 else "ROJO")
                db.insertar_threat_score(
                    timestamp=ts, score=round(score,1), nivel=nivel,
                    f_telemetria=random.uniform(5,30),
                    f_vision=random.uniform(5,40),
                    f_ml=random.uniform(5,35),
                    f_alertas=random.uniform(0,20),
                    f_incidentes=random.uniform(0,15),
                )

                # Telemetría por drone (cada 6 horas)
                if hora % 6 == 0:
                    for drone in DRONES:
                        sector = random.choice(SECTORES_LIMA)
                        lat    = -12.0464 + random.uniform(-0.15, 0.15)
                        lon    = -77.0428 + random.uniform(-0.15, 0.15)
                        db.insertar_telemetria(
                            timestamp=ts, drone_id=drone,
                            sector=sector, lat=lat, lon=lon,
                            altitud_m=random.uniform(60,120),
                            vel_ms=random.uniform(5,15),
                            battery_pct=random.uniform(15,100),
                            rssi_dbm=random.randint(-90,-45),
                            motor_health=random.uniform(0.85,1.0),
                            modo=random.choice(["PATROL","PATROL","HOVER","RTH"]),
                            alerta=random.choice(["NOMINAL","NOMINAL","MEDIO","ALTO"]),
                        )

                # Predicciones ML (2 veces por día)
                if hora in [6, 18]:
                    for sector in random.sample(SECTORES_LIMA, 4):
                        p0 = random.uniform(0.1, 0.7)
                        db.insertar_prediccion(
                            timestamp=ts, distrito=sector,
                            zona=random.choice(["NORTE","SUR","ESTE","CENTRO"]),
                            prob_h0=round(p0,3),
                            prob_h1=round(min(1,p0+random.uniform(-0.1,0.15)),3),
                            prob_h2=round(min(1,p0+random.uniform(-0.15,0.2)),3),
                            nivel="ALTO" if p0>0.5 else "MEDIO" if p0>0.3 else "BAJO",
                            tendencia=random.choice(["↑","↓","→"]),
                        )

                # Incidentes (no todos los días/horas)
                if random.random() < 0.15:
                    tipo     = random.choice(TIPOS_INCIDENTE)
                    sev      = random.choice(SEVERIDADES)
                    sector   = random.choice(SECTORES_LIMA)
                    drone    = random.choice(DRONES)
                    inc_id   = f"INC-{dia:03d}-{hora:02d}-{random.randint(100,999)}"
                    db.insertar_incidente(
                        incidente_id=inc_id,
                        timestamp=ts,
                        tipo=tipo, severidad=sev, sector=sector,
                        distrito=sector,
                        lat=-12.0464+random.uniform(-0.1,0.1),
                        lon=-77.0428+random.uniform(-0.1,0.1),
                        drone_asignado=drone,
                        eta_min=round(random.uniform(1,8),1),
                        roi_score=round(random.uniform(0.3,0.9),3),
                        tiempo_cadena_s=round(random.uniform(8,25),1),
                        descripcion=f"Incidente {tipo} detectado en {sector}",
                        telegram_enviado=(sev in ("ALTO","CRITICO")),
                        resuelto=random.random()<0.85,
                    )

                    # Alerta asociada
                    if sev in ("ALTO","CRITICO"):
                        db.insertar_alerta(
                            timestamp=ts,
                            tipo=f"INCIDENTE_{sev}",
                            drone_id=drone, sector=sector,
                            severidad=sev,
                            mensaje=f"CENTINELA: {tipo} en {sector}",
                            enviado=True,
                        )

                # Detecciones YOLO (varias por hora)
                for _ in range(random.randint(0, 4)):
                    db.insertar_deteccion(
                        timestamp=ts,
                        camara_id=f"CAM-{random.randint(1,8):03d}",
                        distrito=random.choice(SECTORES_LIMA),
                        clase=random.choice(CLASES_YOLO),
                        confianza=round(random.uniform(0.45,0.99),3),
                        riesgo=round(random.uniform(0.02,0.40),3),
                        frame_numero=random.randint(1,100000),
                        fuente=random.choice(["WEBCAM","SIMULADO"]),
                    )

                prog.advance(t)

    console.print(f"  [{GREEN}]✓ Historial generado[/]")


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD RICH
# ─────────────────────────────────────────────────────────────────────────────

def ec(sev: str) -> str:
    return {"CRITICO":RED,"ALTO":"bright_red","MEDIO":AMBER,
            "BAJO":GREEN,"VERDE":GREEN}.get(sev, WHITE)


def build_dashboard(db: CentinelaDB, tick: int,
                    ts_actual: float, uptime: float) -> Table:

    stats   = db.stats_generales()
    incids  = db.ultimos_incidentes(6)
    alertas = db.ultimas_alertas(4)
    sev_map = db.incidentes_por_severidad()
    top_sec = db.top_sectores_incidentes(4)
    ts_hoy  = db.ts_promedio_hoy()
    bat_avg = db.bat_promedio_flota()
    ts_str  = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")

    hdr = RText()
    hdr.append("◈ CENTINELA DATABASE", style=f"bold {AMBER}")
    hdr.append("  │  ", style=DIM)
    hdr.append(f"{db.tipo_db}", style=f"bold {BLUE}")
    hdr.append("  │  ", style=DIM)
    hdr.append(ts_str, style=WHITE)
    hdr.append(f"  │  TICK #{tick:05d}  │  {db.total_ops:,} ops", style=MAG)

    # Panel stats
    def sr(l, v, c=WHITE):
        t2 = RText()
        t2.append(f"  {l:<24}", style=DIM)
        t2.append(v, style=f"bold {c}")
        return t2

    stats_content = RText("\n").join([
        sr("Motor BD",        db.tipo_db,                          BLUE),
        sr("URL",             db.url[:35]+"..." if len(db.url)>35 else db.url, DIM),
        sr("Incidentes",      f"{stats['incidentes']:,}",           AMBER),
        sr("Detecciones YOLO",f"{stats['detecciones']:,}",          BLUE),
        sr("Threat Scores",   f"{stats['threat_scores']:,}",        AMBER),
        sr("Telemetría rows", f"{stats['telemetria']:,}",           BLUE),
        sr("Alertas",         f"{stats['alertas']:,}",              RED),
        sr("Predicciones ML", f"{stats['predicciones']:,}",         MAG),
        sr("Total registros", f"{sum(stats.values()):,}",           GREEN),
        sr("Operaciones",     f"{db.total_ops:,} total",            DIM),
        sr("TS promedio hoy", f"{ts_hoy:.1f}/100",                  AMBER),
        sr("BAT prom. flota", f"{bat_avg:.1f}%",                    GREEN),
        sr("Uptime",          f"{uptime:.0f}s",                     DIM),
    ])

    stats_panel = Panel(stats_content,
        title=f"[bold {AMBER}]▸ ESTADÍSTICAS BD[/]",
        border_style=BLUE, padding=(1,1))

    # Panel incidentes
    inc_t = Table(box=None, show_header=False, expand=True)
    inc_t.add_column("ID",    style=f"bold {BLUE}", width=14)
    inc_t.add_column("TIPO",  width=20)
    inc_t.add_column("SEV",   width=9)
    inc_t.add_column("SECTOR",style=DIM)
    inc_t.add_column("DRONE", width=9)

    for inc in incids:
        sc = ec(inc.severidad)
        inc_t.add_row(
            inc.incidente_id,
            inc.tipo[:18],
            f"[{sc}]{inc.severidad}[/]",
            (inc.sector or "?")[:14],
            inc.drone_asignado or "—",
        )
    if not incids:
        inc_t.add_row("—","Sin datos","—","—","—")

    inc_panel = Panel(inc_t,
        title=f"[bold {AMBER}]▸ ÚLTIMOS INCIDENTES — BD[/]",
        border_style=AMBER, padding=(0,1))

    # Panel distribución
    dist_t = Table(box=None, show_header=False, expand=True)
    dist_t.add_column("SEVER.", width=10)
    dist_t.add_column("COUNT",  justify="right", width=8)
    dist_t.add_column("BARRA",  width=24)

    total_inc = sum(sev_map.values()) or 1
    for sev in ["CRITICO","ALTO","MEDIO","BAJO"]:
        cnt = sev_map.get(sev, 0)
        sc  = ec(sev)
        bar = "█" * int(cnt/total_inc*20) + "░"*(20-int(cnt/total_inc*20))
        dist_t.add_row(
            f"[{sc}]{sev}[/]",
            f"[{sc}]{cnt}[/]",
            f"[{sc}]{bar[:20]}[/]",
        )

    # Top sectores
    top_t = Table(box=None, show_header=False, expand=True)
    top_t.add_column("SECTOR", style=f"bold {BLUE}", width=22)
    top_t.add_column("INCID.", justify="right", style=AMBER, width=7)

    for sector, total in top_sec:
        top_t.add_row((sector or "?")[:20], str(total))

    analytics_panel = Panel(
        Table.grid(expand=True, padding=(0,2)).add_row(
            Panel(dist_t,
                title=f"[bold {AMBER}]▸ POR SEVERIDAD[/]",
                border_style=DIM, padding=(0,1)),
            Panel(top_t,
                title=f"[bold {AMBER}]▸ TOP SECTORES[/]",
                border_style=DIM, padding=(0,1)),
        ),
        title=f"[bold {AMBER}]▸ ANALYTICS EN TIEMPO REAL[/]",
        border_style=BLUE, padding=(0,1),
    )

    # Panel alertas
    al_t = Table(box=None, show_header=False, expand=True)
    al_t.add_column("HORA",  style=DIM,          width=9)
    al_t.add_column("TIPO",  style=f"bold {RED}", width=18)
    al_t.add_column("DRONE", style=BLUE,          width=9)
    al_t.add_column("SEV",                        width=9)
    al_t.add_column("ENV",                        width=7)

    for al in alertas:
        sc = ec(al.severidad)
        al_t.add_row(
            al.timestamp.strftime("%H:%M:%S") if al.timestamp else "—",
            (al.tipo or "?")[:16],
            al.drone_id or "—",
            f"[{sc}]{al.severidad or '?'}[/]",
            f"[{GREEN}]✓[/]" if al.enviado else f"[{RED}]✗[/]",
        )
    if not alertas:
        al_t.add_row("—","Sin alertas","—","—","—")

    alertas_panel = Panel(al_t,
        title=f"[bold {AMBER}]▸ ÚLTIMAS ALERTAS TELEGRAM — BD[/]",
        border_style=RED if alertas else DIM, padding=(0,1))

    # Footer
    footer = RText(justify="center")
    footer.append("CENTINELA DATABASE ACTIVE", style=f"bold {AMBER}")
    footer.append("  ·  ", style=DIM)
    footer.append(f"{sum(stats.values()):,} registros totales", style=f"bold {GREEN}")
    footer.append("  ·  ", style=DIM)
    footer.append(f"SQLAlchemy ORM | {db.tipo_db}", style=f"bold {BLUE}")
    footer.append("  ·  Ctrl+C para detener", style=DIM)

    root = Table.grid(expand=True)
    root.add_row(Panel(Align.center(hdr), style=AMBER, padding=(0,2)))
    col2_grid = Table.grid(expand=True)
    col2_grid.add_row(inc_panel)
    col2_grid.add_row(alertas_panel)
    root.add_row(Columns([stats_panel, col2_grid], expand=True))
    root.add_row(analytics_panel)
    root.add_row(Panel(Align.center(footer), style=DIM, padding=(0,1)))
    return root


# ─────────────────────────────────────────────────────────────────────────────
# INSERCIÓN EN TIEMPO REAL — Simula datos del sistema en vivo
# ─────────────────────────────────────────────────────────────────────────────

def insertar_datos_live(db: CentinelaDB, tick: int):
    """Inserta datos reales del sistema en la BD cada tick."""
    import math

    # Threat Score
    score = random.uniform(15, 70) + 10*math.sin(tick*0.1)
    nivel = ("VERDE" if score<21 else "AMARILLO" if score<41
             else "NARANJA" if score<61 else "ROJO")
    db.insertar_threat_score(score=round(score,1), nivel=nivel,
        f_telemetria=random.uniform(5,25),
        f_vision=random.uniform(5,35),
        f_ml=random.uniform(5,30),
        f_alertas=random.uniform(0,15),
        f_incidentes=random.uniform(0,10))

    # Telemetría flota
    for did in [f"CNTL-{i+1:02d}" for i in range(6)]:
        sector = random.choice(SECTORES_LIMA)
        db.insertar_telemetria(
            drone_id=did, sector=sector,
            lat=-12.0464+random.uniform(-0.1,0.1),
            lon=-77.0428+random.uniform(-0.1,0.1),
            altitud_m=random.uniform(60,120),
            vel_ms=random.uniform(5,15),
            battery_pct=random.uniform(30,99),
            rssi_dbm=random.randint(-85,-45),
            motor_health=random.uniform(0.88,1.0),
            modo=random.choice(["PATROL","PATROL","HOVER"]),
            alerta=random.choice(["NOMINAL","NOMINAL","MEDIO"]),
        )

    # Incidente ocasional
    if random.random() < 0.05:
        tipo  = random.choice(TIPOS_INCIDENTE)
        sev   = random.choice(SEVERIDADES)
        sect  = random.choice(SECTORES_LIMA)
        inc_id= f"INC-LIVE-{tick:05d}"
        db.insertar_incidente(
            incidente_id=inc_id, tipo=tipo,
            severidad=sev, sector=sect,
            lat=-12.0464+random.uniform(-0.1,0.1),
            lon=-77.0428+random.uniform(-0.1,0.1),
            drone_asignado=random.choice([f"CNTL-{i+1:02d}" for i in range(6)]),
            eta_min=round(random.uniform(1,8),1),
            roi_score=round(random.uniform(0.3,0.9),3),
            tiempo_cadena_s=round(random.uniform(8,20),1),
            descripcion=f"Incidente {tipo} en {sect}",
            telegram_enviado=(sev in ("ALTO","CRITICO")),
        )


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    console.clear()
    console.print(Panel.fit(
        f"[bold {AMBER}]Inicializando CENTINELA DATABASE...[/]\n"
        f"[{BLUE}]Motor: SQLAlchemy ORM[/]\n"
        f"[{DIM}]URL: {DATABASE_URL[:60]}[/]",
        title="[bold white]EATON DYNAMICS — BASE DE DATOS v1.0[/]",
        border_style=AMBER,
    ))
    time.sleep(1)

    # Conectar BD
    db = CentinelaDB()
    console.print(f"[{GREEN}]✓ BD conectada — {db.tipo_db}[/]")
    console.print(f"[{GREEN}]✓ Tablas creadas: incidentes, detecciones, threat_scores, telemetria, alertas, predicciones_ml[/]")

    # Generar historial si la BD está vacía
    stats = db.stats_generales()
    if stats["incidentes"] == 0:
        console.print(f"\n[{AMBER}]BD vacía — generando historial de 30 días...[/]")
        generar_datos_historicos(db, n_dias=30)
        console.print(f"[{GREEN}]✓ Historial generado[/]")
    else:
        console.print(f"[{BLUE}]BD existente — {sum(stats.values()):,} registros encontrados[/]")

    time.sleep(1.0)

    tick       = 0
    start_time = time.time()
    ts_actual  = 35.0

    try:
        with Live(console=console, refresh_per_second=2, screen=False) as live:
            while True:
                tick     += 1
                uptime    = time.time() - start_time
                ts_actual = random.uniform(20, 65)

                # Insertar datos en tiempo real cada 3 ticks
                if tick % 3 == 0:
                    insertar_datos_live(db, tick)

                live.update(build_dashboard(db, tick, ts_actual, uptime))
                time.sleep(1.5)

    except KeyboardInterrupt:
        final = db.stats_generales()
        console.print(f"\n[bold {AMBER}]◈ CENTINELA DATABASE DETENIDO.[/]")
        console.print(f"  Total registros en BD: {sum(final.values()):,}")
        console.print(f"  Operaciones realizadas: {db.total_ops:,}")
        console.print(f"  Archivo BD: {DATABASE_URL.replace('sqlite:///', '')}")


if __name__ == "__main__":
    main()

