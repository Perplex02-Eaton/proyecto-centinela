"""
PROYECTO CENTINELA — PREDICCIÓN ML DE ZONAS DE RIESGO v1.0
Random Forest para predecir probabilidad de incidente por sector y hora
Stack: scikit-learn + pandas + Rich terminal + Opus 4.7 para estrategia

Pipeline:
  1. Generación de historial sintético realista (Lima, patrones reales)
  2. Entrenamiento Random Forest con features temporales y operativas
  3. Predicción: probabilidad de riesgo por sector para próximas 2 horas
  4. Heatmap táctico en terminal
  5. Opus 4.7 genera orden de redistribución preventiva de flota
"""

import os, time, math, random, json
from datetime import datetime, timedelta
from collections import deque
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score
import anthropic

from rich import box as rbox
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich.align import Align
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

SECTORES = [
    "Miraflores", "San Isidro", "Barranco",
    "Surquillo",  "La Victoria", "Lince",
]

# Patrones de riesgo reales de Lima por sector y hora
# (basados en estadísticas de criminalidad urbana Lima)
PATRON_RIESGO = {
    "Miraflores":  [0.05,0.03,0.03,0.02,0.02,0.03,0.05,0.08,0.10,0.12,0.12,0.10,
                    0.10,0.10,0.08,0.08,0.10,0.12,0.15,0.18,0.20,0.18,0.12,0.08],
    "San Isidro":  [0.04,0.02,0.02,0.02,0.02,0.03,0.05,0.10,0.15,0.18,0.18,0.15,
                    0.15,0.15,0.12,0.10,0.12,0.15,0.18,0.20,0.18,0.15,0.10,0.06],
    "Barranco":    [0.30,0.35,0.40,0.38,0.25,0.10,0.05,0.04,0.04,0.05,0.06,0.06,
                    0.06,0.06,0.05,0.05,0.06,0.08,0.12,0.18,0.22,0.28,0.32,0.35],
    "Surquillo":   [0.25,0.20,0.18,0.15,0.10,0.08,0.06,0.08,0.10,0.12,0.12,0.10,
                    0.10,0.10,0.10,0.10,0.12,0.15,0.18,0.20,0.22,0.25,0.28,0.28],
    "La Victoria": [0.20,0.15,0.12,0.10,0.08,0.08,0.10,0.15,0.18,0.20,0.22,0.20,
                    0.18,0.18,0.15,0.15,0.18,0.20,0.25,0.28,0.28,0.25,0.22,0.20],
    "Lince":       [0.15,0.12,0.10,0.08,0.06,0.05,0.05,0.06,0.08,0.10,0.10,0.08,
                    0.08,0.08,0.06,0.06,0.08,0.10,0.12,0.15,0.18,0.20,0.18,0.15],
}

# Multiplicadores por día de semana
DIA_MULT = {0:1.1, 1:0.9, 2:0.9, 3:1.0, 4:1.2, 5:1.4, 6:1.3}  # Lun-Dom

AMBER = "bright_yellow"
BLUE  = "bright_cyan"
RED   = "bright_red"
GREEN = "bright_green"
DIM   = "dim white"
MAG   = "magenta"
WHITE = "white"

console = Console()


# ─────────────────────────────────────────────────────────────────────────────
# A. GENERADOR DE HISTORIAL SINTÉTICO REALISTA
# ─────────────────────────────────────────────────────────────────────────────

