from isaacsim import SimulationApp

app = SimulationApp({"headless": True})

from isaacsim.core.api import World
import numpy as np, json, time

world = World(stage_units_in_meters=1.0)
world.scene.add_default_ground_plane()
world.reset()

print("CENTINELA — Isaac Sim Headless activo")
print("Drone CNTL-SIM-01 inicializado")
print("─" * 50)

step = 0
while step < 500:
    world.step(render=False)
    if step % 20 == 0:
        t = {
            "drone":    "CNTL-SIM-01",
            "tick":     step,
            "lat":      -12.0464 + np.random.normal(0, 0.0002),
            "lon":      -77.0428 + np.random.normal(0, 0.0002),
            "alt_m":    80.0 + np.sin(step * 0.1) * 5,
            "bat_pct":  100.0 - step * 0.08,
            "vel_ms":   8.5 + np.random.normal(0, 0.3),
        }
        print(f"[{t['tick']:04d}] ALT:{t['alt_m']:.1f}m "
              f"BAT:{t['bat_pct']:.1f}% "
              f"VEL:{t['vel_ms']:.1f}m/s "
              f"GPS:({t['lat']:.5f},{t['lon']:.5f})")
    step += 1

print("Simulación completada.")
app.close()