"""
PROYECTO CENTINELA — LAUNCHER v1.0
Panel de control maestro — un solo comando para todo el sistema

Uso: python centinela_launcher.py
"""

import os, sys, time, subprocess, threading
from datetime import datetime
from rich import box as rbox
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align
from rich.prompt import Prompt
from rich.live import Live

console = Console()

AMBER = "bright_yellow"
BLUE  = "bright_cyan"
RED   = "bright_red"
GREEN = "bright_green"
DIM   = "dim white"
MAG   = "magenta"

BASE = r"C:\EATON\Centinela"

MODULOS = [
    {"id":"1",  "nombre":"Terminal Dashboard",     "archivo":"centinela_master.py",          "tipo":"terminal", "desc":"Dashboard Rich en tiempo real"},
    {"id":"2",  "nombre":"Física PyBullet",         "archivo":"centinela_pybullet.py",        "tipo":"terminal", "desc":"Drone con física newtoniana real"},
    {"id":"3",  "nombre":"Dashboard Bloomberg",     "archivo":"centinela_streamlit.py",       "tipo":"streamlit","desc":"Web UI estilo Bloomberg"},
    {"id":"4",  "nombre":"Agente IA Sonnet",        "archivo":"centinela_ai_agent.py",        "tipo":"terminal", "desc":"Claude Sonnet 4.6 táctico"},
    {"id":"5",  "nombre":"Multi-Agente 6 Drones",  "archivo":"centinela_multiagent.py",      "tipo":"terminal", "desc":"6 agentes + Coordinador Opus 4.7"},
    {"id":"6",  "nombre":"Reportes PDF",            "archivo":"centinela_report.py",          "tipo":"terminal", "desc":"PDF ejecutivo con Opus 4.7"},
    {"id":"7",  "nombre":"API REST",                "archivo":"centinela_api.py",             "tipo":"terminal", "desc":"FastAPI Swagger → localhost:8000"},
    {"id":"8",  "nombre":"Visión IA",               "archivo":"centinela_vision.py",          "tipo":"terminal", "desc":"Claude Vision webcam"},
    {"id":"9",  "nombre":"Alertas Telegram",        "archivo":"centinela_telegram.py",        "tipo":"terminal", "desc":"Notificaciones tiempo real"},
    {"id":"10", "nombre":"ML Predicción Zonas",     "archivo":"centinela_prediccion.py",      "tipo":"terminal", "desc":"Random Forest + Opus 4.7"},
    {"id":"11", "nombre":"Simulación Incidentes",   "archivo":"centinela_incidentes.py",      "tipo":"terminal", "desc":"Cadena de respuesta completa"},
    {"id":"12", "nombre":"Threat Score",            "archivo":"centinela_threat.py",          "tipo":"terminal", "desc":"Índice unificado 0-100"},
    {"id":"13", "nombre":"Lima 20 Distritos",       "archivo":"centinela_lima_expansion.py",  "tipo":"terminal", "desc":"Cobertura Lima completa"},
    {"id":"14", "nombre":"Satélite + Cámaras",      "archivo":"centinela_satelite_camaras.py","tipo":"terminal", "desc":"Sentinel-2 + SÍVICO Lima"},
    {"id":"15", "nombre":"Cámaras En Vivo",         "archivo":"centinela_camaras_live.py",    "tipo":"streamlit","desc":"Grid video en vivo"},
    {"id":"16", "nombre":"YOLO Detección",          "archivo":"centinela_yolo.py",            "tipo":"streamlit","desc":"YOLOv11 detección real"},
    {"id":"17", "nombre":"Stream WebSocket",        "archivo":"centinela_stream.py",          "tipo":"terminal", "desc":"Video 30fps → localhost:8765"},
    {"id":"18", "nombre":"Enjambre MAVLink",        "archivo":"centinela_swarm.py",           "tipo":"terminal", "desc":"6 drones + ORCA + Opus 4.7"},
    {"id":"19", "nombre":"Seguridad 10 Capas",      "archivo":"centinela_security.py",        "tipo":"terminal", "desc":"JWT + AES + IDS + Zero Trust"},
]

procesos = {}  # id → subprocess

def verificar_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY",""))

