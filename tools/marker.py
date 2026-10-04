import math, random, sys
def circle(cx, cy, rx, ry, seed=1, turns=1.18, start=-2.4):
    r = random.Random(seed); pts = []
    n = 46
    for i in range(n + 1):
        t = start + 2 * math.pi * turns * i / n
        k = 1 + 0.035 * math.sin(3 * t + seed) + r.uniform(-0.012, 0.012) + 0.05 * i / n
        pts.append((cx + rx * k * math.cos(t), cy + ry * k * math.sin(t)))
    return "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)
def box(x, y, w, h, seed=2):
    r = random.Random(seed); j = lambda: r.uniform(-4, 4)
    p = [(x + j(), y + j()), (x + w + j(), y + j()), (x + w + j(), y + h + j()), (x + j(), y + h + j()), (x + 14 + j(), y - 6 + j())]
    return "M" + " L".join(f"{a:.1f} {b:.1f}" for a, b in p)
def squiggle(x, y, w, seed=3, amp=7):
    r = random.Random(seed); n = 18
    return "M" + " L".join(f"{x + w * i / n:.1f} {y + amp * math.sin(i * 1.7) + r.uniform(-2, 2):.1f}" for i in range(n + 1))
if __name__ == "__main__":
    kind, *a = sys.argv[1:]
    a = [float(v) for v in a]
    print({"circle": circle, "box": box, "squiggle": squiggle}[kind](*a[:4]) if kind != "circle" else circle(*a))