def generar_historial(dias: int = 90) -> pd.DataFrame:
    """
    Genera historial sintético de 90 días de incidentes en Lima.
    Basado en patrones reales de criminalidad urbana.

    Features:
      - hora_dia (0-23)
      - dia_semana (0=Lun, 6=Dom)
      - sector (categórico)
      - mes (1-12)
      - es_feriado (bool)
      - lluvia (bool, Lima tiene época de garúa)
      - alertas_previas_3h (conteo)
      - drones_en_sector (1-2)
      - incidente (target: 0=No, 1=Sí)
    """
    registros = []
    fecha_base = datetime.now() - timedelta(days=dias)

    feriados_peru = {1, 44, 64, 120, 148, 189, 209, 228, 269, 296, 325, 359}

    for dia in range(dias):
        fecha   = fecha_base + timedelta(days=dia)
        dow     = fecha.weekday()
        mes     = fecha.month
        dia_ano = fecha.timetuple().tm_yday
        feriado = dia_ano in feriados_peru

        for sector in SECTORES:
            for hora in range(24):
                prob_base = PATRON_RIESGO[sector][hora]
                prob_base *= DIA_MULT[dow]
                if feriado:
                    prob_base *= 1.3
                if mes in (1, 12):  # Verano Lima
                    prob_base *= 1.1
                if hora in (22, 23, 0, 1, 2):
                    prob_base *= 1.2

                # Alertas previas (ventana 3 horas)
                alertas_prev = sum([
                    1 for _ in range(3)
                    if random.random() < prob_base * 0.5
                ])
                # Drones en sector
                drones = random.choice([1, 1, 2])
                if drones == 2:
                    prob_base *= 0.7  # más drones = menos incidentes

                # Ruido gaussiano
                prob_final = max(0.0, min(1.0, prob_base + random.gauss(0, 0.03)))

                # Generar múltiples observaciones por hora (cada 15 min)
                for _ in range(4):
                    incidente = 1 if random.random() < prob_final else 0
                    registros.append({
                        "hora_dia":         hora,
                        "dia_semana":       dow,
                        "sector":           sector,
                        "mes":              mes,
                        "es_feriado":       int(feriado),
                        "alertas_previas":  alertas_prev,
                        "drones_en_sector": drones,
                        "prob_teorica":     round(prob_final, 3),
                        "incidente":        incidente,
                    })

    df = pd.DataFrame(registros)
    return df


# ─────────────────────────────────────────────────────────────────────────────
# B. MODELO RANDOM FOREST + GRADIENT BOOSTING
# ─────────────────────────────────────────────────────────────────────────────

