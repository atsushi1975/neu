"use strict";

const test = require("node:test");
const assert = require("node:assert");

const { seatPosition, tablePositions } = require("./table-layout.js");

function near(actual, expected, epsilon = 1e-6) {
  assert.ok(
    Math.abs(actual - expected) < epsilon,
    `expected ${expected}, got ${actual}`
  );
}

test("自分は卓の手前（真下）に来る", () => {
  const pos = seatPosition(0, 0, 4, {
    radiusX: 100,
    radiusY: 50,
    centerX: 0,
    centerY: 0,
  });
  near(pos.x, 0);
  near(pos.y, 50);
});

test("次の席は反時計回り側（y 下向きで θ 増加方向）に並ぶ", () => {
  const pos = seatPosition(1, 0, 4, {
    radiusX: 100,
    radiusY: 50,
    centerX: 0,
    centerY: 0,
  });
  // k = (1 - 0) mod 4 = 1, θ = 90 + 90 = 180 -> (x, y) = (-100, 0)
  near(pos.x, -100);
  near(pos.y, 0);
});

test("2人でも自分の手前を軸に並ぶ", () => {
  const pos = seatPosition(1, 0, 2, {
    radiusX: 100,
    radiusY: 50,
    centerX: 0,
    centerY: 0,
  });
  // k = (1 - 0) mod 2 = 1, θ = 90 + 180 = 270 -> (x, y) = (0, -50)
  near(pos.x, 0);
  near(pos.y, -50);
});

test("tablePositions は全席の座標を返す", () => {
  const positions = tablePositions(2, 3, {
    radiusX: 100,
    radiusY: 50,
    centerX: 0,
    centerY: 0,
  });
  assert.equal(positions.length, 3);
  // 自分の席は常に手前（y > 0）
  assert.ok(positions.some((p) => p.y > 0));
});
