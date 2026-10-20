import { describe, it, expect, vi, beforeAll } from 'vitest';
import * as fc from 'fast-check';
import { render, screen, within, fireEvent, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ApprovalGate } from '../../app/components/chat/ApprovalGate';
import { HumanInputPrompt } from '../../app/components/chat/HumanInputPrompt';
import { NotificationMessage } from '../../app/components/chat/NotificationMessage';
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
 * Also collapses consecutive whitespace to a single space, since HTML rendering
 * collapses whitespace and accessible names would not match the raw string.
 */
const visibleTextArbitrary: fc.Arbitrary<string> = fc
  .string({ minLength: 1, maxLength: 80 })
  .map((s: string) =>
    s
      .replace(/[<>&_*`#\[\]\\~|{}()!\-\n\r\t"'+]/g, '')
      .replace(/\s+/g, ' ')
      .trim(),
  )
  .filter((s: string) => s.length > 0);

/** Array of 1-5 visible text option strings */
const optionsArbitrary: fc.Arbitrary<string[]> = fc
  .array(visibleTextArbitrary, { minLength: 1, maxLength: 5 })
  .filter((arr) => {
    // Ensure all options are unique (buttons use option as key)
    const unique = new Set(arr);
    return unique.size === arr.length;
  });

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
// Property 8: User response transmission
// ---------------------------------------------------------------------------

/**
 * Feature: simple-clean-frontend, Property 8: User response transmission
 *
 * For any user-submitted response text (to a human_input or approval gate),
 * the SPA shall send the exact response text over the WebSocket connection
 * as a plain text frame.
 *
 * Validates: Requirements 5.2, 5.5
 */
describe('Property 8: User response transmission', () => {
  it('sends the exact typed text via onSendMessage when submitting a human_input response', async () => {
    /**
     * **Validates: Requirements 5.2**
     */
    // Use a smaller set of simple strings for userEvent typing performance
    const simpleTextArbitrary = fc
      .stringMatching(/^[a-zA-Z0-9 ]{1,30}$/)
      .filter((s: string) => s.trim().length > 0);

    await fc.assert(
      fc.asyncProperty(simpleTextArbitrary, async (responseText) => {
        cleanup();
        const user = userEvent.setup();
        const mockSend = vi.fn();
        const msg = makeChatMessage({
          type: 'human_input',
          content: 'Enter your response',
          metadata: { prompt: 'Enter your response' },
        });

        const { unmount } = render(
          <HumanInputPrompt message={msg} onSendMessage={mockSend} />,
        );

        const input = screen.getByPlaceholderText('Type your response…');
        await user.type(input, responseText);
        await user.click(screen.getByRole('button', { name: 'Send' }));

        // onSendMessage must have been called with the exact trimmed text
        expect(mockSend).toHaveBeenCalledTimes(1);
        expect(mockSend).toHaveBeenCalledWith(responseText.trim());

        unmount();
      }),
      { numRuns: 100 },
    );
  });
});

// ---------------------------------------------------------------------------
// Property 9: Approval gate rendering
// ---------------------------------------------------------------------------

/**
 * Feature: simple-clean-frontend, Property 9: Approval gate rendering
 *
 * For any approval_required message with a stage, summary, and options array,
 * the Chat_Panel shall display the stage name, render the summary, and render
 * each option as a quick-reply button whose label matches the option text.
 *
 * Validates: Requirements 5.3, 5.4
 */
describe('Property 9: Approval gate rendering', () => {
  it('renders stage name, summary, and all option buttons for any approval_required message', () => {
    /**
     * **Validates: Requirements 5.3, 5.4**
     */
    fc.assert(
      fc.property(
        visibleTextArbitrary,
        visibleTextArbitrary,
        optionsArbitrary,
        (stageName, summary, options) => {
          cleanup();
          const mockSend = vi.fn();
          const msg = makeChatMessage({
            type: 'approval_required',
            content: stageName,
            metadata: { stage: stageName, summary, options },
          });

          const { container, unmount } = render(
            <ApprovalGate message={msg} onSendMessage={mockSend} />,
          );

          // Stage name must be visible
          expect(container.textContent).toContain(stageName);

          // Summary text must be visible
          expect(container.textContent).toContain(summary);

          // Each option must appear as a button
          for (const option of options) {
            const button = screen.getByRole('button', { name: option });
            expect(button).toBeTruthy();
            expect(button).not.toBeDisabled();
          }

          unmount();
        },
      ),
      { numRuns: 100 },
    );
  });
});

// ---------------------------------------------------------------------------
// Property 10: Approval button disablement after selection
// ---------------------------------------------------------------------------

/**
 * Feature: simple-clean-frontend, Property 10: Approval button disablement after selection
 *
 * For any approval gate with N options, when the user clicks one option
 * button, all N buttons shall become disabled (preventing duplicate
 * submissions).
 *
 * Validates: Requirements 5.5
 */
describe('Property 10: Approval button disablement after selection', () => {
  it('disables all buttons after clicking any one option', () => {
    /**
     * **Validates: Requirements 5.5**
     */
    fc.assert(
      fc.property(
        optionsArbitrary,
        fc.nat(),
        (options, clickIndexRaw) => {
          cleanup();
          const mockSend = vi.fn();
          const clickIndex = clickIndexRaw % options.length;
          const msg = makeChatMessage({
            type: 'approval_required',
            content: 'Stage',
            metadata: { stage: 'Stage', summary: 'Summary', options },
          });

          const { unmount } = render(
            <ApprovalGate message={msg} onSendMessage={mockSend} />,
          );

          // Click the selected button using fireEvent for speed
          const targetButton = screen.getByRole('button', { name: options[clickIndex] });
          fireEvent.click(targetButton);

          // Verify onSendMessage was called with the clicked option text
          expect(mockSend).toHaveBeenCalledTimes(1);
          expect(mockSend).toHaveBeenCalledWith(options[clickIndex]);

          // ALL buttons must now be disabled
          for (const option of options) {
            const button = screen.getByRole('button', { name: option });
            expect(button).toBeDisabled();
          }

          unmount();
        },
      ),
      { numRuns: 100 },
    );
  });
});

// ---------------------------------------------------------------------------
// Property 11: Notification message rendering without input
// ---------------------------------------------------------------------------

/**
 * Feature: simple-clean-frontend, Property 11: Notification message rendering without input
 *
 * For any human_input_notify message with a message string, the Chat_Panel
 * shall display the message in an informational style and shall not present
 * a text input field.
 *
 * Validates: Requirements 5.6
 */
describe('Property 11: Notification message rendering without input', () => {
  it('renders the notification message text and has no input field', () => {
    /**
     * **Validates: Requirements 5.6**
     */
    fc.assert(
      fc.property(visibleTextArbitrary, (messageText) => {
        cleanup();
        const msg = makeChatMessage({
          type: 'human_input_notify',
          content: messageText,
        });

        const { container, unmount } = render(
          <NotificationMessage message={msg} />,
        );

        // Message text must be visible
        expect(container.textContent).toContain(messageText);

        // No input field should exist
        const inputs = container.querySelectorAll('input, textarea');
        expect(inputs.length).toBe(0);

        unmount();
      }),
      { numRuns: 100 },
    );
  });
});
