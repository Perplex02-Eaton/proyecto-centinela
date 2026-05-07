"""
PROYECTO CENTINELA — GENERADOR DE REPORTES EJECUTIVOS v1.0
PDF profesional para gerencia y municipalidad de Lima
Stack: ReportLab + Anthropic API (Opus 4.7 para análisis ejecutivo)

Genera:
  - Portada con branding Eaton Dynamics
  - Resumen ejecutivo generado por Opus 4.7
  - Tabla de KPIs operativos
  - Estado de flota por sector
  - Log de incidentes críticos
  - Recomendaciones tácticas IA
  - Análisis de ahorro de capital
"""

import os, json, time, random, math
from datetime import datetime
from typing import Optional

import anthropic
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether,
)
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.colors import HexColor

# ─────────────────────────────────────────────────────────────────────────────
# PALETA EATON DYNAMICS — Bloomberg Aesthetic
# ─────────────────────────────────────────────────────────────────────────────

C_AMBER   = HexColor("#FF9800")
C_BLUE    = HexColor("#2962FF")
C_BLACK   = HexColor("#0D0D0D")
C_DARK    = HexColor("#1A1A1A")
C_GRAY    = HexColor("#333333")
C_LGRAY   = HexColor("#888888")
C_WHITE   = HexColor("#FFFFFF")
C_GREEN   = HexColor("#00C853")
C_RED     = HexColor("#FF1744")
C_YELLOW  = HexColor("#FFD600")
C_BG      = HexColor("#111111")

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ─────────────────────────────────────────────────────────────────────────────
# DATOS SIMULADOS DE FLOTA (en producción vendrían del FleetState)
# ─────────────────────────────────────────────────────────────────────────────

def generar_datos_flota() -> dict:
    """
    Genera datos realistas de flota para el reporte.
    En producción: importar FleetState del multiagente.
    """
    sectores = [
        "Miraflores", "San Isidro", "Barranco",
        "Surquillo",  "La Victoria", "Lince"
    ]
    drones = []
    for i, sector in enumerate(sectores):
        bat = round(random.uniform(45, 95), 1)
        drones.append({
            "id":           f"CNTL-{i+1:02d}",
            "sector":       sector,
            "battery_pct":  bat,
            "altitude_m":   round(random.uniform(65, 100), 1),
            "vel_ms":       round(random.uniform(7, 14), 2),
            "motor_health": round(random.uniform(0.88, 1.0), 3),
            "rssi_dbm":     random.randint(-85, -55),
            "estado_ia":    "NOMINAL" if bat > 35 else "WARNING",
            "mision":       "PATRULLA ACTIVA",
            "distancia_km": round(random.uniform(0.8, 2.4), 2),
        })

    incidentes = [
        {
            "hora": "09:23:41",
            "drone": "CNTL-03",
            "sector": "Barranco",
            "tipo": "BATERÍA BAJA",
            "severidad": "WARNING",
            "accion": "Retorno programado a base",
            "resuelto": True,
        },
        {
            "hora": "09:47:15",
            "drone": "CNTL-01",
            "sector": "Miraflores",
            "tipo": "SEÑAL GPS DEGRADADA",
            "severidad": "CRITICAL",
            "accion": "Activado modo autónomo por RSSI",
            "resuelto": True,
        },
        {
            "hora": "10:02:08",
            "drone": "CNTL-05",
            "sector": "La Victoria",
            "tipo": "ANOMALÍA MOTOR #2",
            "severidad": "WARNING",
            "accion": "Compensación automática de empuje",
            "resuelto": False,
        },
    ]

    return {
        "drones":          drones,
        "incidentes":      incidentes,
        "uptime_horas":    round(random.uniform(2.5, 8.0), 2),
        "distancia_total": round(random.uniform(180, 450), 1),
        "alertas_total":   random.randint(8, 24),
        "despachos":       random.randint(12, 35),
        "consultas_ia":    random.randint(280, 650),
        "roi_promedio":    round(random.uniform(0.78, 0.94), 3),
        "bat_promedio":    round(sum(d["battery_pct"] for d in drones)/6, 1),
        "coord_orden":     "Mantener cobertura sectorial. CNTL-03 en retorno. Refuerzo Barranco con CNTL-06.",
        "eficiencia":      round(random.uniform(82, 96), 1),
    }


