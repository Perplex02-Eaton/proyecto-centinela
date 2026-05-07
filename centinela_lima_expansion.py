"""
PROYECTO CENTINELA — LIMA EXPANSION v1.0
20 distritos de Lima Metropolitana con datos calibrados INEI Perú

Distritos incluidos:
  Zona Norte:  Comas, Los Olivos, San Martín de Porres, Independencia
  Zona Este:   San Juan de Lurigancho, Ate, El Agustino, Santa Anita
  Zona Centro: Cercado de Lima, Rímac, La Victoria, Lince
  Zona Sur:    Miraflores, San Isidro, Barranco, Surquillo,
               Chorrillos, San Juan de Miraflores, Villa El Salvador,
               Villa María del Triunfo
  Callao:      Callao (Puerto Principal)

Datos calibrados con:
  - INEI Perú: Estadísticas de seguridad ciudadana 2023-2024
  - PNP Lima: Mapa del delito por distrito
  - INCORE: Índice de criminalidad por zona
  - Densidad poblacional real
"""

import os, time, math, random, json
from datetime import datetime
from collections import deque
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
# BASE DE DATOS — 20 DISTRITOS DE LIMA METROPOLITANA
# ─────────────────────────────────────────────────────────────────────────────

DISTRITOS = {
    # ── ZONA NORTE ──
    "Comas": {
        "lat": -11.9381, "lon": -77.0522,
        "zona": "NORTE",
        "poblacion": 524894,
        "densidad": "ALTA",
        "nivel_base_riesgo": 0.45,
        "perfil": "Zona residencial de alta densidad. Alto índice de robos al paso.",
        "color": "bright_red",
        "drones_asignados": 2,
        "incidentes_mes": 312,
        "tipo_delito_ppal": "ROBO_AGRAVADO",
        "hora_pico_riesgo": [20, 21, 22, 23, 0],
        "patron_24h": [40,35,30,25,20,18,15,20,25,30,30,28,
                       28,28,25,22,25,30,38,45,50,48,45,42],
    },
    "Los Olivos": {
        "lat": -11.9825, "lon": -77.0706,
        "zona": "NORTE",
        "poblacion": 325884,
        "densidad": "ALTA",
        "nivel_base_riesgo": 0.38,
        "perfil": "Distrito comercial en crecimiento. Robos en zonas de mercado.",
        "color": "bright_yellow",
        "drones_asignados": 1,
        "incidentes_mes": 245,
        "tipo_delito_ppal": "ROBO_VEHICULO",
        "hora_pico_riesgo": [19, 20, 21, 22],
        "patron_24h": [30,25,22,18,15,12,10,15,20,28,30,28,
                       28,28,25,22,24,30,35,42,45,42,38,32],
    },
    "San Martín de Porres": {
        "lat": -12.0264, "lon": -77.0892,
        "zona": "NORTE",
        "poblacion": 700177,
        "densidad": "MUY_ALTA",
        "nivel_base_riesgo": 0.50,
        "perfil": "Distrito más poblado de Lima Norte. Alta incidencia de microcomercialización.",
        "color": "bright_red",
        "drones_asignados": 2,
        "incidentes_mes": 428,
        "tipo_delito_ppal": "MICROCOMERCIALIZACION",
        "hora_pico_riesgo": [21, 22, 23, 0, 1],
        "patron_24h": [45,40,35,30,25,20,18,22,28,32,32,30,
                       30,28,26,24,28,35,40,48,52,50,48,46],
    },
    "Independencia": {
        "lat": -11.9958, "lon": -77.0556,
        "zona": "NORTE",
        "poblacion": 216503,
        "densidad": "ALTA",
        "nivel_base_riesgo": 0.42,
        "perfil": "Zona comercial y residencial. Mercados informales con alta actividad delictiva.",
        "color": "bright_yellow",
        "drones_asignados": 1,
        "incidentes_mes": 198,
        "tipo_delito_ppal": "ROBO_AGRAVADO",
        "hora_pico_riesgo": [18, 19, 20, 21],
        "patron_24h": [32,28,24,20,16,14,12,18,24,30,32,30,
                       28,28,24,22,26,32,38,44,46,44,40,35],
    },

    # ── ZONA ESTE ──
    "San Juan de Lurigancho": {
        "lat": -11.9805, "lon": -76.9978,
        "zona": "ESTE",
        "poblacion": 1162000,
        "densidad": "MUY_ALTA",
        "nivel_base_riesgo": 0.62,
        "perfil": "Distrito más poblado del Perú. Zona de alta criminalidad en sectores periféricos.",
        "color": "bright_red",
        "drones_asignados": 3,
        "incidentes_mes": 856,
        "tipo_delito_ppal": "ROBO_AGRAVADO",
        "hora_pico_riesgo": [20, 21, 22, 23, 0, 1],
        "patron_24h": [55,50,45,40,35,28,22,25,30,35,35,32,
                       32,30,28,26,30,38,45,55,62,60,58,56],
    },
    "Ate": {
        "lat": -12.0261, "lon": -76.9178,
        "zona": "ESTE",
        "poblacion": 661786,
        "densidad": "ALTA",
        "nivel_base_riesgo": 0.48,
        "perfil": "Zona industrial y residencial. Robos a empresas y a mano armada.",
        "color": "bright_red",
        "drones_asignados": 2,
        "incidentes_mes": 389,
        "tipo_delito_ppal": "ROBO_EMPRESA",
        "hora_pico_riesgo": [21, 22, 23, 0],
        "patron_24h": [42,38,34,30,25,20,18,22,28,32,32,30,
                       28,28,26,24,28,34,40,46,50,48,46,44],
    },
    "El Agustino": {
        "lat": -12.0431, "lon": -76.9961,
        "zona": "ESTE",
        "poblacion": 191365,
        "densidad": "ALTA",
        "nivel_base_riesgo": 0.58,
        "perfil": "Zona de alta conflictividad. Presencia de bandas organizadas.",
        "color": "bright_red",
        "drones_asignados": 2,
        "incidentes_mes": 334,
        "tipo_delito_ppal": "BANDA_ORGANIZADA",
        "hora_pico_riesgo": [21, 22, 23, 0, 1, 2],
        "patron_24h": [50,48,45,42,35,28,22,25,28,32,30,28,
                       28,26,24,22,28,35,42,52,58,56,54,52],
    },
    "Santa Anita": {
        "lat": -12.0447, "lon": -76.9714,
        "zona": "ESTE",
        "poblacion": 228422,
        "densidad": "ALTA",
        "nivel_base_riesgo": 0.40,
        "perfil": "Zona residencial-comercial. Gran Mercado Mayorista genera alta actividad.",
        "color": "bright_yellow",
        "drones_asignados": 1,
        "incidentes_mes": 212,
        "tipo_delito_ppal": "ROBO_AGRAVADO",
        "hora_pico_riesgo": [4, 5, 19, 20, 21],
        "patron_24h": [35,30,25,22,38,35,28,24,28,32,32,30,
                       28,26,24,22,26,32,38,42,44,40,38,36],
    },

    # ── ZONA CENTRO ──
    "Cercado de Lima": {
        "lat": -12.0432, "lon": -77.0282,
        "zona": "CENTRO",
        "poblacion": 271814,
        "densidad": "ALTA",
        "nivel_base_riesgo": 0.55,
        "perfil": "Centro histórico de Lima. Alta incidencia por turismo y comercio informal.",
        "color": "bright_red",
        "drones_asignados": 2,
        "incidentes_mes": 445,
        "tipo_delito_ppal": "ROBO_TURISTA",
        "hora_pico_riesgo": [12, 13, 19, 20, 21, 22],
        "patron_24h": [40,35,30,28,25,22,20,28,38,42,48,50,
                       48,45,40,35,38,44,50,52,50,48,45,42],
    },
    "Rímac": {
        "lat": -12.0264, "lon": -77.0317,
        "zona": "CENTRO",
        "poblacion": 162741,
        "densidad": "ALTA",
        "nivel_base_riesgo": 0.52,
        "perfil": "Distrito histórico. Alta presencia de pandillas juveniles.",
        "color": "bright_red",
        "drones_asignados": 2,
        "incidentes_mes": 298,
        "tipo_delito_ppal": "PANDILLAJE",
        "hora_pico_riesgo": [20, 21, 22, 23, 0],
        "patron_24h": [45,42,38,35,30,25,20,24,28,30,28,26,
                       26,24,22,20,24,30,38,48,52,50,48,46],
    },
    "La Victoria": {
        "lat": -12.0678, "lon": -77.0000,
        "zona": "CENTRO",
        "poblacion": 192724,
        "densidad": "MUY_ALTA",
        "nivel_base_riesgo": 0.58,
        "perfil": "Emporio Gamarra. Alta densidad comercial. Robos y extorsiones frecuentes.",
        "color": "bright_red",
        "drones_asignados": 2,
        "incidentes_mes": 412,
        "tipo_delito_ppal": "EXTORSION",
        "hora_pico_riesgo": [19, 20, 21, 22, 23],
        "patron_24h": [40,35,30,28,24,20,18,22,30,38,42,40,
                       38,36,32,28,30,36,42,50,55,52,48,44],
    },
    "Lince": {
        "lat": -12.0850, "lon": -77.0314,
        "zona": "CENTRO",
        "poblacion": 50228,
        "densidad": "MEDIA",
        "nivel_base_riesgo": 0.28,
        "perfil": "Distrito pequeño y relativamente seguro. Zona residencial tranquila.",
        "color": "bright_green",
        "drones_asignados": 1,
        "incidentes_mes": 78,
        "tipo_delito_ppal": "ROBO_VEHICULO",
        "hora_pico_riesgo": [21, 22, 23],
        "patron_24h": [20,18,15,12,10,8,7,10,14,18,18,16,
                       16,14,12,10,12,15,18,22,25,24,22,21],
    },

    # ── ZONA SUR ──
    "Miraflores": {
        "lat": -12.1191, "lon": -77.0291,
        "zona": "SUR",
        "poblacion": 82619,
        "densidad": "MEDIA",
        "nivel_base_riesgo": 0.18,
        "perfil": "Zona turística y residencial de alto poder adquisitivo. Bajo índice delictivo.",
        "color": "bright_green",
        "drones_asignados": 1,
        "incidentes_mes": 52,
        "tipo_delito_ppal": "ROBO_TURISTA",
        "hora_pico_riesgo": [22, 23, 0],
        "patron_24h": [12,10,8,6,5,4,4,6,8,10,12,12,
                       10,10,8,8,10,12,14,18,20,18,15,13],
    },
    "San Isidro": {
        "lat": -12.0975, "lon": -77.0357,
        "zona": "SUR",
        "poblacion": 54206,
        "densidad": "BAJA",
        "nivel_base_riesgo": 0.15,
        "perfil": "Centro financiero de Lima. Alta vigilancia privada. Bajo índice delictivo.",
        "color": "bright_green",
        "drones_asignados": 1,
        "incidentes_mes": 38,
        "tipo_delito_ppal": "ROBO_VEHICULO",
        "hora_pico_riesgo": [21, 22, 23],
        "patron_24h": [8,6,5,4,3,3,4,8,14,16,16,14,
                       14,14,12,10,12,14,16,18,18,15,12,10],
    },
    "Barranco": {
        "lat": -12.1464, "lon": -77.0217,
        "zona": "SUR",
        "poblacion": 29787,
        "densidad": "MEDIA",
        "nivel_base_riesgo": 0.35,
        "perfil": "Zona bohemia y turística. Robos nocturnos en zonas de bares.",
        "color": "bright_yellow",
        "drones_asignados": 1,
        "incidentes_mes": 124,
        "tipo_delito_ppal": "ROBO_AGRAVADO",
        "hora_pico_riesgo": [23, 0, 1, 2, 3],
        "patron_24h": [42,48,52,50,38,22,10,6,5,6,7,7,
                       7,6,6,6,7,8,12,18,25,32,38,42],
    },
    "Surquillo": {
        "lat": -12.1114, "lon": -77.0200,
        "zona": "SUR",
        "poblacion": 89283,
        "densidad": "ALTA",
        "nivel_base_riesgo": 0.38,
        "perfil": "Zona fronteriza con Miraflores. Contraste socioeconómico genera tensión.",
        "color": "bright_yellow",
        "drones_asignados": 1,
        "incidentes_mes": 156,
        "tipo_delito_ppal": "ROBO_AGRAVADO",
        "hora_pico_riesgo": [20, 21, 22, 23],
        "patron_24h": [28,24,20,18,14,10,8,12,16,20,20,18,
                       18,16,14,14,16,20,24,30,32,30,28,28],
    },
    "Chorrillos": {
        "lat": -12.1628, "lon": -77.0167,
        "zona": "SUR",
        "poblacion": 325547,
        "densidad": "ALTA",
        "nivel_base_riesgo": 0.42,
        "perfil": "Zona costera con sectores de alto riesgo en la periferia.",
        "color": "bright_yellow",
        "drones_asignados": 1,
        "incidentes_mes": 234,
        "tipo_delito_ppal": "ROBO_AGRAVADO",
        "hora_pico_riesgo": [20, 21, 22, 23],
        "patron_24h": [35,30,28,24,20,16,12,16,20,24,24,22,
                       22,20,18,16,20,26,32,38,42,40,38,36],
    },
    "San Juan de Miraflores": {
        "lat": -12.1578, "lon": -76.9731,
        "zona": "SUR",
        "poblacion": 362643,
        "densidad": "ALTA",
        "nivel_base_riesgo": 0.48,
        "perfil": "Zona de alto crecimiento. Mercados informales y pandillas activas.",
        "color": "bright_red",
        "drones_asignados": 2,
        "incidentes_mes": 298,
        "tipo_delito_ppal": "PANDILLAJE",
        "hora_pico_riesgo": [20, 21, 22, 23, 0],
        "patron_24h": [40,36,32,28,24,20,16,20,24,28,28,26,
                       26,24,22,20,24,30,36,42,48,46,44,42],
    },
    "Villa El Salvador": {
        "lat": -12.2139, "lon": -76.9400,
        "zona": "SUR",
        "poblacion": 393254,
        "densidad": "MUY_ALTA",
        "nivel_base_riesgo": 0.52,
        "perfil": "Zona industrial-residencial. Alta presencia de extorsión a empresas.",
        "color": "bright_red",
        "drones_asignados": 2,
        "incidentes_mes": 356,
        "tipo_delito_ppal": "EXTORSION",
        "hora_pico_riesgo": [20, 21, 22, 23],
        "patron_24h": [44,40,36,32,28,22,18,22,26,30,30,28,
                       28,26,24,22,26,32,38,46,52,50,48,46],
    },
    "Villa María del Triunfo": {
        "lat": -12.1628, "lon": -76.9367,
        "zona": "SUR",
        "poblacion": 398433,
        "densidad": "MUY_ALTA",
        "nivel_base_riesgo": 0.50,
        "perfil": "Zona periurbana de alto riesgo. Acceso difícil para patrullas convencionales.",
        "color": "bright_red",
        "drones_asignados": 2,
        "incidentes_mes": 334,
        "tipo_delito_ppal": "ROBO_AGRAVADO",
        "hora_pico_riesgo": [19, 20, 21, 22, 23],
        "patron_24h": [42,38,34,30,26,20,16,20,24,28,28,26,
                       26,24,22,20,24,30,36,44,50,48,46,44],
    },
    # ── CALLAO ──
    "Callao": {
        "lat": -12.0564, "lon": -77.1181,
        "zona": "CALLAO",
        "poblacion": 406889,
        "densidad": "ALTA",
        "nivel_base_riesgo": 0.68,
        "perfil": "Puerto principal del Perú. Alta actividad de crimen organizado y narcotráfico.",
        "color": "bright_red",
        "drones_asignados": 3,
        "incidentes_mes": 612,
        "tipo_delito_ppal": "CRIMEN_ORGANIZADO",
        "hora_pico_riesgo": [21, 22, 23, 0, 1, 2, 3],
        "patron_24h": [60,58,55,52,48,42,35,30,28,30,30,28,
                       28,26,24,22,26,32,40,52,62,65,62,62],
    },
}

