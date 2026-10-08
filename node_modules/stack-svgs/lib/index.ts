interface StackOptions {
  gap?: number;
  rootAttributes?: Record<string, string>;
  normalizeSize?: boolean; // Default true - normalize by largest dimension
  targetSize?: number; // Target size for largest dimension when normalizing
}

interface ParsedSvg {
  width: number;
  height: number;
  viewBox?: string;
  viewBoxMinX?: number;
  viewBoxMinY?: number;
  viewBoxWidth?: number;
  viewBoxHeight?: number;
  content: string;
  attributes: Record<string, string>;
  xmlnsAttributes: Record<string, string>;
}

function parseSvg(svgString: string): ParsedSvg {
  const svgMatch = svgString.match(/<svg([^>]*)>/);
  if (!svgMatch) {
    throw new Error("Invalid SVG string: No <svg> tag found");
  }

  const attributes: Record<string, string> = {};
  const xmlnsAttributes: Record<string, string> = {};
  const attributeString = svgMatch[1] ?? "";

  // Improved attribute parsing: handles single/double quotes and namespaced attributes
  const attrRegex = /([A-Za-z_:][A-Za-z0-9_.:-]*)=(?:"([^"]*)"|'([^']*)')/g;
  let match: RegExpExecArray | null;
  while ((match = attrRegex.exec(attributeString)) !== null) {
    const attrName = match[1];
    const attrValue = match[2] ?? match[3];

    if (!attrName || attrValue === undefined) continue;

    // Separate xmlns attributes
    if (attrName === 'xmlns' || attrName.startsWith('xmlns:')) {
      xmlnsAttributes[attrName] = attrValue;
    }

    attributes[attrName] = attrValue;
  }

  // Extract dimensions
  let width = 0;
  let height = 0;
  let viewBox: string | undefined;
  let viewBoxMinX: number | undefined;
  let viewBoxMinY: number | undefined;
  let viewBoxWidth: number | undefined;
  let viewBoxHeight: number | undefined;

  // Parse viewBox robustly
  if (attributes.viewBox) {
    viewBox = attributes.viewBox;
    const parts = viewBox
      .trim()
      .split(/[\s,]+/)
      .map((p) => Number.parseFloat(p));

    if (Number.isFinite(parts[0])) viewBoxMinX = parts[0];
    if (Number.isFinite(parts[1])) viewBoxMinY = parts[1];
    if (Number.isFinite(parts[2])) viewBoxWidth = parts[2];
    if (Number.isFinite(parts[3])) viewBoxHeight = parts[3];

    // Fallback: if width/height missing, use viewBox dimensions
    if (!attributes.width && Number.isFinite(viewBoxWidth)) {
      width = viewBoxWidth!;
    }
    if (!attributes.height && Number.isFinite(viewBoxHeight)) {
      height = viewBoxHeight!;
    }
  }

  // Parse explicit width/height (these override viewBox for sizing)
  if (attributes.width) {
    const parsed = Number.parseFloat(attributes.width);
    if (Number.isFinite(parsed)) {
      width = parsed;
    }
  }

  if (attributes.height) {
    const parsed = Number.parseFloat(attributes.height);
    if (Number.isFinite(parsed)) {
      height = parsed;
    }
  }

  // Handle invalid or missing dimensions
  if (!Number.isFinite(width)) width = 0;
  if (!Number.isFinite(height)) height = 0;

  // Extract content (everything between <svg> and </svg>)
  const contentMatch = svgString.match(/<svg[^>]*>([\s\S]*)<\/svg>/);
  const content = contentMatch?.[1]?.trim() ?? "";

  return {
    width,
    height,
    viewBox,
    viewBoxMinX,
    viewBoxMinY,
    viewBoxWidth,
    viewBoxHeight,
    content,
    attributes,
    xmlnsAttributes,
  };
}

