import assert from "node:assert/strict";
import test from "node:test";

import { paragraphizeForDisplay } from "../agentsassemble/static/meeting-views.js";

test("display paragraphing keeps decimal and version tokens intact", () => {
  assert.deepEqual(
    paragraphizeForDisplay("Kiro Opus 4.7은 0.5초와 80kg 조건을 말했다. 모르겠다... 다음 문장."),
    ["Kiro Opus 4.7은 0.5초와 80kg 조건을 말했다.", "모르겠다...", "다음 문장."]
  );
});

test("display paragraphing splits long prose at natural boundaries before slicing", () => {
  const result = paragraphizeForDisplay(
    "첫 조건은 앞줄이 무너지지 않는다는 것이다, 둘째 조건은 30명이 동시에 좌우로 붙는다는 것이다, 셋째 조건은 겁먹고 빠지는 사람이 없다는 것이다, 넷째 조건은 고릴라가 지치기 전에 붙잡힌다는 것이다, 다섯째 조건은 0.5초 같은 숫자 표현이 중간에 잘리지 않는다는 것이다."
  );

  assert.ok(result.length > 1);
  assert.ok(result.every((line) => !line.includes("0.\n5")));
  assert.equal(result.join(" "), "첫 조건은 앞줄이 무너지지 않는다는 것이다, 둘째 조건은 30명이 동시에 좌우로 붙는다는 것이다, 셋째 조건은 겁먹고 빠지는 사람이 없다는 것이다, 넷째 조건은 고릴라가 지치기 전에 붙잡힌다는 것이다, 다섯째 조건은 0.5초 같은 숫자 표현이 중간에 잘리지 않는다는 것이다.");
});