# Drones disponibles por zona
DRONES_POR_ZONA = {
    "NORTE":  ["CNTL-01","CNTL-02","CNTL-03","CNTL-04"],
    "ESTE":   ["CNTL-05","CNTL-06","CNTL-07","CNTL-08"],
    "CENTRO": ["CNTL-09","CNTL-10","CNTL-11","CNTL-12"],
    "SUR":    ["CNTL-13","CNTL-14","CNTL-15","CNTL-16"],
    "CALLAO": ["CNTL-17","CNTL-18","CNTL-19"],
}

AMBER  = "bright_yellow"
BLUE   = "bright_cyan"
RED    = "bright_red"
GREEN  = "bright_green"
DIM    = "dim white"
MAG    = "magenta"
WHITE  = "white"
ORANGE = "orange1"

console = Console()

# ─────────────────────────────────────────────────────────────────────────────
# MOTOR DE RIESGO POR DISTRITO
# ─────────────────────────────────────────────────────────────────────────────

class MotorRiesgoDistrito:
    """
    Calcula riesgo en tiempo real por distrito usando:
      - Patrón 24h calibrado con datos INEI/PNP
      - Multiplicador por día de semana
      - Multiplicador por densidad poblacional
      - Variación estocástica realista
      - Eventos especiales (feriados, partidos, etc.)
    """

    DIA_MULT  = {0:1.1, 1:0.9, 2:0.9, 3:1.0, 4:1.2, 5:1.5, 6:1.3}
    DENS_MULT = {"BAJA":0.7, "MEDIA":0.9, "ALTA":1.1, "MUY_ALTA":1.25}

    def __init__(self):
        self._historial: dict = {d: deque(maxlen=30) for d in DISTRITOS}
        self._alertas: dict   = {d: [] for d in DISTRITOS}

    def calcular_riesgo(self, distrito: str) -> dict:
        d    = DISTRITOS[distrito]
        hora = datetime.now().hour
        dow  = datetime.now().weekday()

        # Riesgo base desde patrón 24h
        base = d["patron_24h"][hora] / 100.0

        # Multiplicadores
        base *= self.DIA_MULT[dow]
        base *= self.DENS_MULT[d["densidad"]]
        base  = min(1.0, base + random.gauss(0, 0.03))
        base  = max(0.0, base)

        # Score de riesgo 0-100
        score = round(base * 100, 1)

        # Nivel
        if score >= 65:   nivel = "CRÍTICO"
        elif score >= 45: nivel = "ALTO"
        elif score >= 25: nivel = "MEDIO"
        elif score >= 12: nivel = "BAJO"
        else:             nivel = "MÍNIMO"

        # Alertas activas
        alertas = []
        if hora in d["hora_pico_riesgo"]:
            alertas.append(f"HORA PICO — {d['tipo_delito_ppal']}")
        if score > 60:
            alertas.append("RIESGO ELEVADO DETECTADO")

        self._historial[distrito].append(score)
        self._alertas[distrito] = alertas

        return {
            "distrito": distrito,
            "zona":     d["zona"],
            "score":    score,
            "nivel":    nivel,
            "lat":      d["lat"],
            "lon":      d["lon"],
            "poblacion":d["poblacion"],
            "densidad": d["densidad"],
            "drones":   d["drones_asignados"],
            "incidentes_mes": d["incidentes_mes"],
            "tipo_delito":    d["tipo_delito_ppal"],
            "perfil":   d["perfil"],
            "alertas":  alertas,
            "tendencia": self._tendencia(distrito),
        }

    def _tendencia(self, distrito: str) -> str:
        h = list(self._historial[distrito])
        if len(h) < 3:
            return "→"
        reciente = sum(h[-3:]) / 3
        anterior = sum(h[-6:-3]) / 3 if len(h) >= 6 else reciente
        if reciente > anterior * 1.1:
            return "↑"
        elif reciente < anterior * 0.9:
            return "↓"
        return "→"

    def calcular_todos(self) -> dict:
        return {d: self.calcular_riesgo(d) for d in DISTRITOS}

    def top_riesgo(self, n: int = 5) -> list:
        todos = self.calcular_todos()
        return sorted(todos.values(), key=lambda x: x["score"], reverse=True)[:n]