def lanzar_modulo(mod: dict):
    mid  = mod["id"]
    path = os.path.join(BASE, mod["archivo"])

    if not os.path.exists(path):
        console.print(f"[{RED}]✗ Archivo no encontrado: {path}[/]")
        return

    if mid in procesos and procesos[mid].poll() is None:
        console.print(f"[{AMBER}]⚠ {mod['nombre']} ya está corriendo[/]")
        return

    if mod["tipo"] == "streamlit":
        cmd = [sys.executable, "-m", "streamlit", "run", path]
    else:
        cmd = [sys.executable, path]

    env = os.environ.copy()

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=BASE,
            env=env,
            creationflags=subprocess.CREATE_NEW_CONSOLE if os.name=="nt" else 0,
        )
        procesos[mid] = proc
        console.print(f"[{GREEN}]✓ {mod['nombre']} iniciado (PID {proc.pid})[/]")
        if mod["tipo"] == "streamlit":
            console.print(f"[{BLUE}]  → http://localhost:8501[/]")
        elif "stream" in mod["archivo"]:
            console.print(f"[{BLUE}]  → http://localhost:8765[/]")
        elif "api" in mod["archivo"]:
            console.print(f"[{BLUE}]  → http://localhost:8000/docs[/]")
    except Exception as e:
        console.print(f"[{RED}]✗ Error al lanzar: {e}[/]")

def detener_modulo(mid: str):
    if mid not in procesos:
        console.print(f"[{AMBER}]⚠ Módulo {mid} no está corriendo[/]")
        return
    proc = procesos[mid]
    if proc.poll() is None:
        proc.terminate()
        console.print(f"[{RED}]✓ Módulo {mid} detenido[/]")
    del procesos[mid]

def detener_todos():
    for mid, proc in list(procesos.items()):
        if proc.poll() is None:
            proc.terminate()
    procesos.clear()
    console.print(f"[{RED}]✓ Todos los módulos detenidos[/]")

LOGO = """
 ██████╗███████╗███╗   ██╗████████╗██╗███╗   ██╗███████╗██╗      █████╗
██╔════╝██╔════╝████╗  ██║╚══██╔══╝██║████╗  ██║██╔════╝██║     ██╔══██╗
██║     █████╗  ██╔██╗ ██║   ██║   ██║██╔██╗ ██║█████╗  ██║     ███████║
██║     ██╔══╝  ██║╚██╗██║   ██║   ██║██║╚██╗██║██╔══╝  ██║     ██╔══██║
╚██████╗███████╗██║ ╚████║   ██║   ██║██║ ╚████║███████╗███████╗██║  ██║
 ╚═════╝╚══════╝╚═╝  ╚═══╝   ╚═╝   ╚═╝╚═╝  ╚═══╝╚══════╝╚══════╝╚═╝  ╚═╝"""

def build_header() -> Panel:
    import platform, psutil
    ts     = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    api_ok = verificar_api_key()
    activos= sum(1 for p in procesos.values() if p.poll() is None)

    try:
        cpu = f"{psutil.cpu_percent(interval=0.1):.0f}%"
        ram = f"{psutil.virtual_memory().percent:.0f}%"
    except:
        cpu = ram = "N/A"

    logo_txt = Text(LOGO, style=f"bold {AMBER}", justify="center")

    info = Text(justify="center")
    info.append("\n  EATON DYNAMICS", style=f"bold {BLUE}")
    info.append("  ·  ", style=DIM)
    info.append("Lima, Perú", style="white")
    info.append("  ·  ", style=DIM)
    info.append(ts, style=MAG)
    info.append("\n\n  API: ", style=DIM)
    info.append(f"{'✓ ACTIVA' if api_ok else '✗ NO CONFIGURADA'}",
                style=f"bold {GREEN if api_ok else RED}")
    info.append("  │  MÓDULOS ACTIVOS: ", style=DIM)
    info.append(f"{activos}/19", style=f"bold {GREEN if activos>0 else DIM}")
    info.append(f"  │  CPU: {cpu}  RAM: {ram}", style=DIM)

    content = Text.__add__(logo_txt, info) if False else logo_txt
    full = Table.grid(expand=True)
    full.add_row(logo_txt)
    full.add_row(info)

    return Panel(full, style=AMBER, padding=(0,1))


def build_menu() -> Table:
    api_ok = verificar_api_key()

    t = Table(
        box=rbox.SIMPLE_HEAD, style=DIM,
        header_style=f"bold {AMBER}",
        show_lines=False, expand=True,
        border_style=BLUE,
    )
    t.add_column("ID",      style=f"bold {BLUE}", width=4)
    t.add_column("MÓDULO",  width=24)
    t.add_column("TIPO",    width=10)
    t.add_column("ESTADO",  width=14)
    t.add_column("DESCRIPCIÓN", style=DIM)

    ICONOS = {
        "1":"📊","2":"⚙","3":"🖥","4":"🤖","5":"🚁",
        "6":"📋","7":"🔌","8":"👁","9":"📱","10":"🧠",
        "11":"🚨","12":"⚠","13":"🗺","14":"🛰","15":"📹",
        "16":"🎯","17":"📡","18":"🐝","19":"🔒",
    }
    for mod in MODULOS:
        mid   = mod["id"]
        proc  = procesos.get(mid)
        if proc and proc.poll() is None:
            estado = f"[bold {GREEN}]▶ CORRIENDO[/]"
        elif proc:
            estado = f"[{AMBER}]■ DETENIDO[/]"
            del procesos[mid]
        else:
            estado = f"[{DIM}]○ INACTIVO[/]"

        tipo_c = BLUE if mod["tipo"]=="streamlit" else AMBER
        icono  = ICONOS.get(mid,"·")
        t.add_row(
            f"[bold {BLUE}]{mid}[/]",
            f"{icono} {mod['nombre']}",
            f"[{tipo_c}]{mod['tipo'].upper()}[/]",
            estado,
            mod["desc"],
        )
    return t

