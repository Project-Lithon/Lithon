import type { DocPage } from "../model"

import { ENGINE_PAGES } from "./engine"
import { LANGUAGE_PAGES } from "./language"
import { MACHINE_PAGES } from "./machine"
import { REFERENCE_PAGES } from "./reference"
import { START_PAGES } from "./start"
import { DIVERGENCE_PAGES } from "./divergences"
import { EXCEPTION_PAGES } from "./exceptions"
import { MATCH_PAGES } from "./match"
import { NETWORKING_PAGES } from "./networking"
import { STRINGS_PAGES } from "./strings"

export const PAGES: DocPage[] = [
  ...START_PAGES,
  ...LANGUAGE_PAGES,
  ...DIVERGENCE_PAGES,
  ...EXCEPTION_PAGES,
  ...MATCH_PAGES,
  ...STRINGS_PAGES,
  ...NETWORKING_PAGES,
  ...ENGINE_PAGES,
  ...REFERENCE_PAGES,
  ...MACHINE_PAGES,
]