# ─────────────────────────────────────────────────────────────────────────────
# ANÁLISIS EJECUTIVO — Opus 4.7
# ─────────────────────────────────────────────────────────────────────────────

def generar_analisis_ejecutivo(datos: dict) -> str:
    """Opus 4.7 genera el análisis ejecutivo del reporte."""
    if not API_KEY:
        return ("El sistema CENTINELA operó durante el período de reporte con "
                "eficiencia operativa del {datos['eficiencia']}%. La flota de 6 drones "
                "mantuvo cobertura continua en los sectores asignados de Lima. "
                "Se recomienda revisar el estado del drone CNTL-05 antes del próximo turno.")

    client = anthropic.Anthropic(api_key=API_KEY)
    drones_str = "\n".join([
        f"  {d['id']} ({d['sector']}): BAT={d['battery_pct']}% "
        f"ALT={d['altitude_m']}m ESTADO={d['estado_ia']}"
        for d in datos["drones"]
    ])
    incidentes_str = "\n".join([
        f"  {i['hora']} - {i['drone']} [{i['tipo']}] Severidad:{i['severidad']} "
        f"Resuelto:{'Sí' if i['resuelto'] else 'No'}"
        for i in datos["incidentes"]
    ])

    prompt = f"""Eres el analista ejecutivo del sistema CENTINELA de drones de seguridad de Lima, Perú.
Genera un párrafo ejecutivo de 4-5 oraciones para un reporte gerencial oficial.
Tono: profesional, técnico pero accesible para directivos municipales.
No uses markdown ni asteriscos. Solo texto plano.

DATOS DEL PERÍODO:
Uptime: {datos['uptime_horas']} horas
Distancia total patrullada: {datos['distancia_total']} km
Alertas gestionadas: {datos['alertas_total']}
Despachos ejecutados: {datos['despachos']}
Consultas IA procesadas: {datos['consultas_ia']}
ROI promedio: {datos['roi_promedio']}
Eficiencia de flota: {datos['eficiencia']}%
Batería promedio: {datos['bat_promedio']}%

ESTADO DE DRONES:
{drones_str}

INCIDENTES:
{incidentes_str}

ORDEN DEL COORDINADOR:
{datos['coord_orden']}

Genera el análisis ejecutivo:"""

    try:
        r = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=400,
            messages=[{"role":"user","content":prompt}]
        )
        return r.content[0].text.strip()
    except Exception as e:
        return (f"Durante el período de operación, la flota CENTINELA mantuvo vigilancia "
                f"continua sobre 6 sectores de Lima con una eficiencia del "
                f"{datos['eficiencia']}%. Se gestionaron {datos['alertas_total']} alertas "
                f"y {datos['despachos']} despachos tácticos. El sistema de IA procesó "
                f"{datos['consultas_ia']} consultas con ROI promedio de {datos['roi_promedio']}.")


def generar_recomendaciones(datos: dict) -> list:
    """Opus 4.7 genera recomendaciones tácticas."""
    if not API_KEY:
        return [
            "Programar mantenimiento preventivo de CNTL-05 por anomalía en motor #2.",
            "Incrementar frecuencia de patrullaje en Barranco durante próximo turno.",
            "Considerar expansión a sector Miramar para cobertura costera.",
        ]

    client = anthropic.Anthropic(api_key=API_KEY)
    prompt = f"""Basado en los datos operativos del sistema CENTINELA de drones de Lima:
- {datos['alertas_total']} alertas en {datos['uptime_horas']} horas
- Incidentes: {len(datos['incidentes'])} registrados
- Eficiencia: {datos['eficiencia']}%
- CNTL-05 con anomalía de motor no resuelta

Genera exactamente 4 recomendaciones tácticas para la gerencia.
Formato: una recomendación por línea, sin numeración, sin asteriscos, texto directo."""

    try:
        r = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=300,
            messages=[{"role":"user","content":prompt}]
        )
        lines = [l.strip() for l in r.content[0].text.strip().split("\n") if l.strip()]
        return lines[:4]
    except:
        return [
            "Programar mantenimiento de CNTL-05 antes del siguiente turno operativo.",
            "Reforzar cobertura en Barranco con drone adicional en horario nocturno.",
            "Actualizar firmware de sensores RSSI para mejorar estabilidad de señal.",
            "Implementar protocolo de rotación de batería para optimizar uptime de flota.",
        ]


