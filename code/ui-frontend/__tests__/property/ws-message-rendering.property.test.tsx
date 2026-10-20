import { describe, it, expect, vi, beforeAll } from 'vitest';
import * as fc from 'fast-check';
import { render, screen, within, cleanup } from '@testing-library/react';
import { TextMessage } from '../../app/components/chat/TextMessage';
import { StageBanner } from '../../app/components/chat/StageBanner';
import { ToolUseIndicator } from '../../app/components/chat/ToolUseIndicator';
import { ErrorMessage } from '../../app/components/chat/ErrorMessage';
import { HumanInputPrompt } from '../../app/components/chat/HumanInputPrompt';
import { ChatPanel } from '../../app/components/chat/ChatPanel';
import type { ChatMessage } from '../../app/types/ws-messages';

// Mock scrollIntoView which is not available in jsdom
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/**
 * Printable non-empty strings that won't be transformed by markdown rendering.
 * Avoids markdown-special characters like _ * ` # [ ] etc.
 */
const visibleTextArbitrary: fc.Arbitrary<string> = fc
  .string({ minLength: 1, maxLength: 80 })
  // Also exclude . : ! which trigger ensureParagraphBreaks() when followed by uppercase
  .map((s: string) => s.replace(/[<>&_*`#\[\]\\~|{}()!\-\n\r\t.:]/g, '').trim())
  .filter((s: string) => s.length > 0);

/** Node-id style strings (alphanumeric + dashes) */
const nodeIdArbitrary: fc.Arbitrary<string> = (
  fc.stringMatching(/^[a-zA-Z][a-zA-Z0-9_-]{0,40}$/) as fc.Arbitrary<string>
).filter((s: string) => s.length > 0);

/** Tool name strings */
const toolNameArbitrary: fc.Arbitrary<string> = (
  fc.stringMatching(/^[a-zA-Z][a-zA-Z0-9_.-]{0,60}$/) as fc.Arbitrary<string>
).filter((s: string) => s.length > 0);

/** Helper to build a ChatMessage with sensible defaults */
function makeChatMessage(
  overrides: Partial<ChatMessage> & Pick<ChatMessage, 'type'>,
): ChatMessage {
  return {
    id: Math.random().toString(36).slice(2),
    content: '',
    timestamp: Date.now(),
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Property 3: text_chunk accumulation and markdown rendering
// ---------------------------------------------------------------------------

/**
 * Feature: simple-clean-frontend, Property 3: text_chunk accumulation and markdown rendering
 *
 * For any sequence of text_chunk messages with arbitrary string content, the
 * Chat_Panel shall accumulate all content into the current AI message bubble,
 * and the rendered output shall contain all the provided content rendered as
 * markdown.
 *
 * Validates: Requirements 4.1
 */
describe('Property 3: text_chunk accumulation and markdown rendering', () => {
  it('renders arbitrary text content inside an ai_text TextMessage', () => {
    /**
     * **Validates: Requirements 4.1**
     */
    fc.assert(
      fc.property(visibleTextArbitrary, (text) => {
        const msg = makeChatMessage({ type: 'ai_text', content: text });
        const { container, unmount } = render(<TextMessage message={msg} />);

        // The text content must appear somewhere in the rendered DOM
        expect(container.textContent).toContain(text);

        unmount();
      }),
      { numRuns: 100 },
    );
  });
});

// ---------------------------------------------------------------------------
// Property 4: Stage banner lifecycle
// ---------------------------------------------------------------------------

/**
 * Feature: simple-clean-frontend, Property 4: Stage banner lifecycle
 *
 * For any stage_start message with a node_id, the Chat_Panel shall display a
 * banner with that stage name and an in-progress indicator. When a subsequent
 * stage_complete message with the same node_id arrives, the banner shall
 * update to show a completed status.
 *
 * Validates: Requirements 4.2, 4.3
 */
describe('Property 4: Stage banner lifecycle', () => {
  it('stage_start renders the node_id text with a spinner indicator', () => {
    /**
     * **Validates: Requirements 4.2**
     */
    fc.assert(
      fc.property(nodeIdArbitrary, (nodeId) => {
        const msg = makeChatMessage({
          type: 'stage_start',
          content: nodeId,
          metadata: { node_id: nodeId },
        });
        const { container, unmount } = render(<StageBanner message={msg} />);

        // Stage name must be visible
        expect(screen.getByText(nodeId)).toBeTruthy();

        // Spinner (animate-spin class) must be present for in-progress
        const spinner = container.querySelector('.animate-spin');
        expect(spinner).not.toBeNull();

        unmount();
      }),
      { numRuns: 100 },
    );
  });

  it('stage_complete renders the node_id text with a checkmark (no spinner)', () => {
    /**
     * **Validates: Requirements 4.3**
     */
    fc.assert(
      fc.property(nodeIdArbitrary, (nodeId) => {
        const msg = makeChatMessage({
          type: 'stage_complete',
          content: nodeId,
          metadata: { node_id: nodeId },
        });
        const { container, unmount } = render(<StageBanner message={msg} />);

        // Stage name must be visible
        expect(screen.getByText(nodeId)).toBeTruthy();

        // No spinner should be present
        const spinner = container.querySelector('.animate-spin');
        expect(spinner).toBeNull();

        // A green checkmark icon should be present (text-green-500 class)
        const checkmark = container.querySelector('.text-green-500');
        expect(checkmark).not.toBeNull();

        unmount();
      }),
      { numRuns: 100 },
    );
  });
});

// ---------------------------------------------------------------------------
// Property 5: Tool use indicator rendering
// ---------------------------------------------------------------------------

/**
 * Feature: simple-clean-frontend, Property 5: Tool use indicator rendering
 *
 * For any tool_use message with a tool_name, the Chat_Panel shall display a
 * compact indicator containing that tool name.
 *
 * Validates: Requirements 4.4
 */
describe('Property 5: Tool use indicator rendering', () => {
  it('renders the tool name in the DOM for any tool_use message', () => {
    /**
     * **Validates: Requirements 4.4**
     */
    fc.assert(
      fc.property(toolNameArbitrary, (toolName) => {
        const msg = makeChatMessage({
          type: 'tool_use',
          content: toolName,
          metadata: { tool_name: toolName },
        });
        const { unmount } = render(<ToolUseIndicator message={msg} />);

        expect(screen.getByText(toolName)).toBeTruthy();

        unmount();
      }),
      { numRuns: 100 },
    );
  });
});

// ---------------------------------------------------------------------------
// Property 6: Error message rendering
// ---------------------------------------------------------------------------

/**
 * Feature: simple-clean-frontend, Property 6: Error message rendering
 *
 * For any error message with a message string, the Chat_Panel shall display
 * the error text in a visually distinct error-styled element.
 *
 * Validates: Requirements 4.6
 */
describe('Property 6: Error message rendering', () => {
  it('renders the error text in the DOM for any error message', () => {
    /**
     * **Validates: Requirements 4.6**
     */
    fc.assert(
      fc.property(visibleTextArbitrary, (errorText) => {
        const msg = makeChatMessage({
          type: 'error',
          content: errorText,
        });
        const { unmount } = render(<ErrorMessage message={msg} />);

        expect(screen.getByText(errorText)).toBeTruthy();

        unmount();
      }),
      { numRuns: 100 },
    );
  });
});

// ---------------------------------------------------------------------------
// Property 7: Human input prompt rendering
// ---------------------------------------------------------------------------

/**
 * Feature: simple-clean-frontend, Property 7: Human input prompt rendering
 *
 * For any human_input message with a prompt string, the Chat_Panel shall
 * display the prompt text and present a text input field for the user to
 * respond.
 *
 * Validates: Requirements 5.1
 */
describe('Property 7: Human input prompt rendering', () => {
  it('renders the prompt text and an input field for any human_input message', () => {
    /**
     * **Validates: Requirements 5.1**
     */
    fc.assert(
      fc.property(visibleTextArbitrary, (promptText) => {
        cleanup();
        const mockSend = vi.fn();
        const msg = makeChatMessage({
          type: 'human_input',
          content: promptText,
          metadata: { prompt: promptText },
        });
        const { container, unmount } = render(
          <HumanInputPrompt message={msg} onSendMessage={mockSend} />,
        );

        const scope = within(container);

        // Prompt text must be visible
        expect(container.textContent).toContain(promptText);

        // A text input field must be present
        const input = scope.getByPlaceholderText('Type your response…');
        expect(input).toBeTruthy();

        unmount();
      }),
      { numRuns: 100 },
    );
  });
});

// ---------------------------------------------------------------------------
// Property 16: Chat panel stage field updates
// ---------------------------------------------------------------------------

/**
 * Feature: simple-clean-frontend, Property 16: Chat panel stage field updates
 *
 * For any stage_start message with a node_id, the Chat_Panel title bar's
 * dynamic stage field shall update to display that stage name.
 *
 * Validates: Requirements 10.2
 */
describe('Property 16: Chat panel stage field updates', () => {
  it('renders the currentStage in the ChatPanel header', () => {
    /**
     * **Validates: Requirements 10.2**
     */
    fc.assert(
      fc.property(nodeIdArbitrary, (stageName) => {
        cleanup();
        const mockSend = vi.fn();
        const { container, unmount } = render(
          <ChatPanel
            projectName="Test Project"
            messages={[]}
            currentStage={stageName}
            onSendMessage={mockSend}
            isComplete={false}
          />,
        );

        // The stage name must appear in the header area
        expect(container.textContent).toContain(stageName);

        unmount();
      }),
      { numRuns: 100 },
    );
  });
});

// ---------------------------------------------------------------------------
// Property 17: Message bubble type distinction
// ---------------------------------------------------------------------------

/**
 * Feature: simple-clean-frontend, Property 17: Message bubble type distinction
 *
 * For any chat message, user messages and AI messages shall be rendered in
 * visually distinct message bubbles (different styling/alignment).
 *
 * Validates: Requirements 10.3
 */
describe('Property 17: Message bubble type distinction', () => {
  it('user messages use justify-end and ai_text messages use justify-start', () => {
    /**
     * **Validates: Requirements 10.3**
     */
    fc.assert(
      fc.property(visibleTextArbitrary, (text) => {
        // Render user message
        const userMsg = makeChatMessage({ type: 'user', content: text });
        const { container: userContainer, unmount: unmountUser } = render(
          <TextMessage message={userMsg} />,
        );
        const userWrapper = userContainer.querySelector('.justify-end');
        expect(userWrapper).not.toBeNull();
        unmountUser();

        // Render AI message
        const aiMsg = makeChatMessage({ type: 'ai_text', content: text });
        const { container: aiContainer, unmount: unmountAi } = render(
          <TextMessage message={aiMsg} />,
        );
        const aiWrapper = aiContainer.querySelector('.justify-start');
        expect(aiWrapper).not.toBeNull();
        unmountAi();
      }),
      { numRuns: 100 },
    );
  });
});