// Helper: Build transform that respects viewBox
function buildTransform(svg: ParsedSvg, x: number, y: number): string {
  const transforms: string[] = [];

  // Position the "box" into the stacked layout
  transforms.push(`translate(${x}, ${y})`);

  const hasViewBox =
    svg.viewBoxMinX !== undefined &&
    svg.viewBoxMinY !== undefined &&
    svg.viewBoxWidth &&
    svg.viewBoxHeight;

  if (hasViewBox) {
    const minX = svg.viewBoxMinX!;
    const minY = svg.viewBoxMinY!;

    // width/height as "pixels" of the rendered box
    const widthPx = svg.width || svg.viewBoxWidth!;
    const heightPx = svg.height || svg.viewBoxHeight!;

    const scaleX = widthPx / svg.viewBoxWidth!;
    const scaleY = heightPx / svg.viewBoxHeight!;

    // Replicate viewBox: scale then translate to account for minX/minY
    transforms.push(`scale(${scaleX}, ${scaleY})`);
    transforms.push(`translate(${-minX}, ${-minY})`);
  }

  return transforms.join(" ");
}

// Helper: Rename IDs to avoid collisions
function namespaceIds(content: string, suffix: string): string {
  // Find all id attributes and build a map
  const idMap = new Map<string, string>();
  const idRegex = /\bid="([^"]+)"/g;
  let match: RegExpExecArray | null;

  while ((match = idRegex.exec(content)) !== null) {
    const oldId = match[1];
    if (!oldId) continue;

    const newId = `${oldId}${suffix}`;
    idMap.set(oldId, newId);
  }

  if (idMap.size === 0) {
    return content; // No IDs to rename
  }

  // Replace id attributes
  let result = content.replace(idRegex, (_, id: string) => {
    return `id="${idMap.get(id) || id}"`;
  });

  // Replace references: url(#id)
  result = result.replace(/url\(#([^)]+)\)/g, (match, id) => {
    return idMap.has(id) ? `url(#${idMap.get(id)})` : match;
  });

  // Replace xlink:href="#id" and href="#id"
  result = result.replace(
    /(xlink:href|href)="#([^"]+)"/g,
    (match, attr, id) => {
      return idMap.has(id) ? `${attr}="#${idMap.get(id)}"` : match;
    }
  );

  // Replace style attributes containing url(#id)
  result = result.replace(/style="([^"]*)"/g, (_match, styleContent: string) => {
    const newStyle = styleContent.replace(/url\(#([^)]+)\)/g, (m: string, id: string) => {
      return idMap.has(id) ? `url(#${idMap.get(id)})` : m;
    });
    return `style="${newStyle}"`;
  });

  return result;
}

// Helper: Normalize SVGs to same approximate size
function normalizeSvgSizes(svgs: ParsedSvg[], targetSize?: number): ParsedSvg[] {
  if (svgs.length === 0) return svgs;

  // Find the largest dimension across all SVGs
  let maxDimension = 0;
  for (const svg of svgs) {
    const largest = Math.max(svg.width, svg.height);
    if (largest > maxDimension) {
      maxDimension = largest;
    }
  }

  if (maxDimension === 0) return svgs; // All zero-sized

  // Use targetSize if provided, otherwise use the maxDimension as target
  const target = targetSize ?? maxDimension;

  // Scale each SVG proportionally
  return svgs.map((svg) => {
    const largest = Math.max(svg.width, svg.height);
    if (largest === 0) return svg; // Can't scale zero-sized

    const scale = target / largest;

    return {
      ...svg,
      width: svg.width * scale,
      height: svg.height * scale,
    };
  });
}

// Helper: Merge xmlns attributes from all SVGs
function mergeXmlnsAttributes(svgs: ParsedSvg[]): Record<string, string> {
  const rootXmlns: Record<string, string> = {};

  // Start with default
  rootXmlns.xmlns = "http://www.w3.org/2000/svg";

  // Merge from each svg
  for (const svg of svgs) {
    for (const [key, value] of Object.entries(svg.xmlnsAttributes)) {
      if (!rootXmlns[key]) {
        rootXmlns[key] = value;
      }
    }
  }

  return rootXmlns;
}

// Helper: Serialize attributes to string
function serializeAttributes(attrs: Record<string, string>): string {
  return Object.entries(attrs)
    .map(([k, v]) => `${k}="${v}"`)
    .join(" ");
}