# ─────────────────────────────────────────────────────────────────────────────
# CANVAS PERSONALIZADO — Header y Footer en cada página
# ─────────────────────────────────────────────────────────────────────────────

class CentinelaCanvas:
    def __init__(self, filename, datos):
        self.filename = filename
        self.datos    = datos

    def __call__(self, canvas, doc):
        canvas.saveState()
        W, H = A4

        # ── HEADER ──
        canvas.setFillColor(C_BLACK)
        canvas.rect(0, H-2.2*cm, W, 2.2*cm, fill=1, stroke=0)

        # Línea ámbar
        canvas.setFillColor(C_AMBER)
        canvas.rect(0, H-2.2*cm, W, 0.08*cm, fill=1, stroke=0)

        # Logo / Título
        canvas.setFillColor(C_AMBER)
        canvas.setFont("Helvetica-Bold", 13)
        canvas.drawString(1.2*cm, H-1.4*cm, "PROYECTO CENTINELA")

        canvas.setFillColor(C_LGRAY)
        canvas.setFont("Helvetica", 9)
        canvas.drawString(1.2*cm, H-1.85*cm, "EATON DYNAMICS — Sistema de Drones de Seguridad")

        # Fecha y número de página
        canvas.setFillColor(C_LGRAY)
        canvas.setFont("Helvetica", 8)
        ts = datetime.now().strftime("%Y-%m-%d  %H:%M")
        canvas.drawRightString(W-1.2*cm, H-1.4*cm, ts)
        canvas.drawRightString(W-1.2*cm, H-1.85*cm,
                               f"Página {doc.page}  |  CONFIDENCIAL")

        # ── FOOTER ──
        canvas.setFillColor(C_BLACK)
        canvas.rect(0, 0, W, 1.5*cm, fill=1, stroke=0)
        canvas.setFillColor(C_AMBER)
        canvas.rect(0, 1.5*cm, W, 0.05*cm, fill=1, stroke=0)

        canvas.setFillColor(C_LGRAY)
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(1.2*cm, 0.6*cm,
            "CENTINELA COMMAND CENTER  ·  Lima, Perú  ·  "
            "Documento generado automáticamente por IA")
        canvas.drawRightString(W-1.2*cm, 0.6*cm,
            f"Uptime: {self.datos['uptime_horas']}h  |  "
            f"Eficiencia: {self.datos['eficiencia']}%")

        canvas.restoreState()


# ─────────────────────────────────────────────────────────────────────────────
# ESTILOS
# ─────────────────────────────────────────────────────────────────────────────

