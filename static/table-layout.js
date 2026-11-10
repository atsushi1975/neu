/**
 * 円形卓の席位置計算（純粋関数）。
 *
 * 自分は卓の一番下（手前）。他のプレイヤーは、座席順（席番号の増加 = 時計回り）で
 * 画面上の時計回りに人数分等間隔に並べる。
 *
 * - k = (席 - 自席) mod 人数
 * - θ = 90° + k * (360° / 人数)（画面は y が下向きなので、θ が増える方向が画面の時計回り）
 * - x = cx + rx * cos(θ), y = cy + ry * sin(θ)
 */

function seatPosition(seat, mySeat, seatCount, options) {
  const opts = options || {};
  const n = Number(seatCount) || 0;
  const me = Number(mySeat) || 0;
  const k = (((Number(seat) - me) % n) + n) % n;
  const theta = ((90 + (k * 360) / n) * Math.PI) / 180;
  const radiusX = opts.radiusX || 0;
  const radiusY = opts.radiusY || 0;
  const centerX = opts.centerX || 0;
  const centerY = opts.centerY || 0;
  return {
    x: centerX + radiusX * Math.cos(theta),
    y: centerY + radiusY * Math.sin(theta),
  };
}

function tablePositions(mySeat, seatCount, options) {
  const positions = [];
  for (let seat = 0; seat < (Number(seatCount) || 0); seat += 1) {
    positions.push(
      seatPosition(seat, mySeat, seatCount, options)
    );
  }
  return positions;
}

const TableLayout = { seatPosition, tablePositions };

if (typeof globalThis !== "undefined") {
  globalThis.TableLayout = TableLayout;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = TableLayout;
}
