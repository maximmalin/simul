# @tscircuit/mm

```ts
import mm from "@tscircuit/mm"

mm("10mm") // 10
mm("10cm") // 100
mm("10m") // 10000
mm("1in") // 25.4
mm("1ft") // 304.8
```

```bash
npm install -g @tscircuit/mm
```

## Extra Features

```ts
import { mmStr } from "@tscircuit/mm"

mmStr(10) // 10mm
mmStr("0.1in") // 2.54mm
```
