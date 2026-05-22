export interface SparklinePolylineResult {
  /** Space-separated `x,y` pairs for SVG `polyline.points`. */
  pointsAttr: string;
  min: number;
  max: number;
}

/** Builds a simple sparkline in a 100×32 viewBox from numeric ladder totals (integers). */
export function calculate_sparkline_polyline_points(values: readonly number[]): SparklinePolylineResult {
  if (values.length === 0) {
    return { pointsAttr: "", min: 0, max: 0 };
  }
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const width = 100;
  const height = 32;
  const pts = values.map((v, index) => {
    const x = values.length === 1 ? width / 2 : (index / (values.length - 1)) * width;
    const y = height - ((v - min) / (max - min)) * height;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return { pointsAttr: pts.join(" "), min, max };
}