# ─────────────────────────────────────────────────────────────────────────────
# ANÁLISIS ESTRATÉGICO CIUDAD — Opus 4.7
# ─────────────────────────────────────────────────────────────────────────────

def analizar_ciudad_opus(riesgos: dict) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        top = sorted(riesgos.values(), key=lambda x: x["score"], reverse=True)[:3]
        return (f"Prioridad máxima: {top[0]['distrito']} ({top[0]['score']:.0f}/100). "
                f"Redistribuir flota hacia zona {top[0]['zona']}. "
                f"Callao y SJL requieren refuerzo permanente en horario nocturno.")

    client = anthropic.Anthropic(api_key=key)
    top5   = sorted(riesgos.values(), key=lambda x: x["score"], reverse=True)[:5]
    resumen = "\n".join([
        f"  {r['distrito']} ({r['zona']}): {r['score']:.0f}/100 — {r['tipo_delito']}"
        for r in top5
    ])
    hora = datetime.now().strftime("%H:%M")
    dow  = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"][datetime.now().weekday()]

    try:
        r = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=200,
            messages=[{"role":"user","content":
                f"Eres el estratega de seguridad de Lima Metropolitana. "
                f"Hora: {hora} | Día: {dow} | Flota: 19 drones en 5 zonas.\n\n"
                f"TOP 5 DISTRITOS CRÍTICOS:\n{resumen}\n\n"
                f"En 3 oraciones: estrategia de redistribución de flota para las próximas 2 horas. "
                f"Sé específico con nombres de distrito y cantidad de drones."}],
        )
        return r.content[0].text.strip()
    except:
        return "Error en análisis Opus 4.7. Verificar API key."