export function stackSvgsHorizontally(
  svgs: string[],
  options: StackOptions = {}
): string {
  // Validate and normalize gap
  let gap = options.gap ?? 0;
  if (!Number.isFinite(gap)) gap = 0;

  const normalizeSize = options.normalizeSize ?? true;

  if (svgs.length === 0) {
    return '<svg width="0" height="0" viewBox="0 0 0 0" xmlns="http://www.w3.org/2000/svg"></svg>';
  }

  // Parse all SVGs with error handling
  const parsedSvgs = svgs.map((svg, index) => {
    try {
      return parseSvg(svg);
    } catch (err) {
      throw new Error(
        `Failed to parse SVG at index ${index}: ${(err as Error).message}`
      );
    }
  });

  // Normalize sizes if requested
  const normalizedSvgs = normalizeSize
    ? normalizeSvgSizes(parsedSvgs, options.targetSize)
    : parsedSvgs;

  // Calculate total width and max height
  const totalWidth =
    normalizedSvgs.reduce((sum, svg) => sum + svg.width, 0) +
    gap * (normalizedSvgs.length - 1);
  const maxHeight = Math.max(...normalizedSvgs.map((svg) => svg.height));

  // Build combined SVG with ID namespacing
  let currentX = 0;
  const groups: string[] = [];

  for (let i = 0; i < normalizedSvgs.length; i++) {
    const svg = normalizedSvgs[i];
    if (!svg) continue;

    const yOffset = (maxHeight - svg.height) / 2; // Center vertically

    // Namespace IDs for all SVGs except the first to avoid collisions
    const content = i === 0 ? svg.content : namespaceIds(svg.content, `__stack${i}`);

    const transform = buildTransform(svg, currentX, yOffset);
    groups.push(
      `  <g transform="${transform}">\n    ${content}\n  </g>`
    );
    currentX += svg.width + gap;
  }

  // Merge xmlns attributes from all SVGs
  const rootXmlns = mergeXmlnsAttributes(normalizedSvgs);

  // Add optional root attributes
  const rootAttrs = {
    width: totalWidth.toString(),
    height: maxHeight.toString(),
    viewBox: `0 0 ${totalWidth} ${maxHeight}`,
    ...rootXmlns,
    ...(options.rootAttributes || {}),
  };

  const attrsString = serializeAttributes(rootAttrs);

  return `<svg ${attrsString}>
${groups.join("\n")}
</svg>`;
}

export function stackSvgsVertically(
  svgs: string[],
  options: StackOptions = {}
): string {
  // Validate and normalize gap
  let gap = options.gap ?? 0;
  if (!Number.isFinite(gap)) gap = 0;

  const normalizeSize = options.normalizeSize ?? true;

  if (svgs.length === 0) {
    return '<svg width="0" height="0" viewBox="0 0 0 0" xmlns="http://www.w3.org/2000/svg"></svg>';
  }

  // Parse all SVGs with error handling
  const parsedSvgs = svgs.map((svg, index) => {
    try {
      return parseSvg(svg);
    } catch (err) {
      throw new Error(
        `Failed to parse SVG at index ${index}: ${(err as Error).message}`
      );
    }
  });

  // Normalize sizes if requested
  const normalizedSvgs = normalizeSize
    ? normalizeSvgSizes(parsedSvgs, options.targetSize)
    : parsedSvgs;

  // Calculate max width and total height
  const maxWidth = Math.max(...normalizedSvgs.map((svg) => svg.width));
  const totalHeight =
    normalizedSvgs.reduce((sum, svg) => sum + svg.height, 0) +
    gap * (normalizedSvgs.length - 1);

  // Build combined SVG with ID namespacing
  let currentY = 0;
  const groups: string[] = [];

  for (let i = 0; i < normalizedSvgs.length; i++) {
    const svg = normalizedSvgs[i];
    if (!svg) continue;

    const xOffset = (maxWidth - svg.width) / 2; // Center horizontally

    // Namespace IDs for all SVGs except the first to avoid collisions
    const content = i === 0 ? svg.content : namespaceIds(svg.content, `__stack${i}`);

    const transform = buildTransform(svg, xOffset, currentY);
    groups.push(
      `  <g transform="${transform}">\n    ${content}\n  </g>`
    );
    currentY += svg.height + gap;
  }

  // Merge xmlns attributes from all SVGs
  const rootXmlns = mergeXmlnsAttributes(normalizedSvgs);

  // Add optional root attributes
  const rootAttrs = {
    width: maxWidth.toString(),
    height: totalHeight.toString(),
    viewBox: `0 0 ${maxWidth} ${totalHeight}`,
    ...rootXmlns,
    ...(options.rootAttributes || {}),
  };

  const attrsString = serializeAttributes(rootAttrs);

  return `<svg ${attrsString}>
${groups.join("\n")}
</svg>`;
}