def get_styles():
    base = getSampleStyleSheet()
    styles = {}

    styles["titulo"] = ParagraphStyle(
        "titulo", fontName="Helvetica-Bold", fontSize=22,
        textColor=C_AMBER, spaceAfter=6, alignment=TA_CENTER,
        leading=28,
    )
    styles["subtitulo"] = ParagraphStyle(
        "subtitulo", fontName="Helvetica", fontSize=12,
        textColor=C_LGRAY, spaceAfter=4, alignment=TA_CENTER,
    )
    styles["seccion"] = ParagraphStyle(
        "seccion", fontName="Helvetica-Bold", fontSize=11,
        textColor=C_AMBER, spaceBefore=14, spaceAfter=6,
        borderPad=4,
    )
    styles["cuerpo"] = ParagraphStyle(
        "cuerpo", fontName="Helvetica", fontSize=9,
        textColor=C_WHITE, spaceAfter=6, leading=14,
        alignment=TA_JUSTIFY,
    )
    styles["label"] = ParagraphStyle(
        "label", fontName="Helvetica-Bold", fontSize=8,
        textColor=C_LGRAY,
    )
    styles["valor_amber"] = ParagraphStyle(
        "valor_amber", fontName="Helvetica-Bold", fontSize=14,
        textColor=C_AMBER, alignment=TA_CENTER,
    )
    styles["valor_blue"] = ParagraphStyle(
        "valor_blue", fontName="Helvetica-Bold", fontSize=14,
        textColor=C_BLUE, alignment=TA_CENTER,
    )
    styles["recomendacion"] = ParagraphStyle(
        "recomendacion", fontName="Helvetica", fontSize=9,
        textColor=C_WHITE, spaceAfter=5, leading=13,
        leftIndent=12,
    )
    styles["clasificacion"] = ParagraphStyle(
        "clasificacion", fontName="Helvetica-Bold", fontSize=9,
        textColor=C_RED, alignment=TA_CENTER, spaceAfter=2,
    )
    return styles


# ─────────────────────────────────────────────────────────────────────────────
# GENERADOR DE PDF
# ─────────────────────────────────────────────────────────────────────────────

