/* Minimal typing for the vendored rough.js 4.6.6 ESM bundle (MIT). Only the generator API is used. */
interface RoughGenerator {
  rectangle(x: number, y: number, w: number, h: number, o?: Record<string, unknown>): unknown;
  line(x1: number, y1: number, x2: number, y2: number, o?: Record<string, unknown>): unknown;
  linearPath(points: [number, number][], o?: Record<string, unknown>): unknown;
  circle(x: number, y: number, d: number, o?: Record<string, unknown>): unknown;
  toPaths(drawable: unknown): { d: string; stroke: string; strokeWidth: number; fill?: string }[];
}
declare const rough: { generator(config?: Record<string, unknown>): RoughGenerator };
export default rough;
