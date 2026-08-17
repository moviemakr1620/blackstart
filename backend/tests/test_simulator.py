from app.engine.simulator import Simulator


def test_pull_plug_marks_on_battery():
    sim = Simulator(charge=90, load_watts=400)
    snap = sim.snapshot()
    assert snap["on_battery"] is False
    sim.pull_plug()
    snap = sim.snapshot()
    assert snap["on_battery"] is True
    assert snap["input_voltage"] == 0


def test_restore_charges():
    sim = Simulator(charge=40, load_watts=200)
    sim.pull_plug()
    sim.restore_grid()
    before = sim.charge
    sim.last_tick -= 60
    sim.tick()
    assert sim.charge > before