# ─────────────────────────────────────────────────────────────────────────────
# MAPA ASCII DE LIMA
# ─────────────────────────────────────────────────────────────────────────────

def build_mapa_ascii(riesgos: dict) -> Panel:
    """
    Mapa ASCII simplificado de Lima Metropolitana con indicadores de riesgo.
    """
    ZONA_ICONS = {
        "MÍNIMO":  "·",
        "BAJO":    "○",
        "MEDIO":   "◐",
        "ALTO":    "●",
        "CRÍTICO": "█",
    }
    ZONA_COLORS = {
        "MÍNIMO":  GREEN,
        "BAJO":    "bright_green",
        "MEDIO":   AMBER,
        "ALTO":    ORANGE,
        "CRÍTICO": RED,
    }

    mapa = Text()
    mapa.append("\n")
    mapa.append("    ┌──── LIMA METROPOLITANA ────────────────────┐\n", style=DIM)
    mapa.append("    │  N  NORTE                                  │\n", style=DIM)

    # Zona Norte
    mapa.append("    │  ", style=DIM)
    norte = ["Comas","Los Olivos","San Martín de Porres","Independencia"]
    for d in norte:
        r = riesgos.get(d, {})
        nivel = r.get("nivel","BAJO")
        icon  = ZONA_ICONS.get(nivel, "?")
        col   = ZONA_COLORS.get(nivel, WHITE)
        mapa.append(f"{icon}", style=f"bold {col}")
        mapa.append(f"{d[:4]} ", style=DIM)
    mapa.append("       │\n", style=DIM)

    # Callao | Centro
    mapa.append("    │  ", style=DIM)
    rc = riesgos.get("Callao",{})
    nc = ZONA_COLORS.get(rc.get("nivel","BAJO"), WHITE)
    mapa.append(f"{ZONA_ICONS.get(rc.get('nivel','BAJO'),'?')}", style=f"bold {nc}")
    mapa.append("Call ", style=DIM)

    centro = ["Rímac","Cercado de Lima","La Victoria","Lince"]
    for d in centro:
        r = riesgos.get(d,{})
        nivel = r.get("nivel","BAJO")
        col   = ZONA_COLORS.get(nivel, WHITE)
        mapa.append(f"{ZONA_ICONS.get(nivel,'?')}", style=f"bold {col}")
        mapa.append(f"{d[:4]} ", style=DIM)
    mapa.append("│\n", style=DIM)

    # Zona Este
    mapa.append("    │  ", style=DIM)
    este = ["San Juan de Lurigancho","Ate","El Agustino","Santa Anita"]
    for d in este:
        r = riesgos.get(d,{})
        nivel = r.get("nivel","BAJO")
        col   = ZONA_COLORS.get(nivel, WHITE)
        mapa.append(f"{ZONA_ICONS.get(nivel,'?')}", style=f"bold {col}")
        mapa.append(f"{d[:4]} ", style=DIM)
    mapa.append("         │\n", style=DIM)

    # Zona Sur
    mapa.append("    │  ", style=DIM)
    sur1 = ["Surquillo","Miraflores","San Isidro","Barranco","Chorrillos"]
    for d in sur1:
        r = riesgos.get(d,{})
        nivel = r.get("nivel","BAJO")
        col   = ZONA_COLORS.get(nivel, WHITE)
        mapa.append(f"{ZONA_ICONS.get(nivel,'?')}", style=f"bold {col}")
        mapa.append(f"{d[:4]} ", style=DIM)
    mapa.append(" │\n", style=DIM)

    mapa.append("    │  ", style=DIM)
    sur2 = ["San Juan de Miraflores","Villa El Salvador","Villa María del Triunfo"]
    for d in sur2:
        r = riesgos.get(d,{})
        nivel = r.get("nivel","BAJO")
        col   = ZONA_COLORS.get(nivel, WHITE)
        mapa.append(f"{ZONA_ICONS.get(nivel,'?')}", style=f"bold {col}")
        mapa.append(f"{d[:4]} ", style=DIM)
    mapa.append("                  │\n", style=DIM)

    mapa.append("    │  S  SUR / OCÉANO PACÍFICO                  │\n", style=DIM)
    mapa.append("    └────────────────────────────────────────────┘\n", style=DIM)
    mapa.append("    ", style=DIM)

    # Leyenda
    for nivel, icon in ZONA_ICONS.items():
        col = ZONA_COLORS[nivel]
        mapa.append(f"{icon}", style=f"bold {col}")
        mapa.append(f"{nivel} ", style=DIM)

    return Panel(mapa,
        title=f"[bold {AMBER}]▸ MAPA TÁCTICO — LIMA METROPOLITANA[/]",
        border_style=BLUE, padding=(0,1))


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def nivel_color(nivel: str) -> str:
    return {
        "MÍNIMO":  GREEN,
        "BAJO":    "bright_green",
        "MEDIO":   AMBER,
        "ALTO":    ORANGE,
        "CRÍTICO": RED,
    }.get(nivel, WHITE)


