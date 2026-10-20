import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { TextMessage } from "../../app/components/chat/TextMessage";
import type { ChatMessage } from "../../app/types/ws-messages";

function makeMessage(overrides: Partial<ChatMessage> & Pick<ChatMessage, "type" | "content">): ChatMessage {
  return {
    id: "msg-1",
    timestamp: Date.now(),
    ...overrides,
  };
}

describe("TextMessage", () => {
  it("renders user message as plain text, right-aligned", () => {
    const msg = makeMessage({ type: "user", content: "Hello agent" });
    const { container } = render(<TextMessage message={msg} />);

    expect(screen.getByText("Hello agent")).toBeInTheDocument();
    // User bubble should be right-aligned (justify-end on wrapper)
    const wrapper = container.firstElementChild as HTMLElement;
    expect(wrapper.className).toContain("justify-end");
  });

  it("renders AI text message with markdown, left-aligned", () => {
    const msg = makeMessage({ type: "ai_text", content: "**bold text**" });
    const { container } = render(<TextMessage message={msg} />);

    // Markdown should render a <strong> element
    expect(container.querySelector("strong")).toBeInTheDocument();
    expect(container.querySelector("strong")?.textContent).toBe("bold text");
    // AI bubble should be left-aligned (justify-start on wrapper)
    const wrapper = container.firstElementChild as HTMLElement;
    expect(wrapper.className).toContain("justify-start");
  });

  it("renders GFM tables in AI messages", () => {
    const tableMarkdown = "| A | B |\n|---|---|\n| 1 | 2 |";
    const msg = makeMessage({ type: "ai_text", content: tableMarkdown });
    const { container } = render(<TextMessage message={msg} />);

    expect(container.querySelector("table")).toBeInTheDocument();
    expect(container.querySelector("td")?.textContent).toBe("1");
  });

  it("renders multiline user messages with preserved whitespace", () => {
    const msg = makeMessage({ type: "user", content: "line1\nline2" });
    render(<TextMessage message={msg} />);

    expect(screen.getByText("line1 line2")).toBeInTheDocument();
  });
});
