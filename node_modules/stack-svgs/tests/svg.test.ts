import { expect, test, describe } from "bun:test";
import {
  stackSvgsHorizontally,
  stackSvgsVertically,
} from "../lib/index.ts";

const redCircle = `<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg">
  <circle cx="50" cy="50" r="40" stroke="black" stroke-width="3" fill="red" />
</svg>`;

const blueSquare = `<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="10" width="80" height="80" fill="blue" />
</svg>`;

const greenTriangle = `<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg">
  <polygon points="50,10 90,90 10,90" fill="green" />
</svg>`;

const tallRectangle = `<svg width="50" height="150" xmlns="http://www.w3.org/2000/svg">
  <rect x="5" y="5" width="40" height="140" fill="purple" />
</svg>`;

const wideRectangle = `<svg width="200" height="50" xmlns="http://www.w3.org/2000/svg">
  <rect x="5" y="5" width="190" height="40" fill="orange" />
</svg>`;

const svgWithViewBox = `<svg viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg">
  <circle cx="60" cy="60" r="50" fill="yellow" />
</svg>`;

const svgWithViewBoxAndDimensions = `<svg width="80" height="80" viewBox="0 0 160 160" xmlns="http://www.w3.org/2000/svg">
  <circle cx="80" cy="80" r="70" fill="cyan" />
</svg>`;

