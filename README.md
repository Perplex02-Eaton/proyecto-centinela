# ◈ PROYECTO CENTINELA
### Sistema Autónomo de Drones de Seguridad Ciudadana con IA
**EATON DYNAMICS — Lima, Perú**

![Python](https://img.shields.io/badge/Python-3.11+-blue?style=flat-square&logo=python)
![Claude AI](https://img.shields.io/badge/Claude-Sonnet%204.6%20%2B%20Opus%204.7-orange?style=flat-square)
![YOLOv11](https://img.shields.io/badge/YOLO-v11-darkgreen?style=flat-square)
![MAVLink](https://img.shields.io/badge/MAVLink-2.0-red?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)
![Status](https://img.shields.io/badge/Status-MVP%20Activo-brightgreen?style=flat-square)

---

## ¿Qué es Centinela?

**Proyecto Centinela** es un sistema de gestión táctica de flotas de drones de seguridad ciudadana, con inteligencia artificial integrada en cada capa. Diseñado para operar sobre Lima Metropolitana con cobertura de 20 distritos y 6.8 millones de habitantes.

El sistema fusiona **física de simulación real**, **visión por computadora**, **múltiples agentes IA**, **predicción ML** y **seguridad de nivel militar** en una plataforma unificada — comparable en arquitectura a Palantir Gotham y Anduril Lattice OS.

---

## Demostración

```
◈ CENTINELA — 6 DRONES | MAVLINK 2.0 | ORCA | OPUS 4.7
────────────────────────────────────────────────────────
CNTL-01 Miraflores   PATROL   BAT:94% ALT:82m  NOMINAL
CNTL-02 San Isidro   PATROL   BAT:87% ALT:91m  HIGH ⚠
CNTL-03 Barranco     INTERCEPT BAT:71% ALT:68m  CRITICAL 🔴
CNTL-04 Surquillo    PATROL   BAT:89% ALT:85m  NOMINAL
CNTL-05 La Victoria  RTH      BAT:12% ALT:45m  EMERGENCY 🚨
CNTL-06 Lince        HOVER    BAT:82% ALT:79m  NOMINAL
────────────────────────────────────────────────────────
OPUS 4.7: "CNTL-02 mantiene seguimiento del vehículo
sospechoso en San Isidro con apoyo de CNTL-06"
```

---

## Módulos del Sistema

| # | Módulo | Tecnología | Descripción |
|---|--------|------------|-------------|
| 1 | Terminal Dashboard | Rich + AsyncIO | Telemetría en tiempo real |
| 2 | Física PyBullet | PyBullet 3.2.7 | Dinámica newtoniana real |
| 3 | Dashboard Bloomberg | Streamlit + Plotly | Web UI estilo Bloomberg |
| 4 | Agente IA | Claude Sonnet 4.6 | Decisiones tácticas individuales |
| 5 | Multi-Agente | Sonnet 4.6 × 6 + Opus 4.7 | 6 agentes + coordinador |
| 6 | Reportes PDF | ReportLab + Opus 4.7 | Ejecutivo para gerencia |
| 7 | API REST | FastAPI + Swagger | Endpoints profesionales |
| 8 | Visión IA | Claude Vision | Análisis de cámara en tiempo real |
| 9 | Alertas Telegram | Bot API | Notificaciones instantáneas |
| 10 | ML Predicción | Random Forest + Gradient Boosting | Zonas de riesgo futuras |
| 11 | Simulación Incidentes | Claude API | Cadena de respuesta completa |
| 12 | Threat Score | Motor compuesto | Índice unificado 0-100 |
| 13 | Lima 20 Distritos | Datos INEI/PNP | Cobertura Lima completa |
| 14 | Satélite + Cámaras | Sentinel-2 ESA + SÍVICO | Vigilancia multi-sensor |
| 15 | Cámaras En Vivo | Streamlit + PIL | Grid video en tiempo real |
| 16 | YOLO Detección | YOLOv11 + Streamlit | Detección personas/vehículos |
| 17 | Stream WebSocket | FastAPI + WebSocket | Video 30fps sin latencia |
| 18 | Enjambre MAVLink | MAVLink 2.0 + ORCA | 6 drones coordinados |
| 19 | Seguridad 10 Capas | JWT+AES+TLS+IDS | Nivel militar/enterprise |
| 20 | Launcher Unificado | Rich CLI | Panel de control maestro |

---

## Arquitectura

```
┌─────────────────────────────────────────────────────────┐
│              CENTINELA COMMAND CENTER                   │
│         centinela_launcher.py (punto de entrada)        │
└──────────────────────┬──────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
   SIMULACIÓN      INTELIGENCIA    SEGURIDAD
   ─────────        ARTIFICIAL     ─────────
   PyBullet         Claude          JWT RS256
   ORCA             Sonnet 4.6      AES-256-GCM
   MAVLink 2.0      Opus 4.7        TLS 1.3
   GPS/IMU          YOLOv11         HMAC-SHA256
                    RF Classifier   IDS/Geofence
        │              │              │
        └──────────────┼──────────────┘
                       │
              INTERFACES DE USUARIO
              ──────────────────────
              Rich Terminal (CLI)
              Streamlit (Web)
              FastAPI (REST)
              WebSocket (Stream)
              Telegram (Mobile)
              PDF Reports
```

---

## Stack Tecnológico

```python
CORE:
  Python 3.11+      # Lenguaje principal
  AsyncIO           # Concurrencia
  PyBullet 3.2.7    # Motor de física

IA / ML:
  anthropic         # Claude Sonnet 4.6 + Opus 4.7
  ultralytics       # YOLOv11
  scikit-learn      # Random Forest + Gradient Boosting
  numpy / pandas    # Análisis de datos

INTERFACES:
  rich              # Terminal UI
  streamlit         # Dashboard web
  fastapi           # API REST
  plotly            # Visualizaciones

SEGURIDAD:
  cryptography      # AES-256-GCM
  pyjwt             # JWT RS256
  hmac / hashlib    # HMAC-SHA256

COMUNICACIONES:
  MAVLink 2.0       # Protocolo de drones
  websockets        # Streaming en tiempo real
  python-telegram-bot # Alertas móviles
  reportlab         # Generación de PDF
```

---

## Instalación

### Prerrequisitos
- Windows 10/11 o Linux Ubuntu 22.04+
- Python 3.11 o 3.12
- NVIDIA GPU (recomendado para YOLOv11)
- API key de Anthropic

### Setup rápido

```bash
# 1. Clonar el repositorio
git clone https://github.com/Perplex02-Eaton/proyecto-centinela.git
cd proyecto-centinela

# 2. Instalar dependencias
pip install rich numpy pybullet anthropic streamlit plotly
pip install fastapi uvicorn ultralytics scikit-learn pandas
pip install reportlab cryptography pyjwt websockets psutil
pip install opencv-python Pillow requests sentinelsat

# 3. Configurar API key (permanente)
[System.Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-...", "User")

# 4. Lanzar el sistema
python centinela_launcher.py
```

---

## Uso

### Launcher unificado (recomendado)
```bash
python centinela_launcher.py
```

Desde el menú interactivo:
- Escribe `5` → lanza Multi-Agente 6 Drones
- Escribe `12` → lanza Threat Score
- Escribe `17` → lanza Stream WebSocket (abrir localhost:8765)
- Escribe `stop` → detiene todos los módulos

### Módulos individuales
```bash
# Terminal
python centinela_master.py
python centinela_multiagent.py
python centinela_threat.py
python centinela_swarm.py
python centinela_security.py

# Web (localhost:8501)
streamlit run centinela_streamlit.py
streamlit run centinela_yolo.py

# API REST (localhost:8000/docs)
python centinela_api.py

# Video 30fps (localhost:8765)
python centinela_stream.py
```

---

## Cobertura Lima Metropolitana

| Zona | Distritos | Población |
|------|-----------|-----------|
| Norte | Comas, Los Olivos, SMP, Independencia | 1.7M |
| Este | SJL, Ate, El Agustino, Santa Anita | 2.2M |
| Centro | Cercado, Rímac, La Victoria, Lince | 677K |
| Sur | Miraflores, Barranco, Surquillo, Chorrillos, SJM, VES, VMT, San Isidro | 1.7M |
| Callao | Callao | 407K |
| **Total** | **20 distritos** | **6.8M habitantes** |

---

## Seguridad

El sistema implementa 10 capas de seguridad:

1. **JWT RS256** — Autenticación de API con tokens firmados
2. **AES-256-GCM** — Cifrado de datos en reposo
3. **TLS 1.3** — Comunicaciones cifradas drone↔servidor
4. **HMAC-SHA256** — Integridad de mensajes MAVLink
5. **Anti-GPS Spoofing** — Verificación cruzada GPS+IMU
6. **IDS** — Detección de intrusiones (tasa bloqueo: 92%)
7. **Geofence** — Límites hard-coded en firmware
8. **Failsafe** — RTH/Hover/Land automático (<50ms)
9. **Audit Log** — Cadena SHA-256 inmutable
10. **Zero Trust** — Score ≥ 60 → acceso concedido

---

## Modelo de Negocio

| Plan | Precio | Target |
|------|--------|--------|
| Básico | S/. 8,000/mes | Comisarías, juntas vecinales |
| Profesional | S/. 25,000/mes | Municipalidades distritales |
| Enterprise | S/. 80,000+/mes | Municipalidad Lima, MININTER |

**Costo por drone físico:** ~$1,230 USD (DIY ensamblado)
**Flota de 6 drones:** ~$7,380 USD
**Margen bruto estimado:** 75-85%

---

## Roadmap

- [x] MVP — 20 módulos funcionando
- [x] YOLOv11 detección en tiempo real
- [x] Multi-agente con Opus 4.7
- [x] MAVLink 2.0 + ORCA enjambre
- [x] 10 capas de seguridad
- [x] 20 distritos Lima calibrados con INEI
- [ ] Base de datos PostgreSQL
- [ ] Dashboard unificado Mission Control
- [ ] Drone físico prototipo (Pixhawk + Raspberry Pi 5)
- [ ] Integración SÍVICO real (VPN municipal)
- [ ] Contrato piloto municipalidad
- [ ] Serie A funding

---

## Autor

**Eaton Palacin**
Estudiante de Negocios Internacionales — Finanzas Cuantitativas
Lima, Perú

[![GitHub](https://img.shields.io/badge/GitHub-Perplex02--Eaton-black?style=flat-square&logo=github)](https://github.com/Perplex02-Eaton)

---

## Licencia

MIT License — ver [LICENSE](LICENSE) para detalles.

---

*Construido con Claude AI (Anthropic) · Lima, Perú · 2026*
