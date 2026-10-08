# stack-svgs

Stacking SVGs horizontally or vertically

## Installation

```bash
npm install stack-svgs
# or
bun add stack-svgs
```

## Usage

### Basic Usage

```tsx
import { stackSvgsHorizontally, stackSvgsVertically } from "stack-svgs"

const svg1 = `<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg">
  <circle cx="50" cy="50" r="40" fill="red" />
</svg>`

const svg2 = `<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="10" width="80" height="80" fill="blue" />
</svg>`

// Stack horizontally with a gap
const horizontal = stackSvgsHorizontally([svg1, svg2], { gap: 10 })

// Stack vertically with no gap
const vertical = stackSvgsVertically([svg1, svg2], { gap: 0 })
```

## Options

```typescript
interface StackOptions {
  gap?: number // Gap between SVGs (can be negative for overlap)
  normalizeSize?: boolean // Auto-scale SVGs to same size (default: true)
  targetSize?: number // Target size for largest dimension when normalizing
  rootAttributes?: Record<string, string> // Additional attributes for root <svg>
}
```

### Size Normalization

By default, SVGs are automatically normalized so their largest dimension matches:

```tsx
const smallIcon = `<svg width="50" height="50">...</svg>`
const largeIcon = `<svg width="200" height="200">...</svg>`

// Both will be scaled to 200x200 (matching the largest)
const normalized = stackSvgsHorizontally([smallIcon, largeIcon])

// Disable normalization to preserve original sizes
const original = stackSvgsHorizontally([smallIcon, largeIcon], {
  normalizeSize: false,
})

// Normalize to a specific target size
const custom = stackSvgsHorizontally([smallIcon, largeIcon], {
  targetSize: 100,
})
```

### Gap Control

```tsx
// Positive gap - space between SVGs
stackSvgsHorizontally([svg1, svg2], { gap: 20 })

// Zero gap - SVGs touching
stackSvgsHorizontally([svg1, svg2], { gap: 0 })

// Negative gap - overlapping SVGs
stackSvgsHorizontally([svg1, svg2], { gap: -10 })
```

### Custom Root Attributes

```tsx
const result = stackSvgsHorizontally([svg1, svg2], {
  rootAttributes: {
    "aria-hidden": "true",
    role: "img",
    class: "stacked-icons",
  },
})
```
