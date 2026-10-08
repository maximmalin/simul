# @tscircuit/image-utils

A shim for common image comparison/manipulation libraries. Created
because we wanted to avoid issues with `sharp` and `WASM` bundles. Implements
"transparent API" where you can drop in `"@tscircuit/image-utils/sharp"` where you
use `"sharp"`

Includes the following libraries:

- `sharp`: `@tscircuit/image-utils/sharp`
- `looks-same`: `@tscircuit/image-utils/looks-same`