def build_ayuda() -> Panel:
    t = Text()
    t.append("\n  COMANDOS DISPONIBLES:\n\n", style=f"bold {AMBER}")
    t.append("  1-19        ", style=f"bold {BLUE}")
    t.append("→ Lanzar módulo en ventana nueva\n", style="white")
    t.append("  d1-d19      ", style=f"bold {RED}")
    t.append("→ Detener módulo (ej: d5)\n", style="white")
    t.append("  todos       ", style=f"bold {GREEN}")
    t.append("→ Lanzar TODOS los módulos\n", style="white")
    t.append("  stop        ", style=f"bold {RED}")
    t.append("→ Detener TODOS los módulos\n", style="white")
    t.append("  key         ", style=f"bold {AMBER}")
    t.append("→ Configurar API key de Anthropic\n", style="white")
    t.append("  status      ", style=f"bold {BLUE}")
    t.append("→ Ver estado de todos los módulos\n", style="white")
    t.append("  salir       ", style=f"bold {DIM}")
    t.append("→ Salir del launcher\n", style="white")
    t.append("\n  MÓDULOS WEB (se abren en el navegador):\n", style=f"bold {AMBER}")
    t.append("  3,15,16     ", style=DIM)
    t.append("→ Streamlit → localhost:8501\n", style="white")
    t.append("  17          ", style=DIM)
    t.append("→ WebSocket stream → localhost:8765\n", style="white")
    t.append("  7           ", style=DIM)
    t.append("→ API REST → localhost:8000/docs\n", style="white")
    return Panel(t,
        title=f"[bold {AMBER}]▸ AYUDA[/]",
        border_style=BLUE, padding=(0,1))

def main():
    console.clear()

    # Verificar API key
    if not verificar_api_key():
        console.print(Panel.fit(
            f"[{AMBER}]API key de Anthropic no configurada.[/]\n"
            f"[{DIM}]Algunos módulos no funcionarán sin ella.[/]",
            border_style=AMBER,
        ))
        conf = Prompt.ask("¿Configurar ahora?", choices=["s","n"], default="s")
        if conf == "s":
            key = Prompt.ask("Pega tu API key")
            os.environ["ANTHROPIC_API_KEY"] = key.strip()
            console.print(f"[{GREEN}]✓ API key configurada[/]")

    console.print(Panel.fit(
        f"[bold {AMBER}]CENTINELA COMMAND CENTER v1.0[/]\n"
        f"[{BLUE}]EATON DYNAMICS — Lima, Perú[/]\n"
        f"[{DIM}]20 módulos disponibles[/]",
        border_style=AMBER,
    ))
    time.sleep(1)

    try:
        import psutil
    except:
        pass

    while True:
        console.clear()
        console.print(build_header())
        console.print(Panel(
            build_menu(),
            title=f"[bold {AMBER}]▸ MÓDULOS DISPONIBLES[/]",
            border_style=BLUE, padding=(0,1)
        ))
        console.print(build_ayuda())

        cmd = Prompt.ask(
            f"\n[bold {AMBER}]CENTINELA >[/]"
        ).strip().lower()

        if cmd == "salir":
            detener_todos()
            console.print(f"[bold {AMBER}]◈ CENTINELA CERRADO.[/]")
            break

        elif cmd == "stop":
            detener_todos()
            time.sleep(1)

        elif cmd == "key":
            key = Prompt.ask("Nueva API key")
            os.environ["ANTHROPIC_API_KEY"] = key.strip()
            console.print(f"[{GREEN}]✓ API key actualizada[/]")
            time.sleep(1)

        elif cmd == "todos":
            for mod in MODULOS[:6]:  # Solo los más importantes
                lanzar_modulo(mod)
                time.sleep(0.5)
            time.sleep(2)

        elif cmd.startswith("d") and cmd[1:].isdigit():
            detener_modulo(cmd[1:])
            time.sleep(1)

        elif cmd.isdigit():
            mod = next((m for m in MODULOS if m["id"]==cmd), None)
            if mod:
                lanzar_modulo(mod)
            else:
                console.print(f"[{RED}]Módulo {cmd} no existe[/]")
            time.sleep(1.5)

        else:
            console.print(f"[{RED}]Comando no reconocido: {cmd}[/]")
            time.sleep(1)


if __name__ == "__main__":
    main()