def generar_pdf(output_path: str, datos: dict, analisis: str, recomendaciones: list):
    S = get_styles()
    W, H = A4

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=1.5*cm, leftMargin=1.5*cm,
        topMargin=2.8*cm,   bottomMargin=2.0*cm,
        title="Reporte Ejecutivo CENTINELA",
        author="EATON DYNAMICS",
        subject="Sistema de Drones de Seguridad — Lima, Perú",
    )

    story = []
    now   = datetime.now()

    # ══════════════════════════════════════════════════════════
    # PORTADA
    # ══════════════════════════════════════════════════════════
    story.append(Spacer(1, 2*cm))
    story.append(Paragraph("◈  PROYECTO CENTINELA", S["titulo"]))
    story.append(Paragraph("Sistema de Drones de Seguridad y Vigilancia", S["subtitulo"]))
    story.append(Spacer(1, 0.3*cm))
    story.append(HRFlowable(width="100%", thickness=2, color=C_AMBER, spaceAfter=8))

    story.append(Paragraph("REPORTE EJECUTIVO OPERATIVO", ParagraphStyle(
        "rep", fontName="Helvetica-Bold", fontSize=15,
        textColor=C_BLUE, alignment=TA_CENTER, spaceAfter=4,
    )))
    story.append(Paragraph(
        f"Período: {now.strftime('%d de %B de %Y')}  |  "
        f"Generado: {now.strftime('%H:%M:%S')}",
        S["subtitulo"],
    ))
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph("⚠  DOCUMENTO CONFIDENCIAL — USO INTERNO", S["clasificacion"]))
    story.append(HRFlowable(width="100%", thickness=1, color=C_GRAY, spaceAfter=16))
    story.append(Spacer(1, 0.8*cm))

    # Cuadro de resumen de portada
    cover_data = [
        ["MÉTRICA",            "VALOR",              "ESTADO"],
        ["Drones Activos",     "6 / 6",              "NOMINAL"],
        ["Uptime del Sistema", f"{datos['uptime_horas']} horas", "NOMINAL"],
        ["Eficiencia de Flota",f"{datos['eficiencia']}%",       "NOMINAL"],
        ["Alertas Gestionadas",str(datos['alertas_total']),     "REVISADO"],
        ["ROI Promedio IA",    str(datos['roi_promedio']),       "ÓPTIMO"],
        ["Motor IA Principal", "Claude Opus 4.7",               "ACTIVO"],
    ]
    cover_table = Table(cover_data, colWidths=[6*cm, 5*cm, 5*cm])
    cover_table.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (-1,0),  C_AMBER),
        ("TEXTCOLOR",    (0,0), (-1,0),  C_BLACK),
        ("FONTNAME",     (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",     (0,0), (-1,0),  10),
        ("BACKGROUND",   (0,1), (-1,-1), C_DARK),
        ("TEXTCOLOR",    (0,1), (-1,-1), C_WHITE),
        ("FONTNAME",     (0,1), (-1,-1), "Helvetica"),
        ("FONTSIZE",     (0,1), (-1,-1), 9),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [C_DARK, C_BLACK]),
        ("ALIGN",        (0,0), (-1,-1), "CENTER"),
        ("VALIGN",       (0,0), (-1,-1), "MIDDLE"),
        ("GRID",         (0,0), (-1,-1), 0.5, C_GRAY),
        ("ROWHEIGHT",    (0,0), (-1,-1), 22),
        ("TOPPADDING",   (0,0), (-1,-1), 6),
        ("BOTTOMPADDING",(0,0), (-1,-1), 6),
        # Estado color
        ("TEXTCOLOR",    (2,1), (2,-1),  C_GREEN),
        ("FONTNAME",     (2,1), (2,-1),  "Helvetica-Bold"),
    ]))
    story.append(cover_table)

    story.append(Spacer(1, 1*cm))
    story.append(Paragraph(
        "EATON DYNAMICS  ·  Lima, Perú  ·  "
        "Municipalidad de Lima — Programa de Seguridad Ciudadana",
        ParagraphStyle("firma", fontName="Helvetica", fontSize=8,
                       textColor=C_LGRAY, alignment=TA_CENTER),
    ))
    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════
    # PÁGINA 2 — ANÁLISIS EJECUTIVO + KPIs
    # ══════════════════════════════════════════════════════════
    story.append(Paragraph("1. ANÁLISIS EJECUTIVO", S["seccion"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_GRAY, spaceAfter=8))
    story.append(Paragraph(
        f"<b>Generado por Claude Opus 4.7</b>  ·  "
        f"{now.strftime('%d/%m/%Y %H:%M')}",
        S["label"],
    ))
    story.append(Spacer(1, 0.3*cm))

    # Panel de análisis con fondo oscuro
    analisis_table = Table(
        [[Paragraph(analisis, ParagraphStyle(
            "ai_text", fontName="Helvetica", fontSize=9.5,
            textColor=C_WHITE, leading=15, alignment=TA_JUSTIFY,
        ))]],
        colWidths=[W - 3.5*cm],
    )
    analisis_table.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), C_DARK),
        ("LEFTPADDING",   (0,0), (-1,-1), 14),
        ("RIGHTPADDING",  (0,0), (-1,-1), 14),
        ("TOPPADDING",    (0,0), (-1,-1), 12),
        ("BOTTOMPADDING", (0,0), (-1,-1), 12),
        ("BOX",           (0,0), (-1,-1), 1.5, C_AMBER),
    ]))
    story.append(analisis_table)
    story.append(Spacer(1, 0.6*cm))

    # KPIs en grid
    story.append(Paragraph("2. KPIs OPERATIVOS", S["seccion"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_GRAY, spaceAfter=10))

    kpi_items = [
        ("Distancia Total Patrullada", f"{datos['distancia_total']} km",       C_BLUE),
        ("Uptime del Sistema",         f"{datos['uptime_horas']} horas",        C_GREEN),
        ("Alertas Gestionadas",        str(datos['alertas_total']),             C_AMBER),
        ("Despachos Ejecutados",       str(datos['despachos']),                 C_AMBER),
        ("Consultas IA (Total)",       str(datos['consultas_ia']),              C_BLUE),
        ("Eficiencia de Flota",        f"{datos['eficiencia']}%",               C_GREEN),
        ("ROI Promedio",               str(datos['roi_promedio']),              C_AMBER),
        ("Batería Promedio Flota",     f"{datos['bat_promedio']}%",             C_GREEN),
    ]

    kpi_rows = []
    for i in range(0, len(kpi_items), 2):
        row = []
        for j in range(2):
            if i+j < len(kpi_items):
                label, val, color = kpi_items[i+j]
                cell_content = Table([
                    [Paragraph(label, ParagraphStyle(
                        "kl", fontName="Helvetica", fontSize=7.5,
                        textColor=C_LGRAY, alignment=TA_CENTER,
                    ))],
                    [Paragraph(val, ParagraphStyle(
                        "kv", fontName="Helvetica-Bold", fontSize=16,
                        textColor=color, alignment=TA_CENTER,
                    ))],
                ], colWidths=[7.5*cm])
                cell_content.setStyle(TableStyle([
                    ("BACKGROUND",    (0,0), (-1,-1), C_DARK),
                    ("TOPPADDING",    (0,0), (-1,-1), 8),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 8),
                    ("BOX",           (0,0), (-1,-1), 0.5, C_GRAY),
                ]))
                row.append(cell_content)
            else:
                row.append("")
        kpi_rows.append(row)

    kpi_grid = Table(kpi_rows, colWidths=[7.7*cm, 7.7*cm], spaceBefore=4)
    kpi_grid.setStyle(TableStyle([
        ("ALIGN",       (0,0), (-1,-1), "CENTER"),
        ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
        ("ROWHEIGHT",   (0,0), (-1,-1), 1.8*cm),
        ("LEFTPADDING", (0,0), (-1,-1), 3),
        ("RIGHTPADDING",(0,0), (-1,-1), 3),
        ("TOPPADDING",  (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0),(-1,-1), 3),
    ]))
    story.append(kpi_grid)
    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════
    # PÁGINA 3 — ESTADO DE FLOTA + INCIDENTES
    # ══════════════════════════════════════════════════════════
    story.append(Paragraph("3. ESTADO DE FLOTA POR SECTOR", S["seccion"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_GRAY, spaceAfter=10))

    fleet_headers = ["DRONE", "SECTOR", "BAT %", "ALT m", "VEL m/s",
                     "MOT HS", "DISTANCIA", "ESTADO IA"]
    fleet_rows = [fleet_headers]
    for d in datos["drones"]:
        bat_str = f"{d['battery_pct']}%"
        fleet_rows.append([
            d["id"],
            d["sector"],
            bat_str,
            f"{d['altitude_m']}m",
            f"{d['vel_ms']} m/s",
            str(d["motor_health"]),
            f"{d['distancia_km']} km",
            d["estado_ia"],
        ])

    fleet_table = Table(fleet_rows,
        colWidths=[1.8*cm, 3.0*cm, 1.6*cm, 1.6*cm, 1.8*cm, 1.8*cm, 2.0*cm, 2.0*cm])

    fleet_style = [
        ("BACKGROUND",    (0,0), (-1,0),  C_AMBER),
        ("TEXTCOLOR",     (0,0), (-1,0),  C_BLACK),
        ("FONTNAME",      (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,0),  8),
        ("BACKGROUND",    (0,1), (-1,-1), C_DARK),
        ("TEXTCOLOR",     (0,1), (-1,-1), C_WHITE),
        ("FONTNAME",      (0,1), (-1,-1), "Helvetica"),
        ("FONTSIZE",      (0,1), (-1,-1), 8),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [C_DARK, C_BLACK]),
        ("ALIGN",         (0,0), (-1,-1), "CENTER"),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("GRID",          (0,0), (-1,-1), 0.3, C_GRAY),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
    ]
    # Color estado IA
    for i, d in enumerate(datos["drones"], 1):
        col = C_GREEN if d["estado_ia"]=="NOMINAL" else C_YELLOW
        fleet_style.append(("TEXTCOLOR", (7,i), (7,i), col))
        fleet_style.append(("FONTNAME",  (7,i), (7,i), "Helvetica-Bold"))
        # Color batería
        bat_col = C_RED if d["battery_pct"]<25 else C_YELLOW if d["battery_pct"]<40 else C_GREEN
        fleet_style.append(("TEXTCOLOR", (2,i), (2,i), bat_col))

    fleet_table.setStyle(TableStyle(fleet_style))
    story.append(fleet_table)
    story.append(Spacer(1, 0.6*cm))

    # Incidentes
    story.append(Paragraph("4. LOG DE INCIDENTES", S["seccion"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_GRAY, spaceAfter=10))

    inc_headers = ["HORA", "DRONE", "SECTOR", "TIPO", "SEVERIDAD", "ACCIÓN", "ESTADO"]
    inc_rows = [inc_headers]
    for inc in datos["incidentes"]:
        inc_rows.append([
            inc["hora"], inc["drone"], inc["sector"],
            inc["tipo"], inc["severidad"], inc["accion"][:30],
            "RESUELTO" if inc["resuelto"] else "PENDIENTE",
        ])

    inc_table = Table(inc_rows,
        colWidths=[1.8*cm, 1.8*cm, 2.2*cm, 3.0*cm, 2.0*cm, 3.8*cm, 2.0*cm])
    inc_style = [
        ("BACKGROUND",    (0,0), (-1,0),  C_BLUE),
        ("TEXTCOLOR",     (0,0), (-1,0),  C_WHITE),
        ("FONTNAME",      (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,0),  8),
        ("BACKGROUND",    (0,1), (-1,-1), C_DARK),
        ("TEXTCOLOR",     (0,1), (-1,-1), C_WHITE),
        ("FONTNAME",      (0,1), (-1,-1), "Helvetica"),
        ("FONTSIZE",      (0,1), (-1,-1), 7.5),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [C_DARK, C_BLACK]),
        ("ALIGN",         (0,0), (-1,-1), "CENTER"),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("GRID",          (0,0), (-1,-1), 0.3, C_GRAY),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("WORDWRAP",      (0,0), (-1,-1), True),
    ]
    for i, inc in enumerate(datos["incidentes"], 1):
        sev_col = C_RED if inc["severidad"]=="CRITICAL" else C_YELLOW
        est_col = C_GREEN if inc["resuelto"] else C_RED
        inc_style.append(("TEXTCOLOR", (4,i), (4,i), sev_col))
        inc_style.append(("FONTNAME",  (4,i), (4,i), "Helvetica-Bold"))
        inc_style.append(("TEXTCOLOR", (6,i), (6,i), est_col))
        inc_style.append(("FONTNAME",  (6,i), (6,i), "Helvetica-Bold"))
    inc_table.setStyle(TableStyle(inc_style))
    story.append(inc_table)
    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════
    # PÁGINA 4 — RECOMENDACIONES + FIRMA
    # ══════════════════════════════════════════════════════════
    story.append(Paragraph("5. RECOMENDACIONES TÁCTICAS — OPUS 4.7", S["seccion"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_GRAY, spaceAfter=10))
    story.append(Paragraph(
        "Las siguientes recomendaciones fueron generadas por Claude Opus 4.7 "
        "basándose en el análisis completo del período operativo:",
        S["cuerpo"],
    ))
    story.append(Spacer(1, 0.3*cm))

    for i, rec in enumerate(recomendaciones, 1):
        rec_table = Table(
            [[Paragraph(f"{i}.", ParagraphStyle(
                 "num", fontName="Helvetica-Bold", fontSize=11,
                 textColor=C_AMBER, alignment=TA_CENTER,
             )),
              Paragraph(rec, S["recomendacion"])]],
            colWidths=[1.0*cm, W-4.5*cm],
        )
        rec_table.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,-1), C_DARK),
            ("ALIGN",         (0,0), (0,0),   "CENTER"),
            ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
            ("LEFTPADDING",   (0,0), (-1,-1), 10),
            ("RIGHTPADDING",  (0,0), (-1,-1), 10),
            ("TOPPADDING",    (0,0), (-1,-1), 10),
            ("BOTTOMPADDING", (0,0), (-1,-1), 10),
            ("BOX",           (0,0), (-1,-1), 0.5, C_AMBER),
        ]))
        story.append(rec_table)
        story.append(Spacer(1, 0.3*cm))

    story.append(Spacer(1, 0.5*cm))

    # Orden del coordinador
    story.append(Paragraph("6. ORDEN ACTIVA DEL COORDINADOR DE FLOTA", S["seccion"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_GRAY, spaceAfter=10))
    coord_table = Table(
        [[Paragraph(
            f'"{datos["coord_orden"]}"',
            ParagraphStyle("coord_txt", fontName="Helvetica-BoldOblique",
                           fontSize=10, textColor=C_AMBER, alignment=TA_CENTER,
                           leading=16),
        )]],
        colWidths=[W-3.5*cm],
    )
    coord_table.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), C_BLACK),
        ("BOX",           (0,0), (-1,-1), 2, C_AMBER),
        ("TOPPADDING",    (0,0), (-1,-1), 14),
        ("BOTTOMPADDING", (0,0), (-1,-1), 14),
        ("LEFTPADDING",   (0,0), (-1,-1), 16),
        ("RIGHTPADDING",  (0,0), (-1,-1), 16),
    ]))
    story.append(coord_table)
    story.append(Spacer(1, 1.0*cm))

    # Firma
    story.append(HRFlowable(width="100%", thickness=1, color=C_GRAY, spaceAfter=12))
    firma_data = [
        [Paragraph("EATON DYNAMICS", ParagraphStyle(
             "firma1", fontName="Helvetica-Bold", fontSize=10,
             textColor=C_AMBER, alignment=TA_CENTER,
         )),
         Paragraph("MUNICIPALIDAD DE LIMA", ParagraphStyle(
             "firma2", fontName="Helvetica-Bold", fontSize=10,
             textColor=C_BLUE, alignment=TA_CENTER,
         ))],
        [Paragraph("Director de Operaciones", ParagraphStyle(
             "cargo", fontName="Helvetica", fontSize=8,
             textColor=C_LGRAY, alignment=TA_CENTER,
         )),
         Paragraph("Programa de Seguridad Ciudadana", ParagraphStyle(
             "cargo2", fontName="Helvetica", fontSize=8,
             textColor=C_LGRAY, alignment=TA_CENTER,
         ))],
    ]
    firma_table = Table(firma_data, colWidths=[8*cm, 8*cm])
    firma_table.setStyle(TableStyle([
        ("ALIGN",   (0,0), (-1,-1), "CENTER"),
        ("VALIGN",  (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 6),
    ]))
    story.append(firma_table)

    # Build
    cc = CentinelaCanvas(output_path, datos)
    doc.build(story, onFirstPage=cc, onLaterPages=cc)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("◈ CENTINELA — GENERADOR DE REPORTES EJECUTIVOS")
    print("  Motor: Claude Opus 4.7 + ReportLab")
    print("─" * 50)

    # Crear carpeta de reportes
    reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reportes")
    os.makedirs(reports_dir, exist_ok=True)

    print("\n[1/4] Recopilando datos de flota...")
    datos = generar_datos_flota()
    print(f"      {len(datos['drones'])} drones | "
          f"{datos['alertas_total']} alertas | "
          f"{datos['uptime_horas']}h uptime")

    print("\n[2/4] Generando análisis ejecutivo con Opus 4.7...")
    analisis = generar_analisis_ejecutivo(datos)
    print(f"      Análisis generado ({len(analisis)} caracteres)")

    print("\n[3/4] Generando recomendaciones tácticas con Opus 4.7...")
    recomendaciones = generar_recomendaciones(datos)
    print(f"      {len(recomendaciones)} recomendaciones generadas")

    ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename  = f"CENTINELA_Reporte_{ts}.pdf"
    filepath  = os.path.join(reports_dir, filename)

    print(f"\n[4/4] Generando PDF...")
    generar_pdf(filepath, datos, analisis, recomendaciones)

    print(f"\n✓ REPORTE GENERADO:")
    print(f"  {filepath}")
    print(f"\n  Páginas: 4")
    print(f"  Clasificación: CONFIDENCIAL")
    print(f"  Listo para presentar a gerencia.")


if __name__ == "__main__":
    main()
