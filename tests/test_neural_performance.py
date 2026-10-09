"""Phase 3 (2026-10-09): the neural view must not hog the CPU.

Measured with headless Edge on a 198-node brain, CPU throttled 4x to stand
in for a small PC: before, the idle view drew 3 fps at 262 ms a frame and
kept the main thread ~80% busy, lagging the whole window. These pin the
changes that brought it to 15 fps lite at ~26% busy (and 60 fps at full
speed): sprites instead of per-frame gradients, batched wires and stars, a
cached brain mesh, a frame scheduler that idles and pauses, auto lite.
"""

from __future__ import annotations

import re
from pathlib import Path

PAGE = (Path(__file__).resolve().parents[1] / "src/dariusai/viz/static/index.html").read_text(encoding="utf-8")


def _neural_view() -> str:
    start = PAGE.index("function buildNeuralView(container)")
    return PAGE[start:PAGE.index("// Chat — code blocks in assistant replies", start)]


def test_no_shadow_blur_in_the_render_path():
    # shadowBlur was the single most expensive call on a software canvas,
    # set per edge spark and per electron on every frame.
    code = "\n".join(l for l in _neural_view().splitlines() if not l.strip().startswith("//"))
    assert "shadowBlur" not in code


def test_nodes_and_electrons_are_sprites():
    nv = _neural_view()
    assert "function nodeSprite(color)" in nv and "function electronSprite(color)" in nv
    assert "ctx.drawImage(spr," in nv
    # one sprite per colour: on-screen size is applied when stamping
    assert re.search(r'makeSprite\("n\|" \+ color \+ "\|" \+ DPR', nv)


def test_wires_and_stars_are_batched():
    nv = _neural_view()
    assert "function queueWire(" in nv and "function flushWires()" in nv
    assert "drawLiveWire" not in nv
    assert "function drawStars()" in nv


def test_brain_mesh_is_cached_and_repainted_only_on_change():
    nv = _neural_view()
    assert "var brainCache" in nv
    assert "if (key !== bc.key)" in nv
    assert "BRAIN_LITE = buildBrainMesh(26, 18)" in nv


def test_frame_scheduler_idles_and_pauses():
    nv = _neural_view()
    assert "function targetFps(now)" in nv
    assert "return lite ? 15 : 30;" in nv                       # idle cap
    assert "if (!neuralPrefs.showAnimations) return 0;" in nv    # nothing moves: on demand
    assert "document.hidden || !onScreen" in nv                 # paused when not visible
    assert "IntersectionObserver" in nv
    assert "function kick()" in nv and nv.count("kick();") >= 6  # woken by events and input


def test_auto_lite_and_quality_setting():
    nv = _neural_view()
    assert "function adaptQuality(ms)" in nv and "if (avg > 14)" in nv
    assert 'setQuality: function (v)' in nv
    assert "id='optNeuralQuality'" in PAGE


def test_layout_is_remembered_between_launches():
    nv = _neural_view()
    assert 'var POS_KEY = "dariusai.neural.positions.v1"' in nv
    assert "savePositions()" in nv


def test_graph_poll_is_a_slow_safety_net():
    assert "setInterval(refreshSoon, 60000)" in PAGE
    assert "setInterval(refreshSoon, 10000)" not in PAGE