class CentinelaML:
    """
    Modelo de predicción de riesgo táctico.

    Algoritmo: Random Forest (ensemble de árboles de decisión)
    Features: temporales + operativas + históricas
    Target: P(incidente | features) ∈ [0, 1]

    Métricas de evaluación:
      - Accuracy: exactitud global
      - Precision: de las alertas emitidas, cuántas eran reales
      - Recall: de los incidentes reales, cuántos detectamos
      - F1: balance precision-recall
    """

    def __init__(self):
        self.rf_model  = RandomForestClassifier(
            n_estimators=150,
            max_depth=10,
            min_samples_split=5,
            min_samples_leaf=2,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1,
        )
        self.gb_model  = GradientBoostingClassifier(
            n_estimators=100,
            learning_rate=0.1,
            max_depth=5,
            random_state=42,
        )
        self.le_sector = LabelEncoder()
        self.entrenado = False
        self.metricas  = {}
        self.importancias = {}

    def preparar_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Encoding y feature engineering."""
        d = df.copy()
        d["sector_enc"] = self.le_sector.transform(d["sector"])

        # Features trigonométricas para hora (captura ciclicidad)
        d["hora_sin"] = np.sin(2 * np.pi * d["hora_dia"] / 24)
        d["hora_cos"] = np.cos(2 * np.pi * d["hora_dia"] / 24)
        d["dia_sin"]  = np.sin(2 * np.pi * d["dia_semana"] / 7)
        d["dia_cos"]  = np.cos(2 * np.pi * d["dia_semana"] / 7)

        # Interacción hora × sector
        d["hora_sector"] = d["hora_dia"] * d["sector_enc"]

        # Es noche (10pm - 4am)
        d["es_noche"] = ((d["hora_dia"] >= 22) | (d["hora_dia"] <= 4)).astype(int)

        # Es hora pico (7-9am, 6-8pm)
        d["es_pico"] = (
            ((d["hora_dia"] >= 7) & (d["hora_dia"] <= 9)) |
            ((d["hora_dia"] >= 18) & (d["hora_dia"] <= 20))
        ).astype(int)

        return d

    def columnas_feature(self) -> list:
        return [
            "hora_dia", "dia_semana", "sector_enc", "mes",
            "es_feriado", "alertas_previas", "drones_en_sector",
            "hora_sin", "hora_cos", "dia_sin", "dia_cos",
            "hora_sector", "es_noche", "es_pico",
        ]

    def entrenar(self, df: pd.DataFrame) -> dict:
        """Entrena ambos modelos y retorna métricas."""
        # Encoding
        self.le_sector.fit(df["sector"])
        df_prep = self.preparar_features(df)

        X = df_prep[self.columnas_feature()].values
        y = df_prep["incidente"].values

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y)

        # Entrenar Random Forest
        self.rf_model.fit(X_train, y_train)
        y_pred_rf = self.rf_model.predict(X_test)

        # Entrenar Gradient Boosting
        self.gb_model.fit(X_train, y_train)
        y_pred_gb = self.gb_model.predict(X_test)

        # Ensemble: promedio de probabilidades
        prob_rf = self.rf_model.predict_proba(X_test)[:, 1]
        prob_gb = self.gb_model.predict_proba(X_test)[:, 1]
        prob_ens = (prob_rf + prob_gb) / 2
        y_pred_ens = (prob_ens >= 0.5).astype(int)

        acc_rf  = accuracy_score(y_test, y_pred_rf)
        acc_gb  = accuracy_score(y_test, y_pred_gb)
        acc_ens = accuracy_score(y_test, y_pred_ens)

        # Feature importances
        cols = self.columnas_feature()
        for feat, imp in zip(cols, self.rf_model.feature_importances_):
            self.importancias[feat] = round(float(imp), 4)

        self.entrenado = True
        self.metricas  = {
            "accuracy_rf":       round(acc_rf, 4),
            "accuracy_gb":       round(acc_gb, 4),
            "accuracy_ensemble": round(acc_ens, 4),
            "muestras_train":    len(X_train),
            "muestras_test":     len(X_test),
            "features":          len(cols),
            "sectores":          list(self.le_sector.classes_),
        }
        return self.metricas

    def predecir_sector_hora(self, sector: str, hora: int,
                              dia_semana: int = None,
                              alertas_previas: int = 0,
                              drones: int = 1) -> float:
        """
        Predice probabilidad de incidente para sector y hora dados.
        Retorna float ∈ [0, 1].
        """
        if not self.entrenado:
            raise RuntimeError("Modelo no entrenado")

        if dia_semana is None:
            dia_semana = datetime.now().weekday()

        mes = datetime.now().month

        row = pd.DataFrame([{
            "hora_dia":         hora,
            "dia_semana":       dia_semana,
            "sector":           sector,
            "mes":              mes,
            "es_feriado":       0,
            "alertas_previas":  alertas_previas,
            "drones_en_sector": drones,
        }])
        row["sector_enc"] = self.le_sector.transform(row["sector"])
        row_prep = self.preparar_features(row)
        X = row_prep[self.columnas_feature()].values

        prob_rf = self.rf_model.predict_proba(X)[0, 1]
        prob_gb = self.gb_model.predict_proba(X)[0, 1]
        return float((prob_rf + prob_gb) / 2)

    def predecir_flota_2h(self) -> dict:
        """
        Predice riesgo para todos los sectores en las próximas 2 horas.
        Retorna dict: {sector: [prob_h0, prob_h1, prob_h2]}
        """
        ahora    = datetime.now()
        dow      = ahora.weekday()
        resultado = {}

        for sector in SECTORES:
            probs = []
            for delta_h in range(3):
                hora_target = (ahora.hour + delta_h) % 24
                p = self.predecir_sector_hora(
                    sector, hora_target, dow,
                    alertas_previas=random.randint(0, 2),
                    drones=1,
                )
                probs.append(round(p, 4))
            resultado[sector] = probs

        return resultado


# ─────────────────────────────────────────────────────────────────────────────
# C. ANÁLISIS ESTRATÉGICO — Opus 4.7
# ─────────────────────────────────────────────────────────────────────────────

def generar_estrategia_opus(predicciones: dict, metricas: dict) -> str:
    """Opus 4.7 analiza las predicciones y genera orden de redistribución."""
    if not ANTHROPIC_KEY:
        sector_max = max(predicciones, key=lambda s: max(predicciones[s]))
        return (f"Redistribuir un drone adicional a {sector_max} "
                f"(mayor riesgo predicho). Mantener cobertura en otros sectores.")

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    ahora  = datetime.now()

    pred_str = "\n".join([
        f"  {s}: H+0={p[0]:.1%} H+1={p[1]:.1%} H+2={p[2]:.1%}"
        for s, p in predicciones.items()
    ])

    prompt = f"""Eres el Coordinador Estratégico CENTINELA de Lima, Perú.
Tienes predicciones ML de riesgo para los próximos 3 intervalos de hora.
Modelo: Random Forest + Gradient Boosting | Accuracy: {metricas['accuracy_ensemble']:.1%}
Hora actual: {ahora.strftime('%H:%M')} | Día: {ahora.strftime('%A')}
Flota disponible: 6 drones para 6 sectores