describe("stackSvgsHorizontally", () => {
  test("stacks two identical circles horizontally", async () => {
    const result = stackSvgsHorizontally([redCircle, redCircle]);
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("stacks two identical circles horizontally with gap", async () => {
    const result = stackSvgsHorizontally([redCircle, redCircle], { gap: 20 });
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("stacks three different shapes horizontally", async () => {
    const result = stackSvgsHorizontally([
      redCircle,
      blueSquare,
      greenTriangle,
    ]);
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("handles empty array", () => {
    const result = stackSvgsHorizontally([]);
    expect(result).toBe('<svg width="0" height="0" viewBox="0 0 0 0" xmlns="http://www.w3.org/2000/svg"></svg>');
  });

  test("handles single SVG", async () => {
    const result = stackSvgsHorizontally([redCircle]);
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("stacks SVGs with viewBox attribute", async () => {
    const result = stackSvgsHorizontally([svgWithViewBox, redCircle]);
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });
});

describe("stackSvgsVertically", () => {
  test("stacks two identical circles vertically", async () => {
    const result = stackSvgsVertically([redCircle, redCircle]);
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("stacks two identical circles vertically with gap", async () => {
    const result = stackSvgsVertically([redCircle, redCircle], { gap: 20 });
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("stacks three different shapes vertically", async () => {
    const result = stackSvgsVertically([redCircle, blueSquare, greenTriangle]);
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("handles empty array", () => {
    const result = stackSvgsVertically([]);
    expect(result).toBe('<svg width="0" height="0" viewBox="0 0 0 0" xmlns="http://www.w3.org/2000/svg"></svg>');
  });

  test("handles single SVG", async () => {
    const result = stackSvgsVertically([redCircle]);
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("stacks SVGs with viewBox attribute", async () => {
    const result = stackSvgsVertically([svgWithViewBox, redCircle]);
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });
});

describe("viewBox handling", () => {
  test("SVG with only viewBox (no width/height)", async () => {
    const result = stackSvgsHorizontally([svgWithViewBox, svgWithViewBox], {
      gap: 10,
    });
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("SVG with both viewBox and dimensions uses width/height for sizing", async () => {
    const result = stackSvgsVertically([
      svgWithViewBoxAndDimensions,
      svgWithViewBoxAndDimensions,
    ]);
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("complex viewBox scenario with offset coordinate system", async () => {
    const svgWithOffset = `<svg viewBox="10 10 100 100" xmlns="http://www.w3.org/2000/svg">
  <circle cx="60" cy="60" r="40" fill="magenta" />
</svg>`;
    const result = stackSvgsHorizontally([svgWithOffset, redCircle], {
      gap: 5,
    });
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });
});

describe("edge cases", () => {
  test("shapes with different heights center vertically", async () => {
    const result = stackSvgsHorizontally([redCircle, tallRectangle]);
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("shapes with different widths center horizontally", async () => {
    const result = stackSvgsVertically([redCircle, wideRectangle]);
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("large gap values", async () => {
    const result = stackSvgsHorizontally([redCircle, blueSquare], {
      gap: 500,
    });
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("many SVGs horizontally", async () => {
    const result = stackSvgsHorizontally(
      [redCircle, blueSquare, greenTriangle, redCircle, blueSquare],
      { gap: 5 }
    );
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("SVG with complex attributes and gradients", async () => {
    const complexSvg = `<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">
  <defs>
    <linearGradient id="grad1" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" style="stop-color:rgb(255,255,0);stop-opacity:1" />
      <stop offset="100%" style="stop-color:rgb(255,0,0);stop-opacity:1" />
    </linearGradient>
  </defs>
  <ellipse cx="50" cy="50" rx="40" ry="30" fill="url(#grad1)" />
</svg>`;
    const result = stackSvgsHorizontally([complexSvg, redCircle]);
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });
});

describe("parser edge cases", () => {
  test("handles single quotes on attributes", async () => {
    const svgWithSingleQuotes = `<svg width='100' height='100' xmlns='http://www.w3.org/2000/svg'>
  <circle cx='50' cy='50' r='40' fill='purple' />
</svg>`;
    const result = stackSvgsHorizontally([svgWithSingleQuotes, redCircle]);
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("handles namespaced attributes", async () => {
    const svgWithNamespace = `<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">
  <defs>
    <circle id="dot" cx="5" cy="5" r="5" fill="blue" />
  </defs>
  <use xlink:href="#dot" x="10" y="10" />
</svg>`;
    const result = stackSvgsVertically([svgWithNamespace, redCircle]);
    expect(result).toContain('xmlns:xlink="http://www.w3.org/1999/xlink"');
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("handles invalid viewBox gracefully", async () => {
    const svgWithInvalidViewBox = `<svg viewBox="abc" xmlns="http://www.w3.org/2000/svg">
  <circle cx="50" cy="50" r="40" fill="teal" />
</svg>`;
    const result = stackSvgsHorizontally([svgWithInvalidViewBox, redCircle]);
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("handles SVG with no width/height/viewBox", async () => {
    const svgNoSize = `<svg xmlns="http://www.w3.org/2000/svg">
  <circle cx="50" cy="50" r="40" fill="gray" />
</svg>`;
    const result = stackSvgsVertically([svgNoSize, redCircle]);
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("handles mixed units in dimensions", async () => {
    const svgWithPx = `<svg width="100px" height="100px" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="10" width="80" height="80" fill="brown" />
</svg>`;
    const result = stackSvgsHorizontally([svgWithPx, redCircle]);
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });
});

describe("ID collision handling", () => {
  test("renames duplicate gradient IDs", async () => {
    const svg1 = `<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="grad1" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" style="stop-color:rgb(255,0,0);stop-opacity:1" />
      <stop offset="100%" style="stop-color:rgb(0,0,255);stop-opacity:1" />
    </linearGradient>
  </defs>
  <rect x="10" y="10" width="80" height="80" fill="url(#grad1)" />
</svg>`;

    const svg2 = `<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="grad1" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" style="stop-color:rgb(0,255,0);stop-opacity:1" />
      <stop offset="100%" style="stop-color:rgb(255,255,0);stop-opacity:1" />
    </linearGradient>
  </defs>
  <circle cx="50" cy="50" r="40" fill="url(#grad1)" />
</svg>`;

    const result = stackSvgsHorizontally([svg1, svg2]);

    // First SVG keeps original ID
    expect(result).toContain('id="grad1"');
    // Second SVG gets renamed ID
    expect(result).toContain('id="grad1__stack1"');
    expect(result).toContain('fill="url(#grad1__stack1)"');

    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("handles clipPath ID collisions", async () => {
    const svg1 = `<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <clipPath id="clip1">
      <rect x="0" y="0" width="50" height="50" />
    </clipPath>
  </defs>
  <circle cx="50" cy="50" r="40" fill="red" clip-path="url(#clip1)" />
</svg>`;

    const svg2 = `<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <clipPath id="clip1">
      <circle cx="50" cy="50" r="30" />
    </clipPath>
  </defs>
  <rect x="10" y="10" width="80" height="80" fill="blue" clip-path="url(#clip1)" />
</svg>`;

    const result = stackSvgsVertically([svg1, svg2]);

    expect(result).toContain('id="clip1__stack1"');
    expect(result).toContain('clip-path="url(#clip1__stack1)"');

    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("handles xlink:href ID references", async () => {
    const svg1 = `<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">
  <defs>
    <circle id="shape1" cx="5" cy="5" r="5" fill="red" />
  </defs>
  <use xlink:href="#shape1" x="20" y="20" />
</svg>`;

    const svg2 = `<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">
  <defs>
    <rect id="shape1" width="10" height="10" fill="blue" />
  </defs>
  <use xlink:href="#shape1" x="30" y="30" />
</svg>`;

    const result = stackSvgsHorizontally([svg1, svg2]);

    expect(result).toContain('xlink:href="#shape1__stack1"');

    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });
});

describe("gap handling", () => {
  test("handles negative gap (overlapping)", async () => {
    const result = stackSvgsHorizontally([redCircle, blueSquare], { gap: -20 });
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("handles zero gap", async () => {
    const result = stackSvgsVertically([redCircle, blueSquare], { gap: 0 });
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("handles very large gap", async () => {
    const result = stackSvgsHorizontally([redCircle, blueSquare], { gap: 1000 });
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });
});

describe("size normalization", () => {
  test("normalizes SVGs with different sizes", async () => {
    const smallCircle = `<svg width="50" height="50" xmlns="http://www.w3.org/2000/svg">
  <circle cx="25" cy="25" r="20" fill="red" />
</svg>`;
    const largeCircle = `<svg width="200" height="200" xmlns="http://www.w3.org/2000/svg">
  <circle cx="100" cy="100" r="80" fill="blue" />
</svg>`;

    const result = stackSvgsHorizontally([smallCircle, largeCircle]);

    // With normalization, both should be approximately the same size
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("can disable size normalization", async () => {
    const smallCircle = `<svg width="50" height="50" xmlns="http://www.w3.org/2000/svg">
  <circle cx="25" cy="25" r="20" fill="red" />
</svg>`;
    const largeCircle = `<svg width="200" height="200" xmlns="http://www.w3.org/2000/svg">
  <circle cx="100" cy="100" r="80" fill="blue" />
</svg>`;

    const result = stackSvgsHorizontally([smallCircle, largeCircle], {
      normalizeSize: false
    });

    // Without normalization, original sizes are preserved
    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });

  test("normalizes with custom target size", async () => {
    const result = stackSvgsVertically([redCircle, tallRectangle], {
      targetSize: 50
    });

    await expect(result).toMatchSvgSnapshot(import.meta.path);
  });
});

describe("error handling", () => {
  test("throws error for invalid SVG", () => {
    expect(() => {
      stackSvgsHorizontally(["not-an-svg"]);
    }).toThrow("Failed to parse SVG at index 0");
  });

  test("throws error for invalid SVG with descriptive message", () => {
    expect(() => {
      stackSvgsVertically(["<div>not svg</div>"]);
    }).toThrow("Invalid SVG string");
  });

  test("throws error for invalid SVG at specific index", () => {
    expect(() => {
      stackSvgsHorizontally([redCircle, "invalid", blueSquare]);
    }).toThrow("Failed to parse SVG at index 1");
  });
});

describe("rootAttributes option", () => {
  test("adds custom root attributes", () => {
    const result = stackSvgsHorizontally([redCircle], {
      rootAttributes: {
        "aria-hidden": "true",
        "role": "img"
      }
    });

    expect(result).toContain('aria-hidden="true"');
    expect(result).toContain('role="img"');
  });

  test("preserves xmlns when adding root attributes", () => {
    const result = stackSvgsVertically([redCircle], {
      rootAttributes: {
        "class": "my-svg"
      }
    });

    expect(result).toContain('xmlns="http://www.w3.org/2000/svg"');
    expect(result).toContain('class="my-svg"');
  });
});
