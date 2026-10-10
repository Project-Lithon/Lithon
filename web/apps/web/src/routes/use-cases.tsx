import { createFileRoute } from "@tanstack/react-router"

import { Badge } from "@workspace/ui/components/badge"
import { Button } from "@workspace/ui/components/button"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@workspace/ui/components/card"

import { PageHero, SectionHead, WRAP } from "../site"

export const Route = createFileRoute("/use-cases")({ component: UseCases })

const CASES = [
  {
    type: "Case 01",
    tag: "Signal processing",
    title: "When every millisecond has a destination.",
    text: "A typed, low-latency data path can turn a prototype into a dependable edge workload: without adding an interpreter to the critical loop.",
    metric: { value: "< 1ms", label: "target startup overhead" },
  },
  {
    type: "Case 02",
    tag: "Developer tools",
    title: "Build tools that explain themselves.",
    text: "Static flow makes performance decisions visible instead of hiding them behind runtime magic.",
    link: { label: "Try the path", href: "/playground" },
  },
  {
    type: "Case 03",
    tag: "Embedded systems",
    title: "Keep the runtime close.",
    text: "Zero external dependencies make Lithon a useful direction for environments where the toolchain is part of the product.",
    link: { label: "See the evolution", href: "/roadmap" },
  },
] as const

function UseCases() {
  return (
    <>
      <PageHero
        kicker="02 / Use cases & case studies"
        title={
          <>
            Speed where
            <br />
            the work matters.
          </>
        }
        lede="Lithon is for the parts of a system where predictable execution, tiny startup overhead, and control over the machine are worth designing for."
        meta={["Low-latency services", "Native tooling", "Research & learning"]}
      />

      <section className={`${WRAP} py-14`}>
        <SectionHead
          kicker="Case studies"
          title={
            <>
              Small stories.
              <br />
              <span className="text-muted-foreground">Real constraints.</span>
            </>
          }
          lede="These are the kinds of problems Lithon is shaped to meet: systems that need a clear performance story without inheriting a massive toolchain."
        />

        <div className="grid gap-4 md:grid-cols-3">
          {CASES.map((item) => (
            <Card key={item.type}>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <Badge variant="outline">{item.type}</Badge>
                  <Badge variant="secondary">{item.tag}</Badge>
                </div>
                <CardTitle className="mt-3 text-lg font-bold">
                  {item.title}
                </CardTitle>
              </CardHeader>
              <CardContent className="text-muted-foreground">
                {item.text}
                {"metric" in item && (
                  <div className="mt-4 rounded-lg bg-muted p-3">
                    <b className="font-heading text-xl text-accent-strong">
                      {item.metric.value}
                    </b>
                    <span className="block text-xs">{item.metric.label}</span>
                  </div>
                )}
                {"link" in item && (
                  <Button
                    variant="link"
                    size="sm"
                    className="mt-3 px-0"
                    render={<a href={item.link.href} />}
                  >
                    {item.link.label} ↗
                  </Button>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      </section>
    </>
  )
}