PREDICCIONES DE RIESGO (H+0=ahora, H+1=próxima hora, H+2=en 2 horas):
{pred_str}

Genera en 3-4 oraciones:
1. Los 2 sectores de mayor prioridad y por qué
2. Redistribución táctica recomendada de la flota
3. Ventana temporal crítica a vigilar

Responde en español, tono ejecutivo, sin markdown."""

    try:
        r = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=300,
            messages=[{"role":"user","content":prompt}]
        )
        return r.content[0].text.strip()
    except Exception as e:
        return f"Error en análisis Opus 4.7: {str(e)[:80]}"


# ─────────────────────────────────────────────────────────────────────────────
# D. VISUALIZACIÓN — Heatmap y Dashboard Rich
# ─────────────────────────────────────────────────────────────────────────────

def prob_a_color(p: float) -> str:
    if p >= 0.60: return RED
    if p >= 0.40: return "bright_red"
    if p >= 0.25: return AMBER
    if p >= 0.15: return "bright_yellow"
    return GREEN

def prob_a_barra(p: float, width: int = 20) -> str:
    lleno = int(p * width)
    vacio = width - lleno
    return "█" * lleno + "░" * vacio

def nivel_texto(p: float) -> str:
    if p >= 0.60: return "CRÍTICO"
    if p >= 0.40: return "ALTO"
    if p >= 0.25: return "MEDIO"
    if p >= 0.15: return "BAJO"
    return "MÍNIMO"

def build_heatmap_table(predicciones: dict) -> Table:
    """Tabla heatmap de predicciones por sector y horizonte temporal."""
    ahora = datetime.now()
    h0 = ahora.strftime("%H:%M")
    h1 = (ahora + timedelta(hours=1)).strftime("%H:%M")
    h2 = (ahora + timedelta(hours=2)).strftime("%H:%M")

    t = Table(
        title=f"[bold {AMBER}]▸ HEATMAP TÁCTICO — PREDICCIÓN DE RIESGO ML[/]",
        box=rbox.SIMPLE_HEAD, style=DIM,
        header_style=f"bold {AMBER}",
        show_lines=True, expand=True,
    )
    t.add_column("SECTOR",       style=f"bold {BLUE}", width=14)
    t.add_column(f"AHORA {h0}",  justify="center",    width=14)
    t.add_column(f"H+1  {h1}",   justify="center",    width=14)
    t.add_column(f"H+2  {h2}",   justify="center",    width=14)
    t.add_column("TENDENCIA",     width=22)
    t.add_column("NIVEL",         width=10)
    t.add_column("PRIORIDAD",     justify="center",    width=10)

    # Ordenar por riesgo máximo descendente
    sectores_sorted = sorted(
        SECTORES,
        key=lambda s: max(predicciones.get(s, [0,0,0])),
        reverse=True,
    )

    for rank, sector in enumerate(sectores_sorted, 1):
        probs = predicciones.get(sector, [0.0, 0.0, 0.0])
        p0, p1, p2 = probs

        # Tendencia
        if p2 > p0 * 1.15:
            tendencia = f"[{RED}]↑ ESCALANDO[/]"
        elif p2 < p0 * 0.85:
            tendencia = f"[{GREEN}]↓ BAJANDO[/]"
        else:
            tendencia = f"[{AMBER}]→ ESTABLE[/]"

        max_p  = max(probs)
        nc     = prob_a_color(max_p)
        nivel  = nivel_texto(max_p)
        barra  = prob_a_barra(max_p, 12)

        prioridad_icon = {1:"🔴 #1", 2:"🟠 #2", 3:"🟡 #3"}.get(rank, f"⚪ #{rank}")

        t.add_row(
            sector,
            f"[{prob_a_color(p0)}]{p0:.1%}[/]",
            f"[{prob_a_color(p1)}]{p1:.1%}[/]",
            f"[{prob_a_color(p2)}]{p2:.1%}[/]",
            f"[{nc}]{barra}[/]",
            f"[{nc}]{nivel}[/]",
            prioridad_icon if rank <= 3 else f"#{rank}",
        )

    return t


def build_importancias_panel(importancias: dict) -> Panel:
    """Muestra importancia de features del modelo."""
    sorted_feats = sorted(importancias.items(), key=lambda x: x[1], reverse=True)[:8]

    t = Table(box=None, show_header=False, expand=True)
    t.add_column("FEATURE", style=DIM, width=20)
    t.add_column("IMPORTANCIA", width=24)
    t.add_column("VALOR", justify="right", style=WHITE, width=7)

    for feat, imp in sorted_feats:
        barra = "█" * int(imp * 80) + "░" * (16 - int(imp * 80))
        barra = barra[:16]
        color = AMBER if imp > 0.10 else BLUE if imp > 0.05 else DIM
        t.add_row(
            feat,
            f"[{color}]{barra}[/]",
            f"{imp:.3f}",
        )

    return Panel(t,
        title=f"[bold {AMBER}]▸ IMPORTANCIA DE FEATURES — RANDOM FOREST[/]",
        border_style=BLUE, padding=(0,1))


def build_metricas_panel(metricas: dict, actualizando: bool) -> Panel:
    def mr(l, v, c=WHITE):
        t2 = Text()
        t2.append(f"  {l:<24}", style=DIM)
        t2.append(v, style=f"bold {c}")
        return t2

    acc = metricas.get("accuracy_ensemble", 0)
    acc_color = GREEN if acc > 0.80 else AMBER if acc > 0.65 else RED

    content = Text("\n").join([
        mr("Accuracy Ensemble",  f"{acc:.1%}",                    acc_color),
        mr("Accuracy RF",        f"{metricas.get('accuracy_rf',0):.1%}", BLUE),
        mr("Accuracy GB",        f"{metricas.get('accuracy_gb',0):.1%}", BLUE),
        mr("Muestras entren.",   str(metricas.get("muestras_train",0)),  WHITE),
        mr("Muestras test",      str(metricas.get("muestras_test",0)),   WHITE),
        mr("Features usadas",    str(metricas.get("features",0)),        AMBER),
        mr("Algoritmo",          "RF + GB Ensemble",                     BLUE),
    ])

    if actualizando:
        content.append(f"\n\n  ⟳ Opus 4.7 analizando...", style=f"italic {DIM}")

    return Panel(content,
        title=f"[bold {AMBER}]▸ MÉTRICAS DEL MODELO ML[/]",
        border_style=AMBER, padding=(1,1))


def build_estrategia_panel(estrategia: str, actualizando: bool) -> Panel:
    if actualizando:
        content = Text("\n  ⟳ Claude Opus 4.7 generando estrategia...",
                       style=f"italic {DIM}")
    elif estrategia:
        content = Text()
        content.append(f"\n  {estrategia}", style=WHITE)
    else:
        content = Text("\n  Esperando análisis...", style=DIM)

    return Panel(content,
        title=f"[bold {AMBER}]▸ ESTRATEGIA DE REDISTRIBUCIÓN — CLAUDE OPUS 4.7[/]",
        border_style=AMBER if estrategia else DIM,
        padding=(0,1))


def build_dashboard(predicciones, metricas, importancias,
                    estrategia, tick, actualizando, historial_pred):
    ts  = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    hdr = Text()
    hdr.append("◈ CENTINELA ML", style=f"bold {AMBER}")
    hdr.append("  │  ", style=DIM)
    hdr.append("PREDICCIÓN DE ZONAS DE RIESGO", style=f"bold {BLUE}")
    hdr.append("  │  ", style=DIM)
    hdr.append(ts, style=WHITE)
    hdr.append(f"  │  CICLO #{tick:04d}", style=MAG)

    # Sector de mayor riesgo actual
    if predicciones:
        sector_max = max(predicciones, key=lambda s: predicciones[s][0])
        riesgo_max = predicciones[sector_max][0]
        nc = prob_a_color(riesgo_max)
        footer_txt = Text(justify="center")
        footer_txt.append("CENTINELA-ML ACTIVE", style=f"bold {AMBER}")
        footer_txt.append("  ·  ", style=DIM)
        footer_txt.append(f"MÁXIMO RIESGO: {sector_max} ({riesgo_max:.1%})",
                           style=f"bold {nc}")
        footer_txt.append("  ·  ", style=DIM)
        footer_txt.append("RF + GB Ensemble + Opus 4.7", style=f"bold {BLUE}")
        footer_txt.append("  ·  Ctrl+C para detener", style=DIM)
    else:
        footer_txt = Text("CENTINELA-ML — Entrenando modelo...", style=DIM,
                          justify="center")

    root = Table.grid(expand=True)
    root.add_row(Panel(Align.center(hdr), style=AMBER, padding=(0,2)))

    if predicciones:
        root.add_row(build_heatmap_table(predicciones))
        root.add_row(Columns([
            build_estrategia_panel(estrategia, actualizando),
            build_metricas_panel(metricas, False),
        ], expand=True))
        if importancias:
            root.add_row(build_importancias_panel(importancias))
    else:
        root.add_row(Panel(
            Align.center(Text("\n  Entrenando modelo Random Forest...\n", style=f"bold {AMBER}")),
            border_style=AMBER, padding=(2,2),
        ))

    root.add_row(Panel(Align.center(footer_txt), style=DIM, padding=(0,1)))
    return root


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    console.clear()
    console.print(Panel.fit(
        f"[bold {AMBER}]Inicializando CENTINELA ML...[/]\n"
        f"[{BLUE}]Random Forest + Gradient Boosting[/]\n"
        f"[{DIM}]Generando historial sintético de Lima (90 días)...[/]",
        title="[bold white]EATON DYNAMICS — PREDICCIÓN ML[/]",
        border_style=AMBER,
    ))

    # ── Fase 1: Generar historial ──
    with Progress(
        SpinnerColumn(),
        TextColumn("[bright_yellow]{task.description}"),
        BarColumn(bar_width=40),
        console=console,
    ) as progress:
        t1 = progress.add_task("Generando historial (90 días × 6 sectores × 24h)...", total=100)
        df = generar_historial(dias=90)
        progress.update(t1, completed=100)
        console.print(f"  [{GREEN}]✓ {len(df):,} registros generados[/]")

        t2 = progress.add_task("Entrenando Random Forest + Gradient Boosting...", total=100)
        modelo = CentinelaML()
        metricas = modelo.entrenar(df)
        progress.update(t2, completed=100)
        console.print(
            f"  [{GREEN}]✓ Accuracy Ensemble: {metricas['accuracy_ensemble']:.1%}[/]"
        )

        t3 = progress.add_task("Generando predicciones para próximas 2 horas...", total=100)
        predicciones = modelo.predecir_flota_2h()
        progress.update(t3, completed=100)

    console.print(f"\n[{AMBER}]Consultando Claude Opus 4.7 para estrategia...[/]")
    estrategia = generar_estrategia_opus(predicciones, metricas)
    console.print(f"[{GREEN}]✓ Estrategia generada[/]")
    time.sleep(1.0)

    # ── Fase 2: Dashboard en vivo ──
    tick          = 0
    actualizando  = False
    ultimo_update = time.time()
    UPDATE_CADA   = 30  # segundos entre re-predicciones

    historial_pred: deque = deque(maxlen=10)

    try:
        with Live(console=console, refresh_per_second=2, screen=True) as live:
            while True:
                tick += 1
                now  = time.time()

                # Re-predecir cada UPDATE_CADA segundos
                if now - ultimo_update >= UPDATE_CADA:
                    actualizando  = True
                    live.update(build_dashboard(
                        predicciones, metricas, modelo.importancias,
                        estrategia, tick, True, historial_pred))

                    predicciones  = modelo.predecir_flota_2h()
                    estrategia    = generar_estrategia_opus(predicciones, metricas)
                    ultimo_update = now
                    actualizando  = False

                    # Guardar en historial
                    historial_pred.appendleft({
                        "hora":        datetime.now().strftime("%H:%M:%S"),
                        "pred":        predicciones,
                        "estrategia":  estrategia[:80],
                    })

                live.update(build_dashboard(
                    predicciones, metricas, modelo.importancias,
                    estrategia, tick, actualizando, historial_pred))

                time.sleep(1.0)

    except KeyboardInterrupt:
        console.print(f"\n[bold {AMBER}]◈ CENTINELA ML DETENIDO.[/]")
        console.print(f"\n[{BLUE}]RESUMEN FINAL:[/]")
        console.print(f"  Accuracy del modelo: {metricas['accuracy_ensemble']:.1%}")
        console.print(f"  Muestras de entrenamiento: {metricas['muestras_train']:,}")

        # Mostrar top 3 sectores de mayor riesgo al cierre
        console.print(f"\n[{AMBER}]TOP 3 SECTORES DE MAYOR RIESGO ACTUAL:[/]")
        sorted_sec = sorted(predicciones.items(),
                            key=lambda x: x[1][0], reverse=True)[:3]
        for i, (sec, probs) in enumerate(sorted_sec, 1):
            nc = prob_a_color(probs[0])
            console.print(f"  {i}. [{nc}]{sec}: {probs[0]:.1%}[/]")


if __name__ == "__main__":
    main()