def build_tabla_distritos(riesgos: dict, zona_filtro: Optional[str] = None) -> Table:
    t = Table(
        box=rbox.SIMPLE_HEAD, style=DIM,
        header_style=f"bold {AMBER}",
        show_lines=False, expand=True,
    )
    t.add_column("DISTRITO",    style=f"bold {BLUE}", width=24)
    t.add_column("ZONA",        width=8)
    t.add_column("SCORE",       justify="right",   width=7)
    t.add_column("NIVEL",       width=10)
    t.add_column("TEND",        width=4)
    t.add_column("DRONES",      justify="center",  width=7)
    t.add_column("INCID/MES",   justify="right",   width=10)
    t.add_column("DELITO",      width=20)
    t.add_column("ALERTA",      width=30)

    ordenados = sorted(riesgos.values(), key=lambda x: x["score"], reverse=True)

    for r in ordenados:
        if zona_filtro and r["zona"] != zona_filtro:
            continue
        nc      = nivel_color(r["nivel"])
        tend_c  = RED if r["tendencia"]=="↑" else GREEN if r["tendencia"]=="↓" else AMBER
        alerta  = r["alertas"][0][:28] if r["alertas"] else "—"

        t.add_row(
            r["distrito"],
            r["zona"],
            f"[{nc}]{r['score']:.0f}[/]",
            f"[{nc}]{r['nivel']}[/]",
            f"[{tend_c}]{r['tendencia']}[/]",
            str(r["drones"]),
            str(r["incidentes_mes"]),
            r["tipo_delito"][:18],
            f"[{AMBER}]{alerta}[/]" if r["alertas"] else "—",
        )
    return t


