import { useEffect, useRef } from 'react';
import { EmptyState } from './EmptyState';
import { renderFormattedText } from '../formatText';
import type { PostMortemChatTurn } from '../types/postmortem';

/**
 * The review conversation.
 *
 * Structurally the same component as Learner Mode's chat, and deliberately so:
 * the two modes should feel like one product, and someone who has asked the
 * coach a question in the sandbox already knows how to ask one here. Same
 * bubbles, same ice for the coach's voice, same amber for pending, same
 * independently scrolling log with the composer pinned under it.
 *
 * What differs is upstream, not here: the server answers this endpoint with a
 * different persona and a structured evidence packet about the move on the
 * board (postmortem_api.chat). The component's only job is to keep the
 * conversation legible while the board stays put.
 */
export function PostMortemChat({
    history,
    pending,
    error,
    draft,
    onDraft,
    onSend,
    contextLabel,
    disabled,
}: {
    history: PostMortemChatTurn[];
    pending: string | null;
    error: string | null;
    draft: string;
    onDraft: (value: string) => void;
    onSend: () => void;
    /** What the next question will be answered about - the move, or the branch. */
    contextLabel: string;
    disabled: boolean;
}) {
    const logRef = useRef<HTMLDivElement>(null);

    // Follow the conversation down as it grows. Scrolls the log, never the
    // page: the board and the controls must not move while you read.
    useEffect(() => {
        const log = logRef.current;
        if (log) {
            log.scrollTop = log.scrollHeight;
        }
    }, [history.length, pending, error]);

    return (
        <div className="pm-chat">
            <div className="pm-chat-log" ref={logRef}>
                {history.length === 0 && !pending && (
                    /* The same EmptyState Learner Mode's chat uses. This panel
                       had its own copy, pinned to the top of the log with ~290px
                       of empty surface under it - which reads as a panel that
                       failed to load, and made the two coaches look like two
                       different features. */
                    <EmptyState title="Ask about this game">
                        The coach can see the position on the board, the move played
                        from it, and what the engine thinks of both. Try
                        {' '}<em>"why was that a mistake?"</em>,
                        {' '}<em>"what should I have been looking for?"</em>, or
                        {' '}<em>"what happens if I play the rook here instead?"</em>
                    </EmptyState>
                )}

                {history.map((turn, index) => (
                    <div
                        key={index}
                        className={`pm-chat-msg ${turn.role === 'user' ? 'pm-chat-user' : 'pm-chat-model'}`}
                    >
                        {turn.role === 'model' ? renderFormattedText(turn.text) : turn.text}
                    </div>
                ))}

                {pending && (
                    <>
                        <div className="pm-chat-msg pm-chat-user">{pending}</div>
                        <div className="pm-chat-msg pm-chat-model is-pending" role="status" aria-live="polite">
                            Coach is reviewing the position…
                        </div>
                    </>
                )}

                {error && <p className="pm-chat-error" role="alert">{error}</p>}
            </div>

            {/* Says what the next question is about. Without it, a question
                asked after branching reads as a question about the game, and
                the answer - correctly about the branch - looks wrong. */}
            <p className="pm-chat-context">{contextLabel}</p>

            <form
                className="pm-chat-row"
                onSubmit={event => {
                    event.preventDefault();
                    onSend();
                }}
            >
                <input
                    className="action-btn pm-chat-input"
                    value={draft}
                    onChange={event => onDraft(event.target.value)}
                    placeholder="Ask about this position..."
                    disabled={disabled}
                    aria-label="Ask the coach about this position"
                />
                <button
                    type="submit"
                    className="action-btn pm-chat-send"
                    disabled={disabled || !draft.trim()}
                >
                    Ask
                </button>
            </form>
        </div>
    );
}
