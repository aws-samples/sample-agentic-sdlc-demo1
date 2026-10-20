/* nosemgrep: jsx-not-internationalized — internal demo app, i18n not required */
import { useEffect, useRef, useState } from "react";
import DOMPurify from "dompurify";

interface MermaidDiagramProps {
  chart: string;
}

export function MermaidDiagram({ chart }: MermaidDiagramProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    let cancelled = false;

    async function renderChart() {
      setStatus("loading");
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          theme: "default",
          // Render with 'loose' so Mermaid emits the FULL SVG (arrowhead
          // <marker> defs, edge <style>, etc.). Mermaid's own 'strict' mode
          // runs the SVG through DOMPurify with a config that strips those
          // markers/styles — which removed the connecting arrows. We sanitize
          // the output ourselves below with an SVG-aware profile instead.
          securityLevel: "loose",
          fontFamily: "inherit",
        });

        const id = `mermaid-${Date.now()}`;
        const { svg: rendered } = await mermaid.render(id, chart);

        // Sanitize and return a DOM fragment (not an HTML string), then attach
        // it via the DOM API (replaceChildren) — we deliberately avoid React's
        // raw-HTML injection prop. The svg/svgFilters profiles keep legitimate
        // diagram elements (paths, markers, <style>) while DOMPurify strips
        // <script>, on* event handlers, and javascript: URLs, neutralizing any
        // injection vector.
        const fragment = DOMPurify.sanitize(rendered, {
          USE_PROFILES: { svg: true, svgFilters: true },
          RETURN_DOM_FRAGMENT: true,
        }) as unknown as DocumentFragment;

        if (cancelled) return;
        const host = containerRef.current;
        if (host) {
          host.replaceChildren(fragment);
        }
        setStatus("ready");
      } catch (err) {
        if (!cancelled) {
          console.error("Mermaid render failed:", err);
          if (containerRef.current) containerRef.current.replaceChildren();
          setStatus("error");
        }
      }
    }

    renderChart();
    return () => {
      cancelled = true;
    };
  }, [chart]);

  return (
    <div className="mmd-diagram my-4 flex flex-col items-center overflow-x-auto [&_svg]:max-w-full">
      {/* Force edge/arrow visibility. Mermaid colors node boxes via inline
          `style X fill:...` statements (so boxes always show), but edge
          connectors get their stroke from Mermaid's injected <style> block,
          which can fail to apply once the SVG is re-parented — these scoped
          !important rules guarantee the links and arrowheads are drawn. */}
      <style>{MERMAID_EDGE_CSS}</style>

      {status === "loading" && (
        <div className="py-8 text-gray-400 text-sm">Rendering diagram…</div>
      )}
      {status === "error" && (
        <pre className="w-full whitespace-pre-wrap rounded-md bg-gray-50 p-4 text-sm text-gray-800 dark:bg-gray-900 dark:text-gray-200">
          {chart}
        </pre>
      )}

      {/* Sanitized Mermaid SVG is attached here via DOM APIs (replaceChildren),
          NOT via React's raw-HTML injection prop. */}
      <div ref={containerRef} className="flex justify-center" />
    </div>
  );
}

// Scoped under `.mmd-diagram` so it only affects our rendered Mermaid SVGs.
const MERMAID_EDGE_CSS = `
.mmd-diagram svg .edgePaths path,
.mmd-diagram svg .edgePath path,
.mmd-diagram svg path.flowchart-link,
.mmd-diagram svg .flowchart-link,
.mmd-diagram svg .relation {
  stroke: #2563eb !important;
  fill: none !important;
  stroke-width: 2.75px !important;
}
.mmd-diagram svg marker path,
.mmd-diagram svg .arrowheadPath,
.mmd-diagram svg .marker {
  fill: #2563eb !important;
  stroke: #2563eb !important;
}
`;
