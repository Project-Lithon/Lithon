import type { DocPage } from "../model"

import { ENGINE_PAGES } from "./engine"
import { LANGUAGE_PAGES } from "./language"
import { MACHINE_PAGES } from "./machine"
import { REFERENCE_PAGES } from "./reference"
import { START_PAGES } from "./start"

export const PAGES: DocPage[] = [
  ...START_PAGES,
  ...LANGUAGE_PAGES,
  ...ENGINE_PAGES,
  ...REFERENCE_PAGES,
  ...MACHINE_PAGES,
]