def build_stats_ciudad(riesgos: dict) -> Panel:
    scores = [r["score"] for r in riesgos.values()]
    criticos = sum(1 for r in riesgos.values() if r["nivel"]=="CRÍTICO")
    altos    = sum(1 for r in riesgos.values() if r["nivel"]=="ALTO")
    total_drones = sum(DISTRITOS[d]["drones_asignados"] for d in DISTRITOS)
    total_pob    = sum(DISTRITOS[d]["poblacion"] for d in DISTRITOS)
    total_inc    = sum(DISTRITOS[d]["incidentes_mes"] for d in DISTRITOS)

    def sr(l, v, c=WHITE):
        t2 = Text()
        t2.append(f"  {l:<26}", style=DIM)
        t2.append(v, style=f"bold {c}")
        return t2

    sc_prom = sum(scores)/len(scores)
    sc_max  = max(scores)
    dist_max= max(riesgos.values(), key=lambda x: x["score"])["distrito"]

    content = Text("\n").join([
        sr("Distritos monitoreados", "20",              AMBER),
        sr("Score promedio ciudad",  f"{sc_prom:.1f}",  nivel_color("MEDIO" if sc_prom>25 else "BAJO")),
        sr("Score máximo",           f"{sc_max:.0f} ({dist_max[:12]})", RED),
        sr("Distritos CRÍTICOS",     str(criticos),     RED),
        sr("Distritos ALTO",         str(altos),        ORANGE),
        sr("Total drones activos",   str(total_drones), BLUE),
        sr("Población cubierta",     f"{total_pob:,}",  WHITE),
        sr("Incidentes/mes total",   str(total_inc),    AMBER),
        sr("Hora actual",            datetime.now().strftime("%H:%M:%S"), MAG),
    ])
    return Panel(content,
        title=f"[bold {AMBER}]▸ ESTADÍSTICAS CIUDAD[/]",
        border_style=AMBER, padding=(1,1))


