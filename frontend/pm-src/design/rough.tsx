/* Hand-drawn primitives on top of rough.js (vendored, MIT).
   Every shape gets a seed derived from a stable key, so the same data always
   draws exactly the same wobble — no flicker on refresh or re-render.        */
import rough from "./vendor/rough.esm.js";

const generator = rough.generator();

/** Stable 31-bit seed from a string (FNV-1a). */
export function seedOf(key: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < key.length; i += 1) {
    h ^= key.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return ((h >>> 0) % 2147483646) + 1;
}

/** Pen presets — the only stroke styles the charts use. */
export const PEN = {
  ink: { stroke: "#5C5750", strokeWidth: 1.15, roughness: 1.05, bowing: 0.8 },
  inkLight: { stroke: "#A1998E", strokeWidth: 1, roughness: 0.9, bowing: 0.6 },
  clay: { stroke: "#C97C5D", strokeWidth: 1.4, roughness: 1.1, bowing: 0.8 },
  sage: { stroke: "#8C8A7E", strokeWidth: 1.1, roughness: 1, bowing: 0.7 },
} as const;

/** Hachure fills: progress (sage) and delay (clay). */
export const HATCH = {
  progress: { fill: "#8C8A7E", fillStyle: "hachure", hachureAngle: -41, hachureGap: 5, fillWeight: 1 },
  delay: { fill: "#C97C5D", fillStyle: "hachure", hachureAngle: -41, hachureGap: 4.5, fillWeight: 1.1 },
  soft: { fill: "#C9BFB0", fillStyle: "hachure", hachureAngle: -41, hachureGap: 6, fillWeight: 0.8 },
} as const;

type Opts = Record<string, unknown>;
interface PathInfo { d: string; stroke: string; strokeWidth: number; fill?: string }

function toPaths(drawable: unknown): PathInfo[] {
  return generator.toPaths(drawable) as PathInfo[];
}

const BASE: Opts = { disableMultiStroke: true, disableMultiStrokeFill: true, preserveVertices: false };

export function roughRect(key: string, x: number, y: number, w: number, h: number, opts: Opts): PathInfo[] {
  if (!(w > 0.5) || !(h > 0.5)) return [];
  return toPaths(generator.rectangle(x, y, w, h, { ...BASE, ...opts, seed: seedOf(key) }));
}

export function roughLine(key: string, x1: number, y1: number, x2: number, y2: number, opts: Opts): PathInfo[] {
  return toPaths(generator.line(x1, y1, x2, y2, { ...BASE, ...opts, seed: seedOf(key) }));
}

export function roughLinear(key: string, points: [number, number][], opts: Opts): PathInfo[] {
  if (points.length < 2) return [];
  return toPaths(generator.linearPath(points, { ...BASE, ...opts, seed: seedOf(key) }));
}

export function roughCircle(key: string, cx: number, cy: number, d: number, opts: Opts): PathInfo[] {
  return toPaths(generator.circle(cx, cy, d, { ...BASE, ...opts, seed: seedOf(key) }));
}

/** Render rough paths as SVG <path>s. `dash` applies to strokes only. */
export function Ink({ paths, dash, className }: { paths: PathInfo[]; dash?: string; className?: string }) {
  return (
    <g className={className}>
      {paths.map((p, i) => (
        <path
          key={i}
          d={p.d}
          stroke={p.stroke === "none" ? undefined : p.stroke}
          strokeWidth={p.strokeWidth}
          fill={p.fill && p.fill !== "none" ? p.fill : "none"}
          strokeDasharray={dash && (!p.fill || p.fill === "none") ? dash : undefined}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ))}
    </g>
  );
}
