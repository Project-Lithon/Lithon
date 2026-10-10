import type { ActionEntry } from "../docs/model"

export type ActionHandlers = {
  toggleTheme: () => void
  toggleDirection: () => void
  toggleSound: () => void
  toggleMachineView: () => void
}

/** Every action in the system, tagged so search reaches 100% coverage. */
export function actionEntries(): ActionEntry[] {
  return [
    {
      id: "action:theme",
      title: "Toggle light / dark theme",
      subtitle: "Switches the Lithon palette",
      tags: ["theme", "dark", "light", "appearance", "palette", "toggle"],
      run: "theme",
    },
    {
      id: "action:direction",
      title: "Switch RTL / LTR direction",
      subtitle: "Flips document direction (RTL is the default)",
      tags: ["direction", "rtl", "ltr", "layout", "toggle"],
      run: "direction",
    },
    {
      id: "action:sound",
      title: "Toggle sound feedback",
      subtitle: "UI ticks and confirmations (off by default)",
      tags: ["sound", "audio", "feedback", "mute", "toggle"],
      run: "sound",
    },
    {
      id: "action:machine-view",
      title: "Toggle machine view",
      subtitle: "Open examples on the IR / x86-64 tab",
      tags: ["machine", "view", "ir", "assembly", "x86", "toggle"],
      run: "machine-view",
    },
    {
      id: "action:nav-home",
      title: "Go to Home",
      subtitle: "Landing page",
      tags: ["home", "landing", "navigate", "index"],
      run: "nav:/",
    },
    {
      id: "action:nav-docs",
      title: "Go to Docs",
      subtitle: "Documentation index",
      tags: ["docs", "documentation", "guide", "navigate"],
      run: "nav:/docs",
    },
    {
      id: "action:nav-playground",
      title: "Go to Playground",
      subtitle: "Write and run Lithon in the browser",
      tags: ["playground", "try", "run", "editor", "navigate"],
      run: "nav:/playground",
    },
    {
      id: "action:nav-roadmap",
      title: "Go to Roadmap",
      subtitle: "What is next, by phase",
      tags: ["roadmap", "phases", "future", "navigate"],
      run: "nav:/roadmap",
    },
    {
      id: "action:nav-contributors",
      title: "Go to Contributors",
      subtitle: "Who is building Lithon",
      tags: ["contributors", "people", "community", "navigate"],
      run: "nav:/contributors",
    },
    {
      id: "action:nav-benchmarks",
      title: "Go to Benchmarks",
      subtitle: "Recorded CPython vs Lithon runs, with video",
      tags: ["benchmarks", "performance", "speedup", "videos", "results"],
      run: "nav:/benchmarks",
    },
    {
      id: "action:external-github",
      title: "Open the GitHub repository",
      subtitle: "Source, issues, and the verification suites",
      tags: ["github", "repo", "source", "issues", "external"],
      run: "https://github.com/Project-Lithon/Lithon",
    },
    {
      id: "action:external-actions",
      title: "Open GitHub Actions (CI runs)",
      subtitle: "Every workflow run for this branch",
      tags: ["ci", "cd", "actions", "workflows", "build", "status", "external"],
      run: "https://github.com/Project-Lithon/Lithon/actions",
    },
    {
      id: "action:external-ci",
      title: "Open the CI workflow",
      subtitle: "The full verification gate, run by run",
      tags: ["ci", "test", "ctest", "regression", "fuzz", "external"],
      run: "https://github.com/Project-Lithon/Lithon/actions/workflows/ci.yml",
    },
    {
      id: "action:external-releases",
      title: "Open releases",
      subtitle: "Published builds",
      tags: ["release", "download", "build", "version", "external"],
      run: "https://github.com/Project-Lithon/Lithon/releases",
    },
    {
      id: "action:external-issues",
      title: "Open issues",
      subtitle: "Known gaps and bug reports",
      tags: ["issues", "bugs", "tracker", "external"],
      run: "https://github.com/Project-Lithon/Lithon/issues",
    },
    {
      id: "action:external-discord",
      title: "Open the Discord server",
      subtitle: "Community discussion",
      tags: ["discord", "chat", "community", "external"],
      run: "https://discord.gg/project-lithon",
    },
  ]
}