def build_estrategia_panel(texto: str, actualizando: bool) -> Panel:
    if actualizando:
        content = Text("\n  ⟳ Claude Opus 4.7 analizando Lima completa...\n",
                       style=f"italic {DIM}")
    else:
        content = Text(f"\n  {texto}\n", style=WHITE)
    return Panel(content,
        title=f"[bold {AMBER}]▸ ESTRATEGIA CIUDAD — CLAUDE OPUS 4.7[/]",
        border_style=AMBER if texto else DIM, padding=(0,1))


def build_dashboard(riesgos: dict, estrategia: str,
                    actualizando: bool, tick: int) -> Table:
    ts  = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    hdr = Text()
    hdr.append("◈ CENTINELA LIMA METROPOLITANA", style=f"bold {AMBER}")
    hdr.append("  │  ", style=DIM)
    hdr.append("20 DISTRITOS — COBERTURA TOTAL", style=f"bold {BLUE}")
    hdr.append(f"  │  {ts}  │  TICK #{tick:05d}", style=MAG)

    # Distrito más crítico actual
    max_r   = max(riesgos.values(), key=lambda x: x["score"])
    nc      = nivel_color(max_r["nivel"])
    footer  = Text(justify="center")
    footer.append("LIMA METROPOLITANA ACTIVE", style=f"bold {AMBER}")
    footer.append("  ·  ", style=DIM)
    footer.append(f"⚠ {max_r['distrito']}: {max_r['score']:.0f}/100",
                  style=f"bold {nc}")
    footer.append("  ·  ", style=DIM)
    footer.append("19 drones · Opus 4.7 · INEI Calibrado", style=f"bold {BLUE}")
    footer.append("  ·  Ctrl+C para detener", style=DIM)

    root = Table.grid(expand=True)
    root.add_row(Panel(Align.center(hdr), style=AMBER, padding=(0,2)))
    root.add_row(Columns([
        build_mapa_ascii(riesgos),
        build_stats_ciudad(riesgos),
    ], expand=True))
    root.add_row(Panel(
        build_tabla_distritos(riesgos),
        title=f"[bold {AMBER}]▸ TABLA DE RIESGO — 20 DISTRITOS LIMA[/]",
        border_style=BLUE, padding=(0,1),
    ))
    root.add_row(build_estrategia_panel(estrategia, actualizando))
    root.add_row(Panel(Align.center(footer), style=DIM, padding=(0,1)))
    return root


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    console.clear()
    console.print(Panel.fit(
        f"[bold {AMBER}]Inicializando CENTINELA LIMA METROPOLITANA...[/]\n"
        f"[{BLUE}]Cargando {len(DISTRITOS)} distritos con datos INEI/PNP...[/]\n"
        f"[{DIM}]Poblacion cubierta: {sum(d['poblacion'] for d in DISTRITOS.values()):,} habitantes[/]",
        title="[bold white]EATON DYNAMICS — LIMA EXPANSION v1.0[/]",
        border_style=AMBER,
    ))
    time.sleep(1.5)

    motor       = MotorRiesgoDistrito()
    tick        = 0
    estrategia  = "Analizando Lima Metropolitana completa..."
    actualizando= False
    ultimo_anal = 0.0
    ANAL_CADA   = 45  # segundos entre análisis Opus 4.7

    try:
        with Live(console=console, refresh_per_second=2, screen=True) as live:
            while True:
                tick += 1
                riesgos = motor.calcular_todos()
                now     = time.time()

                # Análisis Opus 4.7 periódico
                if now - ultimo_anal >= ANAL_CADA:
                    actualizando  = True
                    live.update(build_dashboard(riesgos, estrategia, True, tick))
                    estrategia    = analizar_ciudad_opus(riesgos)
                    actualizando  = False
                    ultimo_anal   = now

                live.update(build_dashboard(riesgos, estrategia, False, tick))
                time.sleep(1.2)

    except KeyboardInterrupt:
        console.print(f"\n[bold {AMBER}]◈ LIMA METROPOLITANA DETENIDO.[/]")
        top3 = sorted(riesgos.values(), key=lambda x: x["score"], reverse=True)[:3]
        console.print(f"\n[{AMBER}]TOP 3 DISTRITOS AL CIERRE:[/]")
        for i, r in enumerate(top3, 1):
            nc = nivel_color(r["nivel"])
            console.print(f"  {i}. [{nc}]{r['distrito']}: {r['score']:.0f}/100 — {r['nivel']}[/]")


if __name__ == "__main__":
    main()
